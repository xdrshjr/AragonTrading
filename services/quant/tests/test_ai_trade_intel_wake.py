"""情报告警的会话侧接线（ai-trade-intel-actionability G-7 / G-10 / G-15 / G-29）。

行情 / OKX / LLM 全程桩驱动；`_maybe_intel_wake` 用 `asyncio.create_task` 起轮，断言前须 await
`_decision_task`。

本文件守的是轴 A 全部安全性的支点：**告警调用的是同一个 `_run_decision_round`**，八道门禁一道
不绕；以及 §4.3 的那句话——**唤醒句在任何配置下都不说假话**。
"""
from __future__ import annotations

import asyncio
import json
import time

import pytest

from app.agent.llm.base import StreamEvent
from app.ai_auto.decider import AiTradeDecider
from app.ai_auto.intel.alert import AlertCandidate, AlertHandoff
from app.ai_auto.session import AiTradeSession
from app.ai_auto.wake_text import format_wake_for_prompt
from app.config import Settings
from app.db.session import dispose_engine, init_db
from app.engine.simulator import RiskParams
from app.live_auto.executor import LiveExecutor
from app.okx.accounts import AccountCredentials
from app.okx.types import BAR_SECONDS, Candle
from app.paper.strategy_factory import PaperStrategySpec
from app.strategies.base import BaseStrategy, StrategyMeta

_START_TS = 1_700_000_000
_BAR = "1H"
_STEP = BAR_SECONDS[_BAR]
INST = "BTC-USDT-SWAP"


class _StubStrategy(BaseStrategy):
    meta = StrategyMeta(key="_stub_intel_wake", name="Stub", description="t",
                        default_params={}, params_schema={})

    def warmup(self) -> int:
        return 1

    def on_candle(self, df, i, ctx):
        return []


class _StubLLM:
    async def stream(self, *, system, messages, tools):
        if tools:
            args = {"action": "hold", "reason": "r", "confidence": 0.7}
            yield StreamEvent("tool_use_start", id="t", name="submit_trade_decision")
            yield StreamEvent("tool_use_args_delta", id="t",
                              partial_json=json.dumps(args, ensure_ascii=False))
            yield StreamEvent("message_done", stop_reason="tool_use")
        else:
            yield StreamEvent("text_delta", text="摘要")
            yield StreamEvent("message_done", stop_reason="end")


async def _no_trade_fee(*_a, **_k):
    return None


async def _no_position_history(*_a, **_k):
    return []


def _candle(idx: int, price: float = 30000.0) -> Candle:
    return Candle(ts=_START_TS + idx * _STEP, o=price, h=price + 5, l=price - 5,
                  c=price, vol=100.0, confirm=True)


def _make_session(settings: Settings | None = None) -> AiTradeSession:
    settings = settings or Settings(ai_trade_wake_skip_unchanged_pct=0.0,
                                    ai_trade_tactical_context=False)

    async def place_order_fn(order):
        return {"ok": True, "ordId": "x", "clOrdId": "aragonx"}

    async def close_position_fn(close):
        return {"ok": True, "clOrdId": "aragoncx"}

    async def positions_fn():
        return []

    executor = LiveExecutor(
        creds=AccountCredentials("ak", "sk-000000", "pp-000000"), account_id=1,
        inst_id=INST, td_mode="cross", pos_mode="long_short_mode", leverage=3.0,
        budget_usd=1000.0, risk=RiskParams(), ct_val=0.01, lot_sz=0.01, min_sz=0.01,
        settings=settings, place_order_fn=place_order_fn,
        close_position_fn=close_position_fn, positions_fn=positions_fn)
    decider = AiTradeDecider(_StubLLM(), "sys", context_refresh_rounds=10, settings=settings)
    spec = PaperStrategySpec(1, "S", "atomic", _StubStrategy({}), {})
    session = AiTradeSession("aitrade_intel_wake", 1, [spec], INST, _BAR, executor, decider,
                             priming=False, priming_bars=10, context_refresh_rounds=10,
                             wake_interval_s=0.0)   # ★ 墙钟唤醒关掉：告警是唯一触发源（E-26）

    async def stub_candles(bar, limit):
        return []

    session._window._safe_get_candles = stub_candles
    session._cost._fetch_fee_rate = _no_trade_fee
    session._cost._fetch_position_history = _no_position_history
    return session


def _candidate(**over) -> AlertCandidate:
    base = {"item_id": 41, "headline": "某交易所热钱包异常出金 1.2 万枚 ETH",
            "category": "risk", "tier": "ultra", "stance": "bearish", "impact": "high",
            "confidence": 0.78, "event_ts": int(time.time()) - 960,
            "captured_ts": int(time.time()), "domain_count": 3, "quoted": True,
            "is_scheduled": False, "verdict": "",
            "summary": "三个独立来源报道该交易所热钱包在过去一小时异常出金 1.2 万枚 ETH。"}
    return AlertCandidate(**{**base, **over})


