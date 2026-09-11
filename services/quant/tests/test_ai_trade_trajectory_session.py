"""后训练轨迹采集 · 会话侧写点（ai-trade-trajectory-capture §6.1 S-1…S-12 + G-T1）。

真会话 + 假 LLM + 真 recorder 写线程：用例驱动 `feed_candle` / `_run_decision_round`，关闭 recorder 后
读回段文件断言记录序列。行情 / OKX 全程桩驱动，占位假凭证。

多数用例不跑 `start()`（它要建 WS 与盯市循环），而是手动走一遍 W1（`open_episode`）；W1 的落点
由 `test_w1_start_opens_the_episode_after_priming` 单独经真实 `start()` 验证。
"""
from __future__ import annotations

import ast
import asyncio
import gzip
import json
import re
import shutil
import tempfile
import uuid
from pathlib import Path

import pytest

from app.agent.llm.base import StreamEvent, ToolSchema
from app.ai_auto.decider import AiTradeDecider
from app.ai_auto.hold_horizon import resolve_horizon_rule
from app.ai_auto.horizon_runtime import HorizonRuntime
from app.ai_auto.llm_budget import AiTradeLlmClients
from app.ai_auto.prompts import DECISION_TOOL_NAME, REFLECTION_TOOL_NAME
from app.ai_auto.session import AiTradeSession
from app.ai_auto.trajectory import (NULL_TRAJECTORY, EpisodeFacts, GroupRecorderRequest,
                                    RecordingLlmClient, SessionBinding, TrajectoryRule,
                                    open_group_recorder, wrap_llm_clients)
from app.config import Settings
from app.db.session import dispose_engine, init_db
from app.engine.simulator import RiskParams
from app.live_auto.executor import LiveExecutor
from app.okx.accounts import AccountCredentials
from app.paper.strategy_factory import PaperStrategySpec
from tests.test_ai_trade_session import (_START_TS, _FakeRest, _StubStrategy, _candle, _drain,
                                         _no_funding_refresh, _no_position_history,
                                         _no_trade_fee, _settle)

_INST = "BTC-USDT-SWAP"
_APP = Path(__file__).resolve().parents[1] / "app" / "ai_auto"
_CREATED_TS = 1_788_000_000


# ---------------- 假 LLM ----------------
def _tool_script(name: str, args: dict) -> list[StreamEvent]:
    tool_id = f"{name}-1"
    return [StreamEvent("tool_use_start", id=tool_id, name=name),
            StreamEvent("tool_use_args_delta", id=tool_id,
                        partial_json=json.dumps(args, ensure_ascii=False)),
            StreamEvent("tool_use_end", id=tool_id),
            StreamEvent("message_done", stop_reason="tool_use")]


class _RoundLLM:
    """按工具名区分决策 / 反思 / 摘要；可让第 N 次决策挂起，或让决策 / 摘要卡在一个事件上。"""

    def __init__(self, *, action="open_long", hang_decision_calls=(), decision_gate=None,
                 summary_gate=None):
        self.action = action
        self.hang_decision_calls = set(hang_decision_calls)
        self.decision_gate = decision_gate
        self.summary_gate = summary_gate
        self.decision_calls = 0
        self.summary_calls = 0

    async def stream(self, *, system, messages, tools):
        names = {tool.name for tool in tools}
        if not names:
            async for event in self._summary():
                yield event
            return
        if REFLECTION_TOOL_NAME in names:
            for event in _tool_script(REFLECTION_TOOL_NAME,
                                      {"verdict": "confirm", "reason": "复核通过"}):
                yield event
            return
        self.decision_calls += 1
        if self.decision_calls in self.hang_decision_calls:
            await asyncio.sleep(3600)
        if self.decision_gate is not None:
            await self.decision_gate.wait()
        args = {"action": self.action, "reason": "多周期共振向上", "confidence": 0.8}
        if self.action.startswith("open_"):
            args["sizeFraction"] = 0.5
        yield StreamEvent("text_delta", text="研判：多周期共振向上。")
        for event in _tool_script(DECISION_TOOL_NAME, args):
            yield event

    async def _summary(self):
        self.summary_calls += 1
        if self.summary_gate is not None:
            await self.summary_gate.wait()
        yield StreamEvent("text_delta", text="综合摘要：偏多")
        yield StreamEvent("message_done", stop_reason="end_turn")


