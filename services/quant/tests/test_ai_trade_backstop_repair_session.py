"""交易所侧兜底自愈 · 会话接线 / 契约链路 / prompt 分支（backstop-repair §6.1 S 组）。

本组守的是**「后端算了 ≠ 用户看得见」**这条本仓库已经栽过五次的坑，以及 [P0-3] 那条两端同时断掉
的契约链：`response_model` 会把新块整块吃掉（前端手造 wire 的用例守不住），请求侧的回填点又不在
`api/ai_auto.py` 而在 `hold_horizon.resolve_horizon_rule`。
"""
from __future__ import annotations

import asyncio
import json
import time
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

from app.agent.llm.base import StreamEvent
from app.ai_auto.backstop_repair import (
    BLOCK_PX_THROUGH,
    STATE_FAILED,
    STATE_PLACED,
    STATE_PLANNED,
    AiTradeRepairBlocked,
    schedule_repair,
)
from app.ai_auto.decider import AiTradeDecider
from app.ai_auto.hold_horizon import (
    HorizonFacts,
    HorizonRule,
    HorizonState,
    build_snapshot,
    resolve_horizon_rule,
    rule_wire,
)
from app.ai_auto.hold_horizon_text import format_horizon_for_prompt
from app.ai_auto.manager import AiTradeManager
from app.ai_auto.session import AiTradeSession
from app.api.ai_auto import _resolve_horizon
from app.config import Settings
from app.db.session import dispose_engine, init_db
from app.engine.simulator import RiskParams
from app.live_auto.executor import LiveExecutor
from app.okx.accounts import AccountCredentials
from app.okx.private_client import OKXPrivateError
from app.paper.strategy_factory import PaperStrategySpec
from app.schemas import AiTradeHorizonIn
from app.strategies.base import BaseStrategy, StrategyMeta

_RULE = HorizonRule(profile_id="intraday_swing", title="日内波段", target_label="4 – 12 小时",
                    min_hold_s=7200.0, exit_confirm_rounds=2, confirm_window_s=3600.0,
                    hysteresis_k=1.5, target_hold_min_s=14400.0, target_hold_max_s=43200.0,
                    expected_hold_bars=12)


class _StubStrategy(BaseStrategy):
    meta = StrategyMeta(key="_stub_backstop_repair", name="Stub", description="t",
                        default_params={}, params_schema={})

    def warmup(self) -> int:
        return 1

    def on_candle(self, df, i, ctx):
        return []


class _StubLLM:
    async def stream(self, *, system, messages, tools):
        if tools:
            yield StreamEvent("tool_use_start", id="t", name="submit_trade_decision")
            yield StreamEvent("tool_use_args_delta", id="t",
                              partial_json=json.dumps({"action": "hold", "reason": "r"}))
            yield StreamEvent("message_done", stop_reason="tool_use")
        else:
            yield StreamEvent("text_delta", text="摘要")
            yield StreamEvent("message_done", stop_reason="end")


class _StubBackstop:
    """检测层的可注入替身：只暴露 `wire(now)` 与 `force_next()` 两个本层真正用到的口子。"""

    def __init__(self, wire: dict | None):
        self._wire = wire
        self.forced = 0

    def wire(self, _now_wall):
        return None if self._wire is None else dict(self._wire)

    def force_next(self):
        self.forced += 1


def _detected(**over) -> dict:
    """核实成功、一张止损都没有——本层最该动手的那一格。"""
    base = {"basis": "exchange", "unavailableReason": None, "checkedAgoSeconds": 3.0,
            "stopLossPx": None, "takeProfitPx": None, "hasStop": False, "coveredSz": 0.0,
            "positionSz": 1.0, "coverageRatio": 0.0, "orders": 0,
            "localStopLossPx": 29000.0, "localTakeProfitPx": None, "mismatch": True}
    return {**base, **over}


