"""决策轮的三个写点（ai-trade-audit-journal §2.2.1 / §2.2.6）。

写点的全部承诺是「审计需求绝不碰坏真实资金的交易」，故这里断的是**行为**而不是形状：
一轮决策恰好一行 / 总闸关时零行 / 轮内抛异常时只出一条错误事件而没有半空的决策行 /
喂一个不可序列化对象时那一轮**照样成功** / `_post_round` 内的保护平仓进得了同一行 /
被整轮跳过的轮次留得下一条占位事件。
"""
from __future__ import annotations

import asyncio
import json

import pytest

from app.ai_auto.journal.writer import JournalWriter, WriterConfig
from app.config import Settings
from app.db.session import dispose_engine, init_db

from .test_ai_trade_session import _candle, _make_session, _settle


class _Recorder:
    """把落库替换成一个可观察的替身（本文件测的是写点的行为，不是 SQL）。"""

    def __init__(self) -> None:
        self.rows: list[dict] = []

    async def __call__(self, session, rows):
        self.rows.extend(rows)
        return len(rows)

    @property
    def decisions(self) -> list[dict]:
        return [row for row in self.rows if row.get("kind") == "decision"]

    @property
    def events(self) -> list[dict]:
        return [row for row in self.rows if row.get("kind") == "event"]

    def codes(self) -> list[str]:
        return [row["cols"]["code"] for row in self.events]


def _install_writer(monkeypatch, *, enabled: bool = True,
                    event_min_level: str = "info") -> tuple[JournalWriter, _Recorder]:
    """装一个真的 `JournalWriter`（含队列语义与级别闸），但把落库导到记录器上。"""
    recorder = _Recorder()
    from app.ai_auto.journal import writer as writer_mod
    monkeypatch.setattr(writer_mod.journal_repo, "write_journal_batch", recorder)

    def _maker():
        class _S:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False
        return _S()

    writer = JournalWriter(
        WriterConfig(enabled=enabled, queue_max=64, batch_size=8, flush_interval_s=0.01,
                     drain_timeout_s=0.5, event_min_level=event_min_level),
        lambda: _maker)
    monkeypatch.setattr("app.ai_auto.session.get_journal_writer", lambda: writer)
    writer.start()
    return writer, recorder


async def _flush(writer: JournalWriter, recorder: _Recorder, *, at_least: int = 1) -> None:
    for _ in range(80):
        if len(recorder.rows) >= at_least:
            break
        await asyncio.sleep(0.01)
    await writer.stop()


@pytest.mark.asyncio
async def test_one_round_produces_exactly_one_decision_row(monkeypatch):
    await init_db()
    writer, recorder = _install_writer(monkeypatch)
    session, _calls, _pos = _make_session(action="open_long")
    try:
        await session.feed_candle(_candle(0))
        await _settle(session)
        await _flush(writer, recorder)
    finally:
        await dispose_engine()
    assert len(recorder.decisions) == 1
    cols = recorder.decisions[0]["cols"]
    assert cols["round_no"] == 1 and cols["final_action"] == "open_long"
    assert cols["inst_id"] == "BTC-USDT-SWAP" and cols["bar"] == "1H"
    assert json.loads(cols["gates_json"])["layers"], "门禁链三档全落"


@pytest.mark.asyncio
async def test_gate_off_writes_nothing_at_all(monkeypatch):
    """总闸关 ⇒ 决策轮的行为与本特性合入前**逐字节**相同。"""
    await init_db()
    writer, recorder = _install_writer(monkeypatch, enabled=False)
    session, _calls, _pos = _make_session(action="open_long")
    try:
        await session.feed_candle(_candle(0))
        await _settle(session)
        await writer.stop()
    finally:
        await dispose_engine()
    assert recorder.rows == []
    assert session.rounds_completed == 1, "总闸关不得影响决策轮本身"


@pytest.mark.asyncio
async def test_round_exception_yields_an_error_event_and_no_half_empty_row(monkeypatch):
    """失败可能发生在 `payload` 尚未绑定之前——一个字段半空的决策行比一条明确的错误事件更坏。"""
    await init_db()
    writer, recorder = _install_writer(monkeypatch)
    session, _calls, _pos = _make_session(action="open_long")

    def _boom(*_args, **_kwargs):
        raise RuntimeError("payload boom")

    monkeypatch.setattr(session, "_build_decision_payload", _boom)
    try:
        await session.feed_candle(_candle(0))
        await _settle(session)
        await _flush(writer, recorder)
    finally:
        await dispose_engine()
    assert recorder.decisions == []
    assert recorder.codes() == ["AI_TRADE_ROUND_FAILED"]
    assert recorder.events[0]["cols"]["level"] == "error"