class _FakeExecutor:
    def __init__(self, trades: int = 0) -> None:
        self.trade_count = trades
        self.trades = [_trade(index) for index in range(trades)]

    def add(self, trade: dict) -> None:
        self.trade_count += 1
        self.trades.append(trade)


class _FakeWS:
    is_alive = True

    def __init__(self, *_args, **_kwargs) -> None:
        self.started = 0

    def start(self) -> None:
        self.started += 1

    async def stop(self) -> None:
        return None


# ---------------- 装配 ----------------
def _trade(index: int) -> dict:
    return {"side": "long", "qty": 0.01, "price": 30000.0 + index,
            "explain": {"meta": {"accountingEventId": f"evt-{index}"}}}


def _settings(**over) -> Settings:
    base = {"ai_trade_funding_enabled": False, "ai_trade_decision_max_retries": 0,
            "ai_trade_retry_base_ms": 0, "ai_trade_retry_max_ms": 0,
            "ai_trade_trajectory_min_free_bytes": 0}
    base.update(over)
    return Settings(**base)


def _facts(run_id: int = 1) -> EpisodeFacts:
    return EpisodeFacts("s1", run_id, _INST, "1H", [1], ["S"], 10, 0.0, None, None, _START_TS,
                        {"costBasis": "net", "ctVal": 0.01})


async def _open_recorder(settings: Settings, *, root: str | None = None,
                         group_id: str | None = None, resume_of: dict | None = None):
    root = root or tempfile.mkdtemp(prefix="trj")
    result = await open_group_recorder(GroupRecorderRequest(
        rule=TrajectoryRule(True, root), settings=settings,
        group_id=group_id or f"aitradegrp_{uuid.uuid4().hex}", created_ts=_CREATED_TS,
        resume_of=resume_of, bar="1H"))
    assert result.recorder is not None, result.error
    return result.recorder, root


async def _close_and_read(recorder) -> list[dict]:
    await recorder.aclose("test", compress_active=False, timeout_s=5)
    assert not recorder.is_alive()
    rows: list[dict] = []
    for segment in sorted((recorder.group_dir / "members").rglob("seg-*")):
        opener = gzip.open if segment.suffix == ".gz" else open
        with opener(segment, "rt", encoding="utf-8") as handle:
            rows.extend(json.loads(line) for line in handle if line.strip())
    return rows


async def _cleanup(recorder, root: str) -> None:
    if recorder is not None and recorder.is_alive():
        await recorder.aclose("test", compress_active=False, timeout_s=5)
    shutil.rmtree(root, ignore_errors=True)


def _build_session(llm, settings: Settings, *, channel=None, context_rounds: int = 10,
                   reflection: bool = False):
    """真 `AiTradeSession`；`channel` 非空时按生产装配包装三个客户端并挂上通道。"""

    async def place_order_fn(order):
        return {"ok": True, "ordId": "x", "clOrdId": "aragonx"}

    async def close_position_fn(close):
        return {"ok": True, "clOrdId": "aragoncx"}

    async def positions_fn():
        executor = session.executor
        if executor.projected == "flat":
            return []
        return [{"instId": _INST, "pos": executor.entry_qty_sz, "upl": 0.0}]

    executor = LiveExecutor(
        creds=AccountCredentials("ak", "sk-000000", "pp-000000"), account_id=1,
        inst_id=_INST, td_mode="cross", pos_mode="long_short_mode", leverage=3.0,
        budget_usd=1000.0, risk=RiskParams(), ct_val=0.01, lot_sz=0.01, min_sz=0.01,
        settings=settings, place_order_fn=place_order_fn,
        close_position_fn=close_position_fn, positions_fn=positions_fn)
    clients = AiTradeLlmClients(llm, llm, llm)
    if channel is not None:
        clients = wrap_llm_clients(clients, channel)
    decider = AiTradeDecider(clients.decision, "sys", context_refresh_rounds=context_rounds,
                             settings=settings, reflection_enabled=reflection,
                             reflection_llm=clients.reflection, summary_llm=clients.summary)
    spec = PaperStrategySpec(1, "S", "atomic", _StubStrategy({}), {})
    session = AiTradeSession("aitrade_traj", 1, [spec], _INST, "1H", executor, decider,
                             priming=False, priming_bars=10,
                             context_refresh_rounds=context_rounds)

    async def no_htf_data(*_args, **_kwargs):
        return []

    session._window._safe_get_candles = no_htf_data
    session._cost._fetch_fee_rate = _no_trade_fee
    session._cost._fetch_position_history = _no_position_history
    session._cost.refresh_funding = _no_funding_refresh
    session.attach_trajectory(channel)
    return session