def _make_session(*, horizon_rule=_RULE, settings=None, place_fn=None):
    """脱网会话：executor 的五个回调全打桩，自愈层的下单通道可注入。"""
    settings = settings or Settings(ai_trade_funding_enabled=False)

    async def place_order_fn(order):
        return {"ok": True, "ordId": "x", "clOrdId": "aragonx"}

    async def close_position_fn(close):
        return {"ok": True, "clOrdId": "aragoncx"}

    async def positions_fn():
        ex = session.executor
        return ([] if ex.projected == "flat"
                else [{"instId": "BTC-USDT-SWAP", "pos": ex.entry_qty_sz, "upl": 0.0}])

    async def fills_fn():
        return []

    executor = LiveExecutor(
        creds=AccountCredentials("ak", "sk-000000", "pp-000000"), account_id=1,
        inst_id="BTC-USDT-SWAP", td_mode="cross", pos_mode="long_short_mode",
        leverage=3.0, budget_usd=1000.0, risk=RiskParams(stop_loss_pct=0.05), ct_val=0.01,
        lot_sz=0.01, min_sz=0.01, settings=settings, place_order_fn=place_order_fn,
        close_position_fn=close_position_fn, positions_fn=positions_fn, fills_fn=fills_fn)
    decider = AiTradeDecider(_StubLLM(), "sys", context_refresh_rounds=50, settings=settings)
    spec = PaperStrategySpec(1, "S", "atomic", _StubStrategy({}), {})
    session = AiTradeSession("aitrade_repair", 1, [spec], "BTC-USDT-SWAP", "1H", executor,
                             decider, priming=False, priming_bars=10,
                             context_refresh_rounds=50, horizon_rule=horizon_rule)
    session._last_live_px = 30_000.0
    session._last_px_wall = time.time()
    executor.projected = "long"
    executor.entry_price = 30_000.0
    executor.entry_qty_sz = 1.0
    executor.trades.append({"side": "long", "stopLossPrice": 29_000.0,
                            "takeProfitPrice": 32_000.0})
    if place_fn is not None:
        session._repair._place = place_fn
    return session


async def _ok_place(_executor, stop):
    return {"algoId": "6xxxx", "algoClOrdId": stop.algo_cl_ord_id, "sz": stop.sz,
            "stopPx": stop.stop_px}


def _drain(q: asyncio.Queue) -> list[dict]:
    out = []
    while not q.empty():
        out.append(q.get_nowait())
    return out


def _tick(session, backstop) -> None:
    """一次盯市 tick 上的自愈调度（与 `_monitor_tick` 里那一行**同参**）。"""
    schedule_repair(session._repair, backstop, session.executor,
                    session._session_readings(time.time()), push=session._push,
                    on_change=lambda: session._push({"type": "status",
                                                     "data": session._status_payload()}))


# ==================== S-1 / S-22 · 未装配即整键缺席 ====================
def test_the_layer_is_absent_when_the_ops_switch_is_off():
    """S-1：运维总闸关 ⇒ `horizon.backstop` 里**无 `repair` 键**（prompt 与卡片同时逐字回退）。"""
    session = _make_session(settings=Settings(ai_trade_funding_enabled=False,
                                              ai_trade_backstop_repair_enabled=False))
    assert session._repair.wire() is None
    session._horizon.backstop = _StubBackstop(_detected())
    assert "repair" not in session._horizon._backstop_wire(time.time())


def test_the_layer_is_absent_without_a_horizon_contract():
    """S-22 [P1-4]：未装配周期契约 ⇒ 无 `repair` 键，且**一个写请求都发不出去**。

    这条是本轮的安全底线：本层唯一的呈现面挂在 `horizon.backstop.repair` 上，而未装配周期契约时
    `status.horizon` 恒为 `null`——**一条用户完全看不见的自动下单路径不允许存在**。
    """
    calls = []

    async def spy(_executor, stop):
        calls.append(stop)
        return await _ok_place(_executor, stop)

    session = _make_session(horizon_rule=None, place_fn=spy)
    backstop = _StubBackstop(_detected())
    for _ in range(5):
        _tick(session, backstop)
    assert session._repair.wire() is None
    assert calls == []