@pytest.mark.asyncio
async def test_serialization_failure_never_turns_a_filled_round_into_a_failure(monkeypatch):
    """★ A-3：喂一个不可序列化对象 ⇒ 那一轮**照样成功**，只多一个 `serializeErrors`。

    这条是 §2.2.1 的核心：`json.dumps` 跑在决策轮的 `try` 内，一次序列化失败若冒泡，
    会把一轮**已经成功成交**的决策整个翻成 `AI_TRADE_ROUND_FAILED`——审计需求恰好害了
    它声称绝不碰的东西。
    """
    await init_db()
    writer, recorder = _install_writer(monkeypatch)
    session, calls, _pos = _make_session(action="open_long")

    def _explode(*_args, **_kwargs):
        raise TypeError("Object of type X is not JSON serializable")

    monkeypatch.setattr("app.ai_auto.session.build_decision_row", _explode)
    try:
        await session.feed_candle(_candle(0))
        await _settle(session)
        await asyncio.sleep(0.05)
        await writer.stop()
    finally:
        await dispose_engine()
    assert session.rounds_completed == 1, "那一轮必须照常完成"
    assert len(calls["orders"]) == 1, "而且那一单必须照常发出去"
    assert writer.stats()["counters"]["serializeErrors"] == 1
    assert "AI_TRADE_ROUND_FAILED" not in recorder.codes()


@pytest.mark.asyncio
async def test_post_round_fill_lands_in_the_same_decision_row(monkeypatch):
    """★ A-4：`_post_round` 内产生的成交腿必须进同一行，且标 `source="post_round"`。

    没有这一条，「利润保护在决策轮内平掉的那一笔」在日志台上会表现为「模型说开多、然后什么
    都没发生、然后不知从哪冒出来一笔平仓」——而利润保护正是本仓库实测唯一为正的确定性离场通道。
    """
    await init_db()
    writer, recorder = _install_writer(monkeypatch)
    session, _calls, _pos = _make_session(action="open_long")
    original = session._post_round

    async def _post_round_with_guard_exit(ref_px, ts):
        await original(ref_px, ts)
        # 模拟 `_maybe_profit_exit` 在轮末平掉一笔：它**不在** `executed` 里。
        session.executor.trades.append({"ts": ts, "side": "close", "price": 30100.0,
                                        "qty": 0.01, "fee": 0.01, "pnl": 1.0,
                                        "reason": "profit_guard_peak",
                                        "clOrdId": "aragon-guard"})

    monkeypatch.setattr(session, "_post_round", _post_round_with_guard_exit)
    try:
        await session.feed_candle(_candle(0))
        await _settle(session)
        await _flush(writer, recorder)
    finally:
        await dispose_engine()
    legs = json.loads(recorder.decisions[0]["cols"]["fill_ref_json"])["legs"]
    assert len(legs) == 2
    assert legs[0]["source"] == "round" and legs[1]["source"] == "post_round"
    assert legs[1]["reason"] == "profit_guard_peak"
    # 锚点取**最后**一条腿：那才是「这一轮最终发生的事」。
    assert recorder.decisions[0]["cols"]["cl_ord_id"] == "aragon-guard"


@pytest.mark.asyncio
async def test_skipped_round_leaves_a_placeholder_event(monkeypatch):
    """★ A-5：被整轮跳过的轮次没有决策行——不写这条，审计台上就是一段没有解释的空白。"""
    await init_db()
    writer, recorder = _install_writer(monkeypatch)
    session, _calls, _pos = _make_session(action="open_long")

    async def _always_skip(round_no, ts, wake):
        session.rounds_skipped += 1
        session._journal_event(_skip_event(round_no, ts, wake))
        return True

    from app.ai_auto.journal import EventJournalInput

    def _skip_event(round_no, ts, wake):
        return EventJournalInput(
            category="round", code="ROUND_SKIPPED", level="info",
            message="本轮被整轮跳过（结构上不可能产生任何合法动作）", ts=int(ts),
            detail={"roundNo": round_no, "reason": "horizon_locked",
                    "minHoldRemainingSeconds": 120, "wakeKind": wake.get("kind")})

    monkeypatch.setattr(session, "_maybe_skip_round", _always_skip)
    try:
        await session.feed_candle(_candle(0))
        await _settle(session)
        await _flush(writer, recorder)
    finally:
        await dispose_engine()
    assert recorder.decisions == []
    assert recorder.codes() == ["ROUND_SKIPPED"]
    detail = json.loads(recorder.events[0]["cols"]["detail_json"])
    assert detail["reason"] == "horizon_locked"


@pytest.mark.asyncio
async def test_event_min_level_warn_silences_the_skip_placeholder(monkeypatch):
    """`EVENT_MIN_LEVEL=warn` ⇒ info 级整体不采。那是用户显式选择的静默，不是设计上的空洞。"""
    await init_db()
    writer, recorder = _install_writer(monkeypatch, event_min_level="warn")
    session, _calls, _pos = _make_session(action="open_long")
    from app.ai_auto.journal import EventJournalInput
    try:
        session._journal_event(EventJournalInput(
            category="round", code="ROUND_SKIPPED", level="info", message="x"))
        await asyncio.sleep(0.05)
        await writer.stop()
    finally:
        await dispose_engine()
    assert recorder.events == []
    assert writer.stats()["counters"]["dropped"] == 0, "静默不是丢弃，不该计进缺口"