def _open_episode(session) -> None:
    """手动走 W1（用例不跑 `start()`）。"""
    session._trajectory.open_episode(session._trajectory_episode_facts())


async def _wait_until(predicate, timeout: float = 5.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        assert asyncio.get_running_loop().time() < deadline, "等待条件超时"
        await asyncio.sleep(0.01)


def _kinds(rows: list[dict]) -> list[str]:
    return [row["kind"] for row in rows if row["seq"] is not None]


def _of(rows: list[dict], kind: str) -> list[dict]:
    return [row for row in rows if row["kind"] == kind]


# ---------------- S-1 / S-2：一轮的完整断面 ----------------
async def test_s1_one_round_records_every_call_and_adopts_the_retry():
    """首次决策超时 → 重试成功开仓 → 反思 confirm → 摘要到期：4 条调用、1 条 round、被采纳者是重试那次。"""
    await init_db()
    settings = _settings(ai_trade_decision_timeout_s=0.3, ai_trade_decision_max_retries=1)
    recorder, root = await _open_recorder(settings)
    llm = _RoundLLM(hang_decision_calls={1})
    try:
        session = _build_session(llm, settings, channel=recorder.channel(_INST),
                                 context_rounds=1, reflection=True)
        _open_episode(session)
        await session.feed_candle(_candle(0))
        await _settle(session)
        rows = await _close_and_read(recorder)
    finally:
        await _cleanup(recorder, root)
        await dispose_engine()
    assert _kinds(rows)[0] == "episode_start"
    assert _of(rows, "episode_start")[0]["env"] == recorder.env
    calls = _of(rows, "llm_call")
    assert [call["site"] for call in calls] == ["decision", "decision", "reflection", "summary"]
    assert calls[0]["status"] == "cancelled" and calls[1]["status"] == "completed"
    assert calls[3]["responseComplete"] is True
    assert [call["scope"]["callIndex"] for call in calls] == [1, 2, 3, 4]
    rounds = _of(rows, "round")
    assert len(rounds) == 1
    record = rounds[0]
    assert record["completion"] == "full" and record["summaryInstalled"] is True
    assert [call["callId"] for call in record["calls"]] == [call["callId"] for call in calls]
    assert record["adoptedDecisionCallId"] == calls[1]["callId"]
    assert record["decision"]["model"]["action"] == "open_long"
    assert record["decision"]["final"]["action"] == "open_long"
    assert record["observation"]["round"] == 1
    assert _of(rows, "round_aborted") == [] and _of(rows, "round_failed") == []


async def test_s2_a_fill_made_inside_post_round_lands_in_the_round_record():
    """`_post_round` 里（利润保护）再成交的那一笔不在 `executed` 里，却必须出现在本轮的成交腿中。"""
    await init_db()
    settings = _settings()
    recorder, root = await _open_recorder(settings)
    try:
        session = _build_session(_RoundLLM(), settings, channel=recorder.channel(_INST))
        original = session._post_round

        async def post_round_with_fill(ref_px, ts):
            await original(ref_px, ts)
            session.executor.trades.append({"side": "close", "qty": 0.01, "price": 30010.0,
                                            "pnl": 0.1, "explain": {"meta": {
                                                "accountingEventId": "post-round-close"}}})
            session.executor.trade_count += 1

        session._post_round = post_round_with_fill
        _open_episode(session)
        await session.feed_candle(_candle(0))
        await _settle(session)
        rows = await _close_and_read(recorder)
    finally:
        await _cleanup(recorder, root)
        await dispose_engine()
    fills = _of(rows, "round")[0]["fills"]
    assert fills[-1]["explain"]["meta"]["accountingEventId"] == "post-round-close"
    seqs = [fill["ledgerSeq"] for fill in fills]
    assert seqs == list(range(seqs[0], seqs[0] + len(seqs)))
    assert _of(rows, "fills") == [], "同一笔成交绝不被采两次"


def _sync_before(session, name: str) -> None:
    """让一次盯市同步插在会话方法 `name` 之前执行（并发的盯市 tick 恰好落在这个 await 上）。"""
    original = getattr(session, name)

    async def synced(*args, **kwargs):
        session._trajectory.sync_fills("monitor")
        return await original(*args, **kwargs)

    setattr(session, name, synced)


async def test_s2_a_monitor_sync_inside_the_round_never_steals_its_own_fill():
    """盯市循环是另一个任务：它在「成交之后、W3a 之前」与「W3a 之后、W3b 之前」各同步一次，本轮自己的
    开仓腿仍必须记在 `round.fills` 里——编译器只把 `round` 承载的腿记进该轮奖励，被抢走的那一轮会以
    空成交、未结算的样子进 steps.jsonl（真正开了仓的决策反而拿不到奖励）。"""
    await init_db()
    settings = _settings()
    recorder, root = await _open_recorder(settings)
    try:
        session = _build_session(_RoundLLM(), settings, channel=recorder.channel(_INST))
        for name in ("_emit_result_trace", "_emit_decision"):
            _sync_before(session, name)
        _open_episode(session)
        await session.feed_candle(_candle(0))
        await _settle(session)
        filled = session.executor.trade_count
        session._trajectory.sync_fills("monitor")        # 回合关闭之后的同步：已无可取
        rows = await _close_and_read(recorder)
    finally:
        await _cleanup(recorder, root)
        await dispose_engine()
    assert filled >= 1
    assert len(_of(rows, "round")[0]["fills"]) == filled
    assert _of(rows, "fills") == [], "盯市同步让路给在飞回合：同一笔成交绝不换承载记录"


# ---------------- S-3 / S-4 / S-10：失败与打断 ----------------
async def test_s3_an_exception_before_staging_writes_round_failed_with_the_calls():
    await init_db()
    settings = _settings()
    recorder, root = await _open_recorder(settings)
    try:
        session = _build_session(_RoundLLM(), settings, channel=recorder.channel(_INST))

        async def boom(*_args, **_kwargs):
            raise RuntimeError("gate exploded")

        session._apply_base_rule_gate = boom
        _open_episode(session)
        await session.feed_candle(_candle(0))
        await _settle(session)
        rows = await _close_and_read(recorder)
    finally:
        await _cleanup(recorder, root)
        await dispose_engine()
    failed = _of(rows, "round_failed")
    assert len(failed) == 1 and _of(rows, "round") == []
    assert failed[0]["errorType"] == "RuntimeError" and failed[0]["message"] == "gate exploded"
    assert [call["callId"] for call in failed[0]["calls"]] == [
        call["callId"] for call in _of(rows, "llm_call")]


async def test_s4_a_round_cancelled_during_the_decision_call_is_aborted():
    """暂停第 ③ 步 cancel 决策任务（`_cancel_task`）：`round_aborted`，`CancelledError` 照常上抛。"""
    await init_db()
    settings = _settings()
    recorder, root = await _open_recorder(settings)
    llm = _RoundLLM(decision_gate=asyncio.Event())
    try:
        session = _build_session(llm, settings, channel=recorder.channel(_INST))
        _open_episode(session)
        await session.feed_candle(_candle(0))
        await _wait_until(lambda: llm.decision_calls == 1)
        task = session._decision_task
        session._cancel_task("_decision_task")
        with pytest.raises(asyncio.CancelledError):
            await task
        rows = await _close_and_read(recorder)
    finally:
        await _cleanup(recorder, root)
        await dispose_engine()
    aborted = _of(rows, "round_aborted")
    assert len(aborted) == 1 and aborted[0]["reason"] == "not_completed"
    assert _of(rows, "round") == []
    assert [call["status"] for call in _of(rows, "llm_call")] == ["cancelled"]


async def test_s10_cancel_while_the_summary_hangs_keeps_the_filled_round_as_partial():
    """RV4：成交之后、`flush_summary` 挂起期间被打断 ⇒ 恰一条 `round{partial, cancelled}`，无 `round_aborted`。"""
    await init_db()
    settings = _settings()
    recorder, root = await _open_recorder(settings)
    llm = _RoundLLM(summary_gate=asyncio.Event())
    try:
        session = _build_session(llm, settings, channel=recorder.channel(_INST), context_rounds=1)
        _open_episode(session)
        await session.feed_candle(_candle(0))
        await _wait_until(lambda: llm.summary_calls == 1)
        filled = session.executor.trade_count
        task = session._decision_task
        session._cancel_task("_decision_task")
        with pytest.raises(asyncio.CancelledError):
            await task
        rows = await _close_and_read(recorder)
    finally:
        await _cleanup(recorder, root)
        await dispose_engine()
    rounds = _of(rows, "round")
    assert len(rounds) == 1 and _of(rows, "round_aborted") == []
    record = rounds[0]
    assert record["completion"] == "partial" and record["abortReason"] == "cancelled"
    assert record["observation"] and record["decision"]["final"]["action"] == "open_long"
    assert len(record["fills"]) == filled
    assert _of(rows, "llm_call")[-1]["status"] == "cancelled"


async def test_s10_an_exception_after_the_fill_keeps_the_round_as_partial():
    await init_db()
    settings = _settings()
    recorder, root = await _open_recorder(settings)
    try:
        session = _build_session(_RoundLLM(), settings, channel=recorder.channel(_INST))

        async def boom(*_args, **_kwargs):
            raise RuntimeError("post round exploded")

        session._post_round = boom
        _open_episode(session)
        await session.feed_candle(_candle(0))
        await _settle(session)
        rows = await _close_and_read(recorder)
    finally:
        await _cleanup(recorder, root)
        await dispose_engine()
    rounds = _of(rows, "round")
    assert len(rounds) == 1 and _of(rows, "round_failed") == []
    assert rounds[0]["completion"] == "partial" and rounds[0]["abortReason"] == "exception"
    assert rounds[0]["error"] == {"type": "RuntimeError", "message": "post round exploded"}


async def test_s10_a_session_stop_inside_the_round_is_episode_closed_not_cancelled():
    """`_post_round` 里触发会话止盈 / 止损 ⇒ 决策任务内部 `stop()`：这一轮其实已经跑完，不得标成
    `cancelled`（训练侧按「被打断」过滤会把 episode 最后、奖励最大的那一步丢掉），成交腿也必须在本轮。"""
    await init_db()
    settings = _settings()
    recorder, root = await _open_recorder(settings)
    try:
        session = _build_session(_RoundLLM(), settings, channel=recorder.channel(_INST))

        async def stop_inside_the_round(_ref_px, _ts):
            await session.stop("session_take_profit")

        session._post_round = stop_inside_the_round
        _open_episode(session)
        await session.feed_candle(_candle(0))
        task = session._decision_task
        await task
        rows = await _close_and_read(recorder)
    finally:
        await _cleanup(recorder, root)
        await dispose_engine()
    rounds = _of(rows, "round")
    assert len(rounds) == 1 and _of(rows, "round_aborted") == []
    assert rounds[0]["completion"] == "partial"
    assert rounds[0]["abortReason"] == "episode_closed"
    assert rounds[0]["fills"], "本轮自己的开仓腿（与停止时的平仓腿）记在本轮"
    assert _of(rows, "fills") == []
    assert _kinds(rows)[-1] == "episode_end"


# ---------------- S-5 / S-11：整轮跳过 ----------------
async def test_s5_s11_skipped_round_writes_one_record_with_the_ws_keys_and_no_abort():
    await init_db()
    settings = _settings(ai_trade_skip_round_when_gated=True)
    recorder, root = await _open_recorder(settings)
    pushed: list[dict] = []
    try:
        session = _build_session(_RoundLLM(), settings, channel=recorder.channel(_INST))
        rule = resolve_horizon_rule({"profile": "intraday_swing"}, settings, bar="1H")
        session.horizon_rule = rule
        session._horizon = HorizonRuntime(rule)
        session._horizon.refresh = lambda _facts: {
            "status": "holding", "releasedGates": [], "minHoldRemainingSeconds": 900.0}

        async def capture_push(event):
            pushed.append(event)

        session._push = capture_push
        _open_episode(session)
        await session._run_decision_round([], 0, 30000.0, _START_TS,
                                          {"kind": "heartbeat", "trigger": "interval"})
        rows = await _close_and_read(recorder)
    finally:
        await _cleanup(recorder, root)
        await dispose_engine()
    assert _kinds(rows) == ["episode_start", "round_skipped"], "跳过轮一轮只有一条记录（RV5）"
    record = _of(rows, "round_skipped")[0]
    event = next(item["data"] for item in pushed if item.get("type") == "round_skipped")
    for key in ("roundNo", "ts", "reason", "minHoldRemainingSeconds", "wakeKind"):
        assert record[key] == event[key], key


# ---------------- S-6：采集与否不改变交易 ----------------
_WALL_CLOCK = re.compile(r"summaryAsOfTs=[0-9.]+")


def _context_fingerprint(decider) -> list:
    """上下文逐条指纹。摘要种子里的 `summaryAsOfTs` 是墙钟读数，两次运行必然差几毫秒，剔除后再比。"""
    out = []
    for message in decider._context:
        blocks = []
        for block in message.content:
            if getattr(block, "type", "") == "tool_result":
                continue
            text = getattr(block, "text", None)
            blocks.append(_WALL_CLOCK.sub("summaryAsOfTs=<wall>", text) if isinstance(text, str)
                          else getattr(block, "input", None))
        out.append((message.role, blocks))
    return out


async def _run_twice(settings: Settings, *, capture: bool) -> tuple[dict, object]:
    recorder, root = (await _open_recorder(settings)) if capture else (None, "")
    try:
        session = _build_session(_RoundLLM(), settings, context_rounds=1,
                                 channel=recorder.channel(_INST) if capture else None)
        queue = session.subscribe()
        _open_episode(session)
        for index in (0, 1):
            await session.feed_candle(_candle(index))
            await _settle(session)
        events = _drain(queue)
        fingerprint = {
            "events": [(event.get("type"), (event.get("data") or {}).get("action"))
                       for event in events],
            "trades": [(trade.get("side"), trade.get("qty"), trade.get("price"))
                       for trade in session.executor.trades],
            "context": _context_fingerprint(session.decider),
            "summary": session.decider.summary, "projected": session.executor.projected}
        llm = session.decider._llm
    finally:
        if recorder is not None:
            await _cleanup(recorder, root)
    return fingerprint, llm


async def test_s6_capturing_or_not_does_not_change_trading():
    await init_db()
    settings = _settings()
    try:
        plain, plain_llm = await _run_twice(settings, capture=False)
        captured, captured_llm = await _run_twice(settings, capture=True)
    finally:
        await dispose_engine()
    assert captured == plain
    assert not isinstance(plain_llm, RecordingLlmClient)
    assert isinstance(captured_llm, RecordingLlmClient)


def test_s6_a_session_without_capture_keeps_the_null_channel():
    session = _build_session(_RoundLLM(), _settings())
    assert session.trajectory is NULL_TRAJECTORY
    assert not isinstance(session.decider._llm, RecordingLlmClient)


# ---------------- S-7 / G-T1：源码门禁 ----------------
def test_s7_session_never_awaits_the_channel_and_every_channel_method_has_one_guard():
    source = (_APP / "session.py").read_text(encoding="utf-8")
    assert "await self._trajectory" not in source
    for write_point in ("open_episode", "begin_round", "stage_round", "end_round",
                        "fail_round", "release_round", "skip_round", "close_episode"):
        assert source.count(f"self._trajectory.{write_point}(") == 1, write_point
    assert source.count('self._trajectory.sync_fills(') == 2
    tree = ast.parse((_APP / "trajectory" / "channel.py").read_text(encoding="utf-8"))
    cls = next(node for node in tree.body
               if isinstance(node, ast.ClassDef) and node.name == "TrajectoryChannel")
    public = [node for node in cls.body
              if isinstance(node, ast.FunctionDef) and not node.name.startswith("_")]
    assert len(public) >= 13
    for method in public:
        body = [stmt for stmt in method.body
                if not (isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant))]
        assert len(body) == 1 and isinstance(body[0], ast.Try), method.name
        handlers = body[0].handlers
        assert len(handlers) == 1 and isinstance(handlers[0].type, ast.Name), method.name
        assert handlers[0].type.id == "Exception", method.name