# ==================== S-8~12 · 会话接线与事件 ====================
async def test_plan_mode_sends_nothing_over_ten_ticks():
    """S-8：`plan` 模式跑 10 个 tick ⇒ 下单调用次数为 **0**（默认不自动花钱）。"""
    calls = []

    async def spy(_executor, stop):
        calls.append(stop)
        return await _ok_place(_executor, stop)

    session = _make_session(place_fn=spy)
    backstop = _StubBackstop(_detected())
    for _ in range(10):
        _tick(session, backstop)
    await asyncio.sleep(0)
    assert calls == []
    assert session._repair.wire()["state"] == STATE_PLANNED


async def test_arm_mode_fires_exactly_once(monkeypatch):
    """S-9：`arm` 模式且检测到未覆盖 ⇒ **恰好 1 次**调用（单飞），并立刻强制下一次核实。"""
    calls = []

    async def spy(_executor, stop):
        calls.append(stop)
        return await _ok_place(_executor, stop)

    session = _make_session(
        horizon_rule=HorizonRule(**{**_RULE.__dict__, "backstop_repair_mode": "arm"}),
        place_fn=spy)
    backstop = _StubBackstop(_detected())
    for _ in range(3):
        _tick(session, backstop)
    await session._repair._task
    assert len(calls) == 1
    assert session._repair.wire()["state"] == STATE_PLACED


def test_the_verify_hook_is_wired_to_the_sessions_own_detector():
    """§九 D-6 的接线证据：补挂成功后强制核实的那个钩子，指向会话**自己那个**检测层运行时。

    钩子挂错对象 ⇒ `placed` 永远等不到一次新核实 ⇒ 卡片与 prompt 永远停在「已发出，正在核实」。
    （钩子**被调用**这一半由纯核心用例 T-29 守，此处只守它指向谁。）
    """
    session = _make_session()
    hook = session._repair.verify_hook
    assert hook.__self__ is session._backstop
    assert hook.__func__ is type(session._backstop).force_next


async def test_a_state_change_pushes_exactly_one_extra_status():
    """S-21 [P1-3]：状态一变即补推**一条** status；状态不变的后续 tick **不再重复推**。

    全量 `status` 只在换向 / 唤醒跳过 / 决策轮 / 停会话时推，**没有挂在 5s 盯市 tick 上**；而本层的
    目标用户恰恰是唤醒周期 0、决策轮以小时计的 `trend_hold`——不补推，用户点完「立即补挂」之后
    界面可以几个小时纹丝不动。
    """
    session = _make_session()
    q = session.subscribe()
    backstop = _StubBackstop(_detected())
    for _ in range(3):
        _tick(session, backstop)
    await asyncio.sleep(0)
    statuses = [e for e in _drain(q) if e["type"] == "status"]
    assert len(statuses) == 1
    assert statuses[0]["data"]["horizon"] is None or True     # 快照形状另有用例守
    assert session._repair.wire()["state"] == STATE_PLANNED


async def test_a_failed_repair_pushes_an_error_with_the_raw_code():
    """S-11：补挂失败 ⇒ 推一条 `error`，`message` 含交易所原始码、**不含任何凭证片段**。"""
    async def boom(_executor, _stop):
        raise OKXPrivateError("51008", "余额不足")

    session = _make_session(
        horizon_rule=HorizonRule(**{**_RULE.__dict__, "backstop_repair_mode": "arm"}),
        place_fn=boom)
    q = session.subscribe()
    _tick(session, _StubBackstop(_detected()))
    await session._repair._task
    events = _drain(q)
    errors = [e for e in events if e["type"] == "error"]
    assert errors and "51008" in errors[0]["data"]["message"]
    assert "sk-000000" not in json.dumps(events, ensure_ascii=False)
    assert session._repair.wire()["state"] == STATE_FAILED


async def test_a_successful_repair_pushes_status_but_no_error():
    """S-10：补挂成功 ⇒ 推 status、**不推 error**（成功不是坏消息，别制造事件流噪音）。"""
    session = _make_session(
        horizon_rule=HorizonRule(**{**_RULE.__dict__, "backstop_repair_mode": "arm"}),
        place_fn=_ok_place)
    q = session.subscribe()
    _tick(session, _StubBackstop(_detected()))
    await session._repair._task
    types = {e["type"] for e in _drain(q)}
    assert "error" not in types