def _handoff(**over) -> AlertHandoff:
    base = {"alert_id": 501, "candidate": _candidate(), "severity": 0.82,
            "created_wall": time.time(), "ref": "I02", "in_digest": True}
    return AlertHandoff(**{**base, **over})


async def _settle(session) -> None:
    task = session._decision_task
    if task is not None:
        try:
            await task
        except asyncio.CancelledError:
            pass


def _drain(q: asyncio.Queue) -> list[dict]:
    out: list[dict] = []
    while not q.empty():
        out.append(q.get_nowait())
    return out


@pytest.fixture(autouse=True)
def _record_outcomes(monkeypatch):
    """把 `note_alert_outcome` 换成一个记录器：会话侧的每一条抑制路径都必须回写台账。"""
    calls: list[tuple] = []
    monkeypatch.setattr(
        "app.ai_auto.session.note_alert_outcome",
        lambda inst_id, alert_id, *, outcome, run_id=0, round_no=0:
            calls.append((inst_id, alert_id, outcome, run_id, round_no)))
    return calls


# ---------- G-7：端到端唤醒 ----------
async def test_a_pending_alert_starts_one_standard_decision_round(_record_outcomes):
    await init_db()
    session = _make_session()
    try:
        q = session.subscribe()
        await session.feed_candle(_candle(0))
        await _settle(session)
        _drain(q)
        session.note_intel_alert(_handoff())
        await session._maybe_intel_wake()
        await _settle(session)

        decision = [e for e in _drain(q) if e["type"] == "decision"][-1]["data"]
        wake = decision["wake"]
        assert wake["kind"] == "intel_alert"
        # Q2 的算术依据：告警自己认领的那一轮 `trigger` 恒为 `"interval"` ⇒ `candle_close`
        # 恒不成立 ⇒ `intel_alert` 恒为首位。
        assert wake["trigger"] == "interval"
        assert wake["intel"]["itemId"] == 41 and wake["intel"]["ref"] == "I02"
        assert session._pending_intel_alert is None
        assert session._wake.intel_alert_wakes == 1
        # `roundNo` 指向**这一轮**（`rounds_completed + 1`）：上面那根 K 线已经跑过第 1 轮，
        # 故告警轮是第 2 轮。台账上那个数是用户点进决策轨迹的唯一线索，错一位就点不中。
        assert _record_outcomes == [("BTC-USDT-SWAP", 501, "woke", 1,
                                     decision["round"])]
    finally:
        await dispose_engine()


async def test_the_alert_round_is_never_skipped_and_never_bypasses_a_gate():
    """轴 A 的安全支点：它调用的是**同一个** `_run_decision_round`。

    判据取源码的字面形态——一个「另起一条更短的执行路径」的实现不会以任何别的方式露出来。
    """
    import inspect

    from app.ai_auto import session as session_mod

    src = inspect.getsource(session_mod.AiTradeSession._maybe_intel_wake)
    assert "self._run_decision_round(" in src
    # 会话侧**永不复读**告警总闸（§3.4.3 的第三道防线，G-15 的源码扫描一侧）。
    # **必须先剥注释与 docstring**：那条不变式本身就写在 docstring 里，按原文扫会把它自己
    # 判成违规——一条会把「写清楚了为什么」当成缺陷的门禁，只会教人把理由删掉。
    assert "ai_trade_intel_alert_enabled" not in _code_only(src)


async def test_the_session_never_rereads_the_alert_switch_anywhere():
    """G-15（源码扫描）：整个 `session.py` 里都不得出现对告警总闸的第二处读取。

    会话持的是 `executor.settings`（分组启动时定稿的那一份），而情报设置住在 `app_settings`
    里、经 `model_copy` 派生——两份在用户改设置之后必然不同步，那一判会变成「面板上关了但还在
    唤醒」或「面板上开了却永不唤醒」。
    """
    from pathlib import Path

    source = Path(__file__).resolve().parents[1] / "app" / "ai_auto" / "session.py"
    text = _code_only(source.read_text(encoding="utf-8"))
    assert "ai_trade_intel_alert_enabled" not in text
    # 先证明剥壳没把整份源码剥空，否则这条门禁会永远绿。
    assert "_maybe_intel_wake" in text and len(text) > 50_000