def _is_trajectory_module(module: str | None) -> bool:
    return bool(module) and (module == "trajectory" or module.startswith("trajectory.")
                             or ".trajectory" in module)


def test_gt1_no_trajectory_symbol_takes_part_in_a_decision_expression():
    offenders = []
    for name in ("session.py", "manager.py", "decider.py"):
        tree = ast.parse((_APP / name).read_text(encoding="utf-8"))
        imported = {alias.asname or alias.name for node in ast.walk(tree)
                    if isinstance(node, ast.ImportFrom) and _is_trajectory_module(node.module)
                    for alias in node.names}
        for node in ast.walk(tree):
            tests = [node.test] if isinstance(node, (ast.If, ast.While, ast.IfExp)) else []
            tests += [node] if isinstance(node, ast.Compare) else []
            for test in tests:
                used = {sub.id for sub in ast.walk(test) if isinstance(sub, ast.Name)} & imported
                if used:
                    offenders.append((name, node.lineno, sorted(used)))
    assert offenders == []


# ---------------- S-8 / S-9：成交水位 ----------------
async def test_s8_a_trimmed_ledger_writes_a_ledger_gap_and_numbers_by_trade_count():
    recorder, root = await _open_recorder(_settings())
    try:
        executor = _FakeExecutor()
        channel = recorder.channel(_INST)
        channel.bind(SessionBinding("s1", 1, _INST, "1H"), executor=executor)
        channel.open_episode(_facts())
        for index in range(5):
            executor.add(_trade(index))
        del executor.trades[:3]                  # 两次同步之间台账被裁：新增 5 笔只剩 2 笔
        channel.sync_fills("monitor")
        rows = await _close_and_read(recorder)
    finally:
        await _cleanup(recorder, root)
    gaps = [row for row in _of(rows, "gap") if row.get("scope") == "ledger"]
    assert len(gaps) == 1 and (gaps[0]["fromSeq"], gaps[0]["toSeq"]) == (1, 3)
    assert gaps[0]["reason"] == "ledger_trimmed"
    assert [fill["ledgerSeq"] for fill in _of(rows, "fills")[0]["fills"]] == [4, 5]