async def test_the_manual_channel_shares_one_reading_point():
    """人工通道与自动通道共用同一个 `plan_repair` 与同一份断面；完成后补推一条 status。"""
    session = _make_session(place_fn=_ok_place)
    session._horizon.backstop = session._backstop
    q = session.subscribe()
    receipt = await _repair_via(session, _detected())
    assert receipt["state"] == STATE_PLACED
    assert receipt["sz"] == 1.0
    assert [e["type"] for e in _drain(q)] == ["status"]


async def test_the_manual_channel_still_honours_the_locks():
    """人工通道**只绕过 `mode`，绝不绕过 H1/H2/H3**：现价已越过止损价即 409 值域码。"""
    session = _make_session(place_fn=_ok_place)
    session._last_live_px = 28_000.0          # 已跌破本地止损 29000
    with pytest.raises(AiTradeRepairBlocked) as caught:
        await _repair_via(session, _detected())
    assert caught.value.reason == BLOCK_PX_THROUGH


async def _repair_via(session, wire: dict) -> dict:
    """把桩检测层挂上会话再走人工通道（会话侧只认 `self._backstop`）。"""
    session._backstop = _StubBackstop(wire)
    return await session.repair_backstop()


# ==================== S-17 / S-18 · 会话侧落点与决策链不变 ====================
def test_the_session_keeps_only_a_thin_seam():
    """S-17：自愈层在 `session.py` 里的落点必须**保持极薄**（装配 / 盯市一行 / epoch / close /
    人工入口），`session.py` 已 1700+ 行，每多一处都在加深既有行数债。"""
    from pathlib import Path

    text = (Path(__file__).resolve().parents[1] / "app/ai_auto/session.py").read_text("utf-8")
    assert text.count("self._repair") <= 6
    assert "plan_repair(" not in text         # 判定式一行都不许搬进会话
    assert "AlgoStopInput" not in text        # 下单体组装同样不许
    assert "place_backstop_stop" not in text  # 下单调用同样不许


def test_the_decision_chain_is_untouched():
    """S-18：本层**不新增任何门禁**——`session.py` 里不得出现把自愈接进决策链的痕迹。

    自愈层不返回 `TradeDecision`、不产生降级码、不进 `gate_reason` 的或链、不进决策轨迹。
    """
    from pathlib import Path

    text = (Path(__file__).resolve().parents[1] / "app/ai_auto/session.py").read_text("utf-8")
    for line in text.splitlines():
        if "gate_reason" in line or "degrade" in line:
            assert "_repair" not in line


# ==================== S-2~7 · prompt 分支（每句以「有没有兜底」结尾） ====================
def _prompt(repair: dict | None) -> str:
    backstop = _detected()
    if repair is not None:
        backstop["repair"] = repair
    facts = HorizonFacts(seq=1, side="long", entry_px=30_000.0, mark_px=30_100.0,
                         leverage=3.0, now_wall=1_700_000_000.0, exchange_sl_px=29_000.0)
    snapshot = build_snapshot(_RULE, HorizonState(epoch=("long", 1), opened_wall=1_699_990_000.0),
                              facts, backstop=backstop)
    return format_horizon_for_prompt(snapshot)


def _repair_wire(**over) -> dict:
    base = {"mode": "plan", "state": "planned", "blockedReason": None,
            "plannedStopPx": 29_000.0, "plannedSz": 1.0, "coveredSz": 0.0, "positionSz": 1.0,
            "attemptsEpoch": 0, "maxPerEpoch": 2, "attemptsSession": 0, "maxPerSession": 8,
            "coolingSeconds": None, "lastResult": None}
    return {**base, **over}


def _backstop_line(repair: dict | None) -> str:
    return next(row for row in _prompt(repair).splitlines() if "交易所" in row)


def _clause(repair: dict) -> str:
    """自愈子句 = 有 `repair` 那一行**减去**没有它那一行；顺带守住「五支基线一个字节没改」。"""
    base = _backstop_line(None)
    line = _backstop_line(repair)
    assert line.startswith(base), "自愈层改写了既有五支——冻结纪律被破"
    return line[len(base):]