# ---------- G-10：五条会话侧抑制路径各写一次台账 ----------
@pytest.mark.parametrize(("prepare", "outcome", "cleared"), [
    (lambda s: setattr(s, "state", "paused"), "dropped_not_running", True),
    (lambda s: setattr(s, "_stale_degraded", True), "dropped_feed_stale", True),
    (lambda s: setattr(s, "_last_board", None), "dropped_no_board", True),
    (lambda s: setattr(s, "_deciding", True), "deferred", False),
])
async def test_every_suppression_path_writes_the_ledger(prepare, outcome, cleared,
                                                        _record_outcomes):
    await init_db()
    session = _make_session()
    try:
        await session.feed_candle(_candle(0))
        await _settle(session)
        session.note_intel_alert(_handoff())
        prepare(session)
        await session._maybe_intel_wake()
        assert [c[2] for c in _record_outcomes] == [outcome]
        assert (session._pending_intel_alert is None) is cleared
        assert session._wake.intel_alert_drops == 1
    finally:
        session._deciding = False
        await dispose_engine()


async def test_a_deferred_alert_is_counted_once_per_handoff_not_once_per_tick(
        _record_outcomes):
    """一轮决策要跑几秒，而盯市 tick 默认 5s：不节流的话同一次等待会被记成好几次丢弃。"""
    await init_db()
    session = _make_session()
    try:
        await session.feed_candle(_candle(0))
        await _settle(session)
        session.note_intel_alert(_handoff())
        session._deciding = True
        for _ in range(3):
            await session._maybe_intel_wake()
        assert [c[2] for c in _record_outcomes] == ["deferred"]
        assert session._wake.intel_alert_drops == 1
        assert session._pending_intel_alert is not None, "保留：下个 tick 再试"
    finally:
        session._deciding = False
        await dispose_engine()


async def test_an_expired_alert_is_dropped_rather_than_fired_late(_record_outcomes):
    """「等下去也不会变好」的四条一律清空：留着只会让一条陈旧情报在恢复会话时突然唤醒一轮。"""
    await init_db()
    session = _make_session()
    try:
        await session.feed_candle(_candle(0))
        await _settle(session)
        session.note_intel_alert(_handoff(created_wall=time.time() - 10_000))
        await session._maybe_intel_wake()
        assert [c[2] for c in _record_outcomes] == ["dropped_expired"]
        assert session._pending_intel_alert is None
    finally:
        await dispose_engine()


async def test_only_the_most_severe_pending_alert_survives(_record_outcomes):
    """至多一条，用 `severity` 比大小覆盖，**不排队**；被覆盖的那条立刻结账。"""
    await init_db()
    session = _make_session()
    try:
        session.note_intel_alert(_handoff(alert_id=1, severity=0.4))
        session.note_intel_alert(_handoff(alert_id=2, severity=0.9))
        assert session._pending_intel_alert.alert_id == 2
        assert [c[1:3] for c in _record_outcomes] == [(1, "superseded")]
        session.note_intel_alert(_handoff(alert_id=3, severity=0.1))
        assert session._pending_intel_alert.alert_id == 2
        assert [c[1:3] for c in _record_outcomes][-1] == (3, "superseded")
    finally:
        await dispose_engine()


# ---------- G-29：唤醒句绝不说假话 ----------
def _code_only(text: str) -> str:
    """剥掉三引号 docstring 与 `#` 行注释，只留可执行代码。

    本仓库的注释是被后来的人当证据读的，一条把「说明为什么」也算成违规的扫描会教人删注释。
    """
    import re

    stripped = re.sub(r'"""[\s\S]*?"""', "", text)
    return re.sub(r"(^|[^:])#.*", r"\1", stripped)


def _render(handoff: AlertHandoff) -> str:
    wire = {"kind": "intel_alert", "alsoTrue": [],
            "intel": handoff.wire(now=time.time())}
    return format_wake_for_prompt(wire)


def test_the_wake_sentence_claims_the_digest_only_when_it_is_really_there():
    text = _render(_handoff(in_digest=True))
    assert "该情报已在下方情报段中列出" in text
    assert "[I02]" in text
    assert _candidate().summary not in text, "在段内时不必重复摘要（下面就有）"


def test_the_wake_sentence_inlines_the_summary_when_the_item_missed_the_digest():
    """`in_digest=False`（被 `prompt_max_chars` 挤出去 / 注入已关闭）⇒ **内联摘要 + 如实说明**。

    照 v1 那样无条件说「详见下方情报段」，就是把一句确定错误的话逐字写进一次真实资金决策的
    prompt 首行。
    """
    text = _render(_handoff(in_digest=False))
    assert "该情报已在下方情报段中列出" not in text
    assert "未出现在下方情报段中" in text
    assert _candidate().summary in text


def test_the_wake_sentence_degrades_gracefully_without_a_citation_token():
    text = _render(_handoff(ref="", in_digest=True))
    assert "[" not in text.split("高危市场情报")[1].split("）")[0]
    assert "风险事件·偏空·高" in text