async def test_s9_cold_resume_catches_up_the_fills_settled_while_offline():
    settings = _settings()
    root = tempfile.mkdtemp(prefix="trj")
    group_id = f"aitradegrp_{uuid.uuid4().hex}"
    second = None
    try:
        first, _ = await _open_recorder(settings, root=root, group_id=group_id)
        channel = first.channel(_INST)
        channel.bind(SessionBinding("s1", 1, _INST, "1H"), executor=_FakeExecutor(trades=3))
        channel.open_episode(_facts())
        await first.aclose("app_quit", compress_active=False, timeout_s=5)
        second, _ = await _open_recorder(settings, root=root, group_id=group_id, resume_of={
            "groupId": group_id, "createdTs": _CREATED_TS, "resumedCount": 0})
        channel = second.channel(_INST)
        channel.bind(SessionBinding("s2", 1, _INST, "1H"), executor=_FakeExecutor(trades=5))
        channel.open_episode(_facts())
        rows = await _close_and_read(second)
        manifest = json.loads((second.group_dir / "manifest.json").read_text("utf-8"))
    finally:
        await _cleanup(second, root)
    assert first.group_dir == second.group_dir, "冷续跑复用同一个分组目录"
    epoch_rows = [row for row in rows if row["epoch"] == second.epoch]
    start = _of(epoch_rows, "episode_start")[0]
    assert start["catchUp"] == "written" and start["ledgerSeqAtOpen"] == 5
    assert start["resumeOf"]["groupId"] == group_id
    catch_up = _of(epoch_rows, "fills")[0]
    assert catch_up["context"] == "offline_catch_up"
    assert [fill["ledgerSeq"] for fill in catch_up["fills"]] == [4, 5]
    assert [epoch["openReason"] for epoch in manifest["epochs"]] == ["start", "cold_resume"]
    assert manifest["members"][_INST]["ledgerSeqCaptured"] == 5
    names = manifest["members"][_INST]["segments"]
    assert len(names) == 2 and names == sorted(names), "跨 epoch 的段文件名字典序即时间序（I-29）"


