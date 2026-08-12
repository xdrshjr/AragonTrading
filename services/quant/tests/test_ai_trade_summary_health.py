"""滚动摘要健康度（ai-trade-tradability-and-open-funnel 轴 D · G-14 / G-15）。

D-3（journal 无界增长的失败螺旋）与 D-4（诊断串恒为空）的回归门禁。
"""
from __future__ import annotations

import asyncio

from app.ai_auto.decider import AiTradeDecider
from app.ai_auto.summary_health import (
    SummaryHealth,
    describe_exc,
    health_wire,
    is_stalled,
    next_backoff,
)
from app.config import Settings


# ---------- G-15：诊断串永不为空（D-4）----------
def test_timeout_error_yields_a_non_empty_diagnostic():
    """`str(asyncio.TimeoutError()) == ""` —— 合入前那 4 条空错误串就是这么来的。"""
    assert str(asyncio.TimeoutError()) == ""
    text = describe_exc(asyncio.TimeoutError(), timeout_s=30.0)
    assert text and "TimeoutError" in text and "30" in text


def test_non_empty_exception_keeps_its_message():
    assert "LLM_ERROR" in describe_exc(RuntimeError("LLM_ERROR"))
    assert describe_exc(ValueError()) == "ValueError"


# ---------- 退避与停摆判据 ----------
def test_backoff_grows_exponentially_and_is_capped():
    assert next_backoff(0, 10) == 0
    assert next_backoff(1, 10) == 10
    assert next_backoff(2, 10) == 20
    assert next_backoff(4, 10) == 80
    assert next_backoff(9, 10) == 80           # cap=8 ⇒ 最多 80 轮，不会永久静默
    assert next_backoff(3, 0) == 0


def test_consecutive_failures_reset_on_success_but_total_does_not():
    """只增不减的计数器不叫「连续失败」——两个口径必须分开。"""
    health = SummaryHealth()
    health.note_failure(RuntimeError("a"), refresh_rounds=10)
    health.note_failure(RuntimeError("b"), refresh_rounds=10)
    assert health.failures == 2 and health.total_failures == 2
    health.note_success()
    assert health.failures == 0 and health.total_failures == 2
    assert health.backoff_rounds == 0 and health.last_error is None


def test_stalled_needs_the_configured_streak():
    health = SummaryHealth(failures=2)
    assert is_stalled(health, 3) is False
    health.failures = 3
    assert is_stalled(health, 3) is True
    assert is_stalled(health, 0) is False       # 阈 0 ⇒ 该判据关闭


def test_health_wire_shape():
    wire = health_wire(SummaryHealth(failures=3, total_failures=7, last_error="TimeoutError(30s)",
                                     backoff_rounds=40),
                       rounds_since_summary=4, refresh_rounds=10, max_failures=3)
    assert wire["stalled"] is True
    assert wire["failures"] == 3 and wire["totalFailures"] == 7
    assert wire["backoffRounds"] == 40 and wire["roundsSinceSummary"] == 4
    assert health_wire(None, rounds_since_summary=0, refresh_rounds=10, max_failures=3) is None


# ---------- G-14：journal 有界（D-3 的回归门禁）----------
class _FailingLlm:
    """摘要恒失败的 LLM 桩（`stream` 直接抛）。"""

    async def stream(self, **_kw):
        raise RuntimeError("LLM_ERROR")
        yield  # pragma: no cover —— 使本函数成为 async generator


def _decider(**over) -> AiTradeDecider:
    settings = Settings(**over)
    return AiTradeDecider(_FailingLlm(), "sys", context_refresh_rounds=10, settings=settings)


async def test_journal_stays_bounded_across_consecutive_failures():
    """连续 3 次失败后 journal 必须 ≤ `mult × refresh_rounds`。

    合入前 `_journal` 是一个**无 maxlen 的 list** 且只在成功分支清空：每失败一次，下一次摘要的
    素材就多 `context_refresh_rounds` 轮 ⇒ prompt 单调变长 ⇒ 更容易再超时（越失败越容易失败）。
    """
    decider = _decider(ai_trade_summary_journal_cap_mult=2)
    decider._journal = [{"round": i} for i in range(500)]
    for _ in range(3):
        assert await decider.summarize() is None
    assert len(decider._journal) <= 20
    assert decider._summary_health.failures == 3
    assert decider._summary_health.last_error and "LLM_ERROR" in decider._summary_health.last_error


async def test_backoff_raises_the_next_due_threshold():
    """一次失败后到期门槛抬到 `refresh + backoff`——不再每 10 轮就白烧一次超时预算。"""
    decider = _decider()
    await decider.summarize()
    assert decider._summary_skip_rounds == 10
    assert decider._summary_health.backoff_rounds == 10


async def test_empty_summary_is_treated_as_a_failure_not_a_success():
    """空产出同样进失败分支：否则 journal 会被清空而摘要其实没生成（记忆静默丢失）。"""

    class _EmptyLlm:
        async def stream(self, **_kw):
            return
            yield  # pragma: no cover

    decider = AiTradeDecider(_EmptyLlm(), "sys", context_refresh_rounds=10, settings=Settings())
    decider._journal = [{"round": i} for i in range(50)]
    assert await decider.summarize() is None
    assert decider._summary_health.failures == 1
    assert len(decider._journal) == 20