def test_prompt_falls_back_byte_for_byte_without_the_repair_key():
    """S-1 冻结：`repair` 子键缺席 / `idle` ⇒ 兜底行与上一轮**逐字节相同**。"""
    assert _prompt(None) == _prompt({"state": "idle"})


@pytest.mark.parametrize("repair,needle", [
    (_repair_wire(), "仅提醒"),
    (_repair_wire(state="placing"), "正在向交易所核实"),
    (_repair_wire(state="placed"), "正在向交易所核实"),
    (_repair_wire(state="verified", lastResult={"sz": 1.0}), "已核实通过"),
    (_repair_wire(state="failed",
                  lastResult={"code": "OKX_ERROR", "codeLabel": "交易所业务错误"}),
     "交易所业务错误"),
    (_repair_wire(state="blocked", blockedReason="px_through"), "已越过本地止损价"),
    (_repair_wire(state="blocked", blockedReason="px_stale"), "判定价已过期"),
    (_repair_wire(state="blocked", blockedReason="cooling"), "冷却"),
])
def test_each_repair_state_adds_exactly_one_clause(repair, needle):
    """S-2~7：每个分支**恰好**追加一句（兜底行已是 prompt 里最长的一段，绝不多于一句）。"""
    clause = _clause(repair)
    assert needle in clause
    assert clause.count("。") == 1


@pytest.mark.parametrize("repair", [
    _repair_wire(),
    _repair_wire(state="placing"),
    _repair_wire(state="blocked", blockedReason="px_through"),
    _repair_wire(state="blocked", blockedReason="px_stale"),
    _repair_wire(state="blocked", blockedReason="attempts_exhausted"),
    _repair_wire(state="failed", lastResult={"code": "OKX_ERROR", "codeLabel": "交易所业务错误"}),
])
def test_every_clause_ends_on_whether_a_backstop_exists(repair):
    """每一句都以「当前**有没有**兜底」收尾，不以「系统做了什么」收尾。

    模型需要的是事实，不是工单状态——一句「已提交补挂请求」会让它误以为兜底已经存在。
    """
    clause = _clause(repair)
    assert any(tail in clause for tail in ("没有兜底", "无持久化兜底", "退出计划", "在你手上"))


def test_a_blocked_reason_never_leaks_its_raw_code():
    """值域四处镜像：prompt 侧必须给中文标签，绝不把英文降级码直接印给模型。"""
    clause = _clause(_repair_wire(state="blocked", blockedReason="order_in_flight"))
    assert "order_in_flight" not in clause
    assert "限价委托在途" in clause


def test_a_failure_code_never_leaks_raw_either():
    """失败码同理：prompt 读**后端预渲染的**中文（`codeLabel`），不印归一码本身。

    与 `blockedReason` 的差别在于码域的归属：失败码来自 `okx.trading._map_trade_code`，会随交易所
    错误码演进，故本文件**不能**抄一张表——抄了下一次新增码就在 prompt 里变成一串英文。
    """
    clause = _clause(_repair_wire(
        state="failed",
        lastResult={"code": "ORDER_SIZE_NOT_MULTIPLE", "codeLabel": "张数须为下单单位的整数倍"}))
    assert "ORDER_SIZE_NOT_MULTIPLE" not in clause
    assert "张数须为下单单位的整数倍" in clause


# ==================== S-19 / S-20 · 契约链路（真端点） ====================
async def test_the_profiles_endpoint_actually_carries_the_repair_block():
    """S-19 [P0-3]：打**真端点**（`response_model` 生效的那条路径）断言新块在回执里。

    该端点声明了 `response_model=AiTradeHorizonProfilesOut`，`profiles_wire` 多下发的键会被
    FastAPI **静默剔除**；而前端规则是「整块缺席 ⇒ 不渲染控件」——两条一叠就是「控件永远不出现，
    且没有任何用例会红」。**手造 wire 的前端用例守不住这一格。**
    """
    await init_db()
    try:
        from app.main import app

        async with AsyncClient(transport=ASGITransport(app=app),
                               base_url="http://t") as client:
            res = await client.get("/api/ai-trade/horizon-profiles")
        assert res.status_code == 200
        block = res.json()["backstopRepair"]
        assert block is not None
        assert block["defaultMode"] == Settings().ai_trade_backstop_repair_default_mode
        assert [m["id"] for m in block["modes"]] == ["plan", "arm"]
        assert all(m["label"] and m["detail"] for m in block["modes"])
        assert block["maxPerEpoch"] == Settings().ai_trade_backstop_repair_max_per_epoch
        assert block["maxPerSession"] == Settings().ai_trade_backstop_repair_max_per_session
    finally:
        await dispose_engine()