async def test_s9_catch_up_finds_its_start_by_event_identity_not_by_count():
    """I-30：上一进程采到一笔没落库的成交（水位虚高），离线期又补记了一笔平仓——回灌后 `trade_count`
    恰好等于旧水位。只比计数会把那笔平仓整条漏掉且不留缺口；按事件身份定位找不到 ⇒ 有界回看补上。"""
    settings = _settings()
    root = tempfile.mkdtemp(prefix="trj")
    group_id = f"aitradegrp_{uuid.uuid4().hex}"
    second = None
    try:
        first, _ = await _open_recorder(settings, root=root, group_id=group_id)
        channel = first.channel(_INST)
        executor = _FakeExecutor(trades=2)
        channel.bind(SessionBinding("s1", 1, _INST, "1H"), executor=executor)
        channel.open_episode(_facts())
        executor.add({**_trade(99), "explain": {"meta": {"accountingEventId": "never-persisted"}}})
        channel.sync_fills("monitor")
        await first.aclose("app_quit", compress_active=False, timeout_s=5)
        restored = _FakeExecutor(trades=2)
        restored.add({"side": "close", "qty": 0.01, "price": 30100.0, "pnl": 1.0,
                      "explain": {"meta": {"accountingEventId": "offline-close"}}})
        second, _ = await _open_recorder(settings, root=root, group_id=group_id, resume_of={
            "groupId": group_id, "createdTs": _CREATED_TS, "resumedCount": 0})
        channel = second.channel(_INST)
        channel.bind(SessionBinding("s2", 1, _INST, "1H"), executor=restored)
        channel.open_episode(_facts())
        rows = await _close_and_read(second)
    finally:
        await _cleanup(second, root)
    epoch_rows = [row for row in rows if row["epoch"] == second.epoch]
    assert _of(epoch_rows, "episode_start")[0]["catchUp"] == "written"
    caught = [fill["explain"]["meta"]["accountingEventId"]
              for fill in _of(epoch_rows, "fills")[0]["fills"]]
    assert "offline-close" in caught