@pytest.mark.asyncio
async def test_cancellation_is_never_journalled(monkeypatch):
    """取消原样上抛、不写审计事件，漏斗仍闭合未发送意愿。"""
    writer, recorder = _install_writer(monkeypatch)
    session, calls, _pos = _make_session(action="open_long")

    async def _cancel(*_args, **_kwargs):
        raise asyncio.CancelledError

    monkeypatch.setattr(session, "_execute_decision", _cancel)
    try:
        with pytest.raises(asyncio.CancelledError):
            await session._run_decision_round(
                [], 0, 30000.0, 1_700_000_000,
                {"trigger": "candle", "kind": "candle_close"})
    finally:
        await writer.stop()
    assert recorder.rows == []
    assert calls["orders"] == []
    assert session._deciding is False
    from app.ai_auto.open_funnel import funnel_wire
    funnel = funnel_wire(session._discipline.funnel)
    assert funnel["executionBlocked"]["canceled_before_send"] == 1


@pytest.mark.asyncio
async def test_gate_readings_use_the_real_trace_keys(monkeypatch):
    """门禁读数的键名必须逐字取自 `trace.new_round_trace` 的**实际**键集。

    写错一个名字不会报错，只会让那一层的读数在详情页上恒为空——「有写点没读点」正是本仓库
    反复付过代价的形状。故这里断言的是「跑完一轮之后，至少有一层真的带回了读数」，
    而不只是「gates_json 非空」。
    """
    await init_db()
    writer, recorder = _install_writer(monkeypatch)
    session, _calls, _pos = _make_session(action="open_long")
    try:
        await session.feed_candle(_candle(0))
        await _settle(session)
        await _flush(writer, recorder)
    finally:
        await dispose_engine()
    gates = json.loads(recorder.decisions[0]["cols"]["gates_json"])
    assert [row["key"] for row in gates["layers"]], "13 层一条不少"
    with_readings = [row for row in gates["layers"] if row.get("readings")]
    assert with_readings, "一层读数都取不到 ⇒ 键名多半写错了（那不会报错，只会恒为空）"
    # 反例：一个不存在的键取出来是 None，`_journal_reading` 会把它整键丢掉。
    assert session._journal_reading({"a": None, "b": 1}) == {"b": 1}


@pytest.mark.asyncio
async def test_journal_settings_come_from_the_session_executor_settings(monkeypatch):
    """采集档取的是**会话那一份派生 settings**，不是进程级单例（否则面板上关了、会话照旧开着）。"""
    await init_db()
    settings = Settings(ai_trade_journal_capture_tier="minimal")
    session, _calls, _pos = _make_session(action="open_long", settings=settings)
    try:
        scope = session._journal_scope()
    finally:
        await dispose_engine()
    assert scope.limits.tier == "minimal"
    assert scope.inst_id == "BTC-USDT-SWAP"


@pytest.mark.asyncio
async def test_gate_chain_uses_the_pre_execution_degrade_not_the_composite(monkeypatch):
    """门禁链取 `gate_degrade`，绝不取被 `closeBlocked` 污染过的合成 `degrade`。

    这是 directional-symmetry B-2 已经成文过的那一条：`exit_order_slot_busy` 不在任何降级前缀
    里，经 `classify_block` 会落进 `"other"` ——于是门禁链上会出现一条**凭空捏造**的
    「本轮被『其它』层拦下了」。挂单槽位被占用不是一道门禁。
    """
    from app.ai_auto.journal.records import build_gates
    # 合成值会被归到 `other`（那正是要避免的），门禁前的 `None` 则谁都没否决。
    assert build_gates({}, {}, "exit_order_slot_busy")["vetoedBy"] == "other"
    assert build_gates({}, {}, None)["vetoedBy"] is None

    await init_db()
    writer, recorder = _install_writer(monkeypatch)
    session, _calls, _pos = _make_session(action="open_long")
    try:
        await session.feed_candle(_candle(0))
        await _settle(session)
        await _flush(writer, recorder)
    finally:
        await dispose_engine()
    gates = json.loads(recorder.decisions[0]["cols"]["gates_json"])
    assert gates["vetoedBy"] is None, "一轮正常放行的开仓不该在门禁链上被判成被谁拦了"


def test_the_write_point_passes_gate_degrade_to_the_gate_chain():
    """源码判据：写点传给 `_journal_gates` 的**必须**是 `inp.gate_degrade`。

    只断言运行期结果的用例对这条不敏感——正常轮里两个值恰好都是 `None`，
    而它们分叉的那一轮（挂单槽位被占用的平仓轮）在单测里造起来很贵。
    """
    import pathlib
    src = (pathlib.Path(__file__).resolve().parents[1] / "app" / "ai_auto"
           / "session.py").read_text(encoding="utf-8")
    assert "self._journal_gates(inp.gate_degrade)" in src
    assert "self._journal_gates(inp.degrade)" not in src