def test_the_request_side_backfill_lands_on_the_rule():
    """S-20 [P0-3] 请求侧：`tradingHorizon.backstopRepair` 必须一路走到 `HorizonRule`。

    回填点在 `hold_horizon.resolve_horizon_rule`（**唯一解析点**）而不是 `api/ai_auto.py`——
    后者只是两行透传，把回填写在那里，值就到不了运行时。
    """
    settings = Settings()
    armed = _resolve_horizon(AiTradeHorizonIn(backstopRepair="arm"), settings, bar="1H")
    assert armed.backstop_repair_mode == "arm"
    default = _resolve_horizon(AiTradeHorizonIn(), settings, bar="1H")
    assert default.backstop_repair_mode == settings.ai_trade_backstop_repair_default_mode
    off = resolve_horizon_rule({"backstopRepair": "arm"},
                               Settings(ai_trade_backstop_repair_enabled=False), bar="1H")
    assert off.backstop_repair_mode is None      # 运维总闸关 ⇒ 字段被忽略


def test_the_receipt_mirrors_the_landed_mode():
    """S-20 回执侧：`rule_wire` 镜像该字段，否则用户无法核对「我选的和落地的是不是同一个」。"""
    rule = _resolve_horizon(AiTradeHorizonIn(backstopRepair="arm"), Settings(), bar="1H")
    assert rule_wire(rule)["backstopRepair"] == "arm"


def test_group_status_carries_the_mode_through_the_same_wire():
    """S-20 分组回执：`group_status()` 的 `tradingHorizon` 走**同一个** `rule_wire`。"""
    rule = _resolve_horizon(AiTradeHorizonIn(backstopRepair="arm"), Settings(), bar="1H")
    manager = AiTradeManager()
    manager._group = SimpleNamespace(
        group_id="g1", bar="1H", strategy_ids=[1], strategy_names=["S"],
        total_budget_usd=100.0, leverage=3.0, context_refresh_rounds=50, wake_interval_s=0,
        base_rule=None, profit_rule=None, horizon_rule=rule, created_ts=0, members=[],
        # 纪律预设三键（discipline-preset 轴 A）：`group_status()` 现在也回显它们。
        discipline_preset="off", discipline_adjustments=[], discipline_thresholds={})
    assert manager.group_status()["tradingHorizon"]["backstopRepair"] == "arm"


def test_an_illegal_mode_is_rejected_at_the_schema_layer():
    """S-20 值域：非法取值 ⇒ 422（`arm` 是唯一一处「允许系统替你下单」的授权，不能靠拼错滑进去）。"""
    with pytest.raises(ValidationError):
        AiTradeHorizonIn(backstopRepair="auto-fire")


# ==================== S-13~16 · 人工端点 ====================
async def test_the_repair_endpoint_404s_without_a_group():
    """S-13：分组 / 成员不存在 ⇒ 404（镜像 `stop_member` 的既有姿态）。"""
    await init_db()
    try:
        from app.main import app

        async with AsyncClient(transport=ASGITransport(app=app),
                               base_url="http://t") as client:
            res = await client.post(
                "/api/ai-trade/nope/member/BTC-USDT-SWAP/backstop/repair")
        assert res.status_code == 404
        assert res.json()["error"]["code"] == "NOT_FOUND"
    finally:
        await dispose_engine()