# ---------------- S-12：episode 关闭之后的晚到调用 ----------------
async def test_s12_calls_and_fill_syncs_after_stop_are_late_not_gaps():
    await init_db()
    settings = _settings()
    recorder, root = await _open_recorder(settings)
    try:
        session = _build_session(_RoundLLM(action="hold"), settings,
                                 channel=recorder.channel(_INST))
        _open_episode(session)
        await session.stop("user_stop")
        tools = [ToolSchema(DECISION_TOOL_NAME, "d", {"type": "object"})]
        seen = [event async for event in session.decider.llm.stream(
            system="S", messages=[], tools=tools)]
        session._trajectory.sync_fills("monitor")
        counters = recorder.counters_snapshot()
        rows = await _close_and_read(recorder)
    finally:
        await _cleanup(recorder, root)
        await dispose_engine()
    assert seen, "直通：事件照常转发"
    kinds = _kinds(rows)
    assert kinds[-1] == "episode_end" and "llm_call" not in kinds
    assert counters["lateCalls"] == 1 and counters["dropped"] == 0
    assert _of(rows, "gap") == []


# ---------------- W1：真实 start() ----------------
async def test_w1_start_opens_the_episode_after_priming(monkeypatch):
    """冷续跑在 `start()` 之前回灌成交台账：W1 必须以此刻的 `trade_count` 为水位，历史成交不重采。"""
    await init_db()
    monkeypatch.setattr("app.ai_auto.market_window.get_rest_client", lambda: _FakeRest([]))
    monkeypatch.setattr("app.ai_auto.session.OKXWSClient", _FakeWS)
    settings = _settings()
    recorder, root = await _open_recorder(settings)
    try:
        session = _build_session(_RoundLLM(action="hold"), settings,
                                 channel=recorder.channel(_INST))
        for index in range(2):
            session.executor.trades.append(_trade(index))
        session.executor.trade_count = 2
        await session.start()
        session._cancel_task("_monitor_task")
        session._trajectory.sync_fills("monitor")
        rows = await _close_and_read(recorder)
    finally:
        await _cleanup(recorder, root)
        await dispose_engine()
    start = _of(rows, "episode_start")[0]
    assert start["ledgerSeqAtOpen"] == 2 and start["catchUp"] == "none"
    assert start["sessionId"] == "aitrade_traj" and start["accounting"]["costBasis"] in (
        "net", "gross")
    assert _of(rows, "fills") == []
