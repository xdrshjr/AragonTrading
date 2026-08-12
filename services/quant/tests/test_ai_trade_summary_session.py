"""摘要不再阻塞决策事件（轴 D · D-5 / G-16 / G-17 的后端半边）。

合入前 `commit_round` 里的摘要 await 排在 `_emit_decision` **之前**，而摘要走的是决策的 60s 超时
预算 ⇒「成交已经发生、决策已经完成」之后，界面上的决策事件还要再等最多一分钟才出现。现象是
**成交先到、解释后到**（成交事件走 `_dispatch`，不受影响）。
"""
from __future__ import annotations

import asyncio

from app.ai_auto.decider import AiTradeDecider
from app.ai_auto.trace import maybe_emit_summary_failed
from app.config import Settings


class _SlowLlm:
    """摘要挂起 `delay` 秒后才产出（模拟一次慢摘要）。"""

    def __init__(self, delay: float = 0.05):
        self.delay = delay
        self.calls = 0

    async def stream(self, **_kw):
        self.calls += 1
        await asyncio.sleep(self.delay)
        yield type("Ev", (), {"kind": "text_delta", "text": "SUMMARY"})()
        yield type("Ev", (), {"kind": "message_done"})()


def _decider(llm) -> AiTradeDecider:
    return AiTradeDecider(llm, "sys", context_refresh_rounds=1, settings=Settings(_env_file=None))


def _payload(round_no: int) -> dict:
    return {"round": round_no, "signalBoard": [], "sessionPnlUsd": 0.0}


class _D:
    """最小 decision 替身（`commit_round` 只读这几个字段）。"""

    action = "hold"
    size_fraction = None
    confidence = None
    reason = "r"
    ord_type = "market"
    limit_px = None
    hold_exit_px = None


# ---------- G-16：决策事件先于摘要事件推出 ----------
async def test_deferred_summary_does_not_block_the_commit_call():
    """`defer_summary=True` ⇒ `commit_round` **立刻**返回 None，一次 LLM 都没调。"""
    llm = _SlowLlm(delay=0.2)
    decider = _decider(llm)
    started = asyncio.get_running_loop().time()
    result = await decider.commit_round(_payload(1), _D(), {"ok": True}, defer_summary=True)
    elapsed = asyncio.get_running_loop().time() - started
    assert result is None
    assert llm.calls == 0                     # 决策事件推出之前，摘要一次都没跑
    assert elapsed < 0.1                      # 没有被那 0.2s 的摘要拖住
    assert decider._summary_due is True


async def test_flush_summary_runs_it_afterwards_exactly_once():
    llm = _SlowLlm(delay=0.0)
    decider = _decider(llm)
    await decider.commit_round(_payload(1), _D(), {"ok": True}, defer_summary=True)
    assert await decider.flush_summary() == "SUMMARY"
    assert llm.calls == 1
    assert decider._summary_due is False
    assert await decider.flush_summary() is None       # 不欠着 ⇒ 直接 None，不重复调
    assert llm.calls == 1


async def test_default_is_the_verbatim_pre_feature_path():
    """`defer_summary` 默认 **False** ⇒ 回测 / 单测的既有调用逐字回退（摘要仍在 commit 里跑）。"""
    llm = _SlowLlm(delay=0.0)
    decider = _decider(llm)
    assert await decider.commit_round(_payload(1), _D(), {"ok": True}) == "SUMMARY"
    assert llm.calls == 1
    assert decider._summary_due is False


async def test_a_failed_deferred_summary_is_not_retried_on_the_next_round():
    """`_summary_due` 必须在成功与失败两条分支都归位之后才复位——提前复位会让一次失败之后的
    下一轮误以为还欠着一次摘要而立刻重试，那正是 D-3 螺旋的另一种写法。"""

    class _Fail:
        calls = 0

        async def stream(self, **_kw):
            _Fail.calls += 1
            raise RuntimeError("LLM_ERROR")
            yield  # pragma: no cover

    decider = _decider(_Fail())
    await decider.commit_round(_payload(1), _D(), {"ok": True}, defer_summary=True)
    assert await decider.flush_summary() is None
    assert decider._summary_due is False
    assert await decider.flush_summary() is None
    assert _Fail.calls == 1


async def test_backoff_defers_the_next_due_point():
    """一次失败后到期门槛抬到 `refresh + backoff`，连续失败时不再每 N 轮白烧一次超时预算。"""

    class _Fail:
        async def stream(self, **_kw):
            raise RuntimeError("LLM_ERROR")
            yield  # pragma: no cover

    decider = AiTradeDecider(_Fail(), "sys", context_refresh_rounds=2,
                             settings=Settings(_env_file=None))
    for round_no in (1, 2):
        await decider.commit_round(_payload(round_no), _D(), {"ok": True}, defer_summary=True)
    await decider.flush_summary()
    assert decider._summary_skip_rounds == 2          # 2 ** 0 × refresh(2)
    # 下一轮到期门槛已是 refresh + skip = 4：第 3 轮不再触发。
    assert await decider.commit_round(_payload(3), _D(), {"ok": True},
                                      defer_summary=True) is None
    assert decider._summary_due is False


# ---------- D-6：健康度的轨迹落点 ----------
async def test_summary_failed_breadcrumb_only_fires_on_a_real_failure():
    pushed: list[dict] = []

    async def _push(event):
        pushed.append(event)

    trace: dict = {}
    healthy = {"roundsSinceSummary": 4, "failures": 0, "stalled": False,
               "backoffRounds": 0, "lastError": None}
    await maybe_emit_summary_failed(_push, trace, healthy, 1, 100)
    assert pushed == [] and trace == {}

    failed = {"roundsSinceSummary": 0, "failures": 2, "stalled": False,
              "backoffRounds": 20, "lastError": "TimeoutError(30s)"}
    await maybe_emit_summary_failed(_push, trace, failed, 2, 200)
    assert len(pushed) == 1
    assert pushed[0]["data"]["phase"] == "summary_failed"
    assert pushed[0]["data"]["reason"] == "TimeoutError(30s)"
    assert trace["summaryHealth"] == failed
    await maybe_emit_summary_failed(_push, None, None, 3, 300)
    assert len(pushed) == 1                       # health 缺席 ⇒ 零动作


async def test_health_wire_is_available_from_the_decider():
    decider = _decider(_SlowLlm())
    wire = decider.summary_health_wire()
    assert wire["failures"] == 0 and wire["stalled"] is False
    assert wire["refreshRounds"] == 1