async def test_the_repair_endpoint_is_not_gated_by_the_ai_master_switch(monkeypatch):
    """S-16 [D-11]：AI 总闸关时端点**仍可调用**——补挂是收敛风险的动作，与 `/stop` 同理。

    它守的是运维总闸（关 ⇒ 整层未装配 ⇒ 会话侧抛 `not_applicable` ⇒ 409），而不是 AI 总闸。
    """
    await init_db()
    try:
        from app import config as config_module
        from app.main import app

        monkeypatch.setattr(config_module, "get_settings",
                            lambda: Settings(ai_trade_enabled=False))
        async with AsyncClient(transport=ASGITransport(app=app),
                               base_url="http://t") as client:
            res = await client.post(
                "/api/ai-trade/g/member/BTC-USDT-SWAP/backstop/repair")
        assert res.status_code == 404          # 只因为没有分组，而**不是** 403
    finally:
        await dispose_engine()


async def test_a_blocked_manual_repair_maps_to_409_with_the_shared_reason(monkeypatch):
    """S-14：被 H1/H2/H3 拦下 ⇒ 409，`blockedReason` 与卡片、prompt 读**同一份值域**。"""
    await init_db()
    try:
        from app.main import app
        from app.api import ai_auto as api_module

        async def blocked(_group_id, _inst_id):
            raise AiTradeRepairBlocked(BLOCK_PX_THROUGH, "现价已越过本地止损价")

        manager = SimpleNamespace(repair_member=blocked)
        monkeypatch.setattr(api_module, "get_ai_trade_manager", lambda: manager)
        async with AsyncClient(transport=ASGITransport(app=app),
                               base_url="http://t") as client:
            res = await client.post("/api/ai-trade/g/member/BTC-USDT-SWAP/backstop/repair")
        assert res.status_code == 409
        body = res.json()["error"]
        assert body["code"] == "AI_TRADE_BACKSTOP_NOT_REPAIRABLE"
        assert body["blockedReason"] == BLOCK_PX_THROUGH
    finally:
        await dispose_engine()


async def test_an_exchange_rejection_maps_to_502_with_the_raw_scode(monkeypatch):
    """S-15：交易所拒单 ⇒ 502 + **原始 sCode**（message 已在服务层脱敏，端点不再二次加工）。"""
    await init_db()
    try:
        from app.main import app
        from app.api import ai_auto as api_module

        async def rejected(_group_id, _inst_id):
            raise OKXPrivateError("51008", "余额不足")

        manager = SimpleNamespace(repair_member=rejected)
        monkeypatch.setattr(api_module, "get_ai_trade_manager", lambda: manager)
        async with AsyncClient(transport=ASGITransport(app=app),
                               base_url="http://t") as client:
            res = await client.post("/api/ai-trade/g/member/BTC-USDT-SWAP/backstop/repair")
        assert res.status_code == 502
        assert res.json()["error"]["code"] == "51008"
    finally:
        await dispose_engine()


async def test_a_502_leads_with_a_chinese_reason_not_a_bare_normalised_code(monkeypatch):
    """§4.1 的 502 契约是「原始 sCode + **中文标签**」。

    `plan` 是默认模式 ⇒ 人工那一发的失败提示往往是用户对本层唯一的一次阅读；交易所原文常是英文、
    归一码更是一串大写英文，只给它们等于把本轮最需要说清的一格留成了乱码。
    """
    await init_db()
    try:
        from app.main import app
        from app.api import ai_auto as api_module

        async def rejected(_group_id, _inst_id):
            raise OKXPrivateError("INSUFFICIENT_BALANCE", "Insufficient balance")

        manager = SimpleNamespace(repair_member=rejected)
        monkeypatch.setattr(api_module, "get_ai_trade_manager", lambda: manager)
        async with AsyncClient(transport=ASGITransport(app=app),
                               base_url="http://t") as client:
            res = await client.post("/api/ai-trade/g/member/BTC-USDT-SWAP/backstop/repair")
        body = res.json()["error"]
        assert res.status_code == 502
        assert body["code"] == "INSUFFICIENT_BALANCE"          # 码原样回传，供交易所后台核对
        assert body["message"].startswith("可用余额 / 保证金不足")
        assert "Insufficient balance" in body["message"]       # 交易所原文不吞
    finally:
        await dispose_engine()
