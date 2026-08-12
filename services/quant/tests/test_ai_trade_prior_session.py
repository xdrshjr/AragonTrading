"""先验层的会话级集成测试（§8.1 T-15 / T-16 / T-20；验收门禁 G-1 / G-10）。

脱网、桩回调、桩 LLM。四件在纯函数单测里看不见的事：
1. **零回归**：总闸①关（默认）⇒ payload 与 prompt 与「压根没给快照」时**逐字段 / 逐字节相同**；
2. **装配**：给一份合法快照 ⇒ payload 有键、prompt 带免责、`edge_gate` wire 带 `priorP`；
3. **两条快照通道**：`costSnapshot.priorCoverage` 在门禁生效与空转两条互斥通道上都拿得到（P1-3）；
4. **分组按 `instId` 取快照**（T-20）：成员各拿自己那份——共享一份就是拿 ETH 的统计开 BTC 的仓。
"""
from __future__ import annotations

import asyncio
import json
import time

from app.agent.llm.base import StreamEvent
from app.ai_auto.decider import AiTradeDecider, TradeDecision
from app.ai_auto.prior_runtime import PriorRuntime
from app.ai_auto.prompts import build_decision_prompt
from app.ai_auto.session import AiTradeSession
from app.ai_auto.signals import StrategySignalView
from app.config import Settings
from app.db.session import dispose_engine, init_db
from app.engine.simulator import RiskParams
from app.live_auto.executor import LiveExecutor
from app.live_auto.fee_ledger import build_fee_ledger
from app.okx.accounts import AccountCredentials
from app.paper.strategy_factory import PaperStrategySpec
from app.strategies.base import BaseStrategy, StrategyMeta

# wake-aware-context §2.7：`_build_decision_payload` 的第 5 形参由 `trigger: str` 换成结构化
# `wake: dict`（`wake["trigger"]` 承载原值，wire 逐字不变）。本常量只是把既有调用点的 "candle"
# 平移过来，**断言一字未改**。
_CANDLE_WAKE = {"trigger": "candle", "kind": "candle_close"}

# E-2（discipline-preset-and-integrity D-6）：**绝不写绝对墙钟常量**。夹具 `_snapshot()` 的
# `builtAtTs = _START_TS - 3600` 要通过 `load_snapshot` 的 C-2 新鲜度门（`ai_trade_prior_max_age_s`
# 默认 7 天），而一个写死的时间点会在写死那天起第 8 天开始必红——本文件正是这样红了 17 天，
# 期间先验层的会话级回归护栏完全失效。改成相对当前墙钟（对齐到整根，保持 `_row` 的 ts 相位稳定）。
# `test_prior_fixture_age_within_contract` 是防复发的元用例，改动本常量前先看它。
_STEP = 3600
_START_TS = int(time.time()) // _STEP * _STEP - 3 * _STEP
_PX = 30_000.0


class _StubStrategy(BaseStrategy):
    meta = StrategyMeta(key="_stub_prior", name="Stub", description="t",
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


def _row(idx: int) -> dict:
    """`MarketWindow.prime_append` 吃的是 dict 行（不是 `Candle`）。"""
    return {"ts": _START_TS + idx * _STEP, "o": _PX, "h": _PX + 5, "l": _PX - 5,
            "c": _PX, "vol": 100.0}


def _entry(**over) -> dict:
    base = {"source": "strategy_replay", "event": "stance", "signalKey": None,
            "strategyId": 1, "strategyName": "Stub", "strategyFingerprint": "fp1",
            "direction": "long", "regime": "all", "winRate": 0.6, "meanRetBps": 4.0,
            "avgWinBps": 40.0, "avgLossBps": 30.0, "sampleN": 120, "lossN": 48,
            "segments": 6, "runN": 20, "ir": 1.2, "tStat": 2.9, "usable": True,
            "unusableReason": None, "costBasis": "gross"}
    base.update(over)
    return base


def _snapshot(entries=None, **over) -> dict:
    base = {"version": 1, "instId": "BTC-USDT-SWAP", "bar": "1H",
            "builtAtTs": _START_TS - 3600, "asOfTs": _START_TS - 3600, "horizonBars": 8,
            "costBasis": "gross", "entries": entries or [_entry()]}
    base.update(over)
    return base


def _prior_settings(**over) -> Settings:
    base = {"ai_trade_signal_prior_enabled": True,
            "ai_trade_weighted_consensus_enabled": True,
            "ai_trade_funding_enabled": False}
    base.update(over)
    return Settings(**base)


def _board() -> list[StrategySignalView]:
    return [StrategySignalView(strategy_id=1, name="Stub", stance="long",
                               latest_signal="none", strength=0.0, reason="",
                               risk_params=RiskParams())]


def _make_session(*, settings=None, prior_snapshot=None, bars: int = 3) -> AiTradeSession:
    settings = settings or Settings()

    async def place_order_fn(order):
        return {"ok": True, "ordId": "x", "clOrdId": "aragonx"}

    async def close_position_fn(close):
        return {"ok": True, "clOrdId": "aragoncx"}

    async def positions_fn():
        return []

    async def fills_fn():
        return []

    executor = LiveExecutor(
        creds=AccountCredentials("ak", "sk-000000", "pp-000000"), account_id=1,
        inst_id="BTC-USDT-SWAP", td_mode="cross", pos_mode="long_short_mode",
        leverage=3.0, budget_usd=1000.0, risk=RiskParams(), ct_val=0.01, lot_sz=0.01,
        min_sz=0.01, settings=settings, place_order_fn=place_order_fn,
        close_position_fn=close_position_fn, positions_fn=positions_fn, fills_fn=fills_fn,
        fee_ledger=build_fee_ledger(settings, ct_val=0.01))
    decider = AiTradeDecider(_StubLLM(), "sys", context_refresh_rounds=50, settings=settings)
    spec = PaperStrategySpec(1, "Stub", "atomic", _StubStrategy({}), {})
    session = AiTradeSession("aitrade_prior", 1, [spec], "BTC-USDT-SWAP", "1H", executor,
                             decider, priming=False, priming_bars=10,
                             context_refresh_rounds=50, prior_snapshot=prior_snapshot)
    for idx in range(bars):        # 只种窗口，不触发决策轮（`feed_candle` 才会起轮）
        session._window.prime_append(_row(idx))
    return session


def _payload(session: AiTradeSession) -> dict:
    return session._build_decision_payload(1, _START_TS, _PX, _board(), _CANDLE_WAKE)


# ============================ T-15：零回归（三总闸默认全关）============================
async def test_switch_off_payload_is_field_by_field_identical():
    """总闸①关（默认）+ 给了合法快照 ⇒ payload 与「压根没给快照」时**逐字段相同**。"""
    await init_db()
    try:
        with_snapshot = _payload(_make_session(prior_snapshot=_snapshot()))
        without = _payload(_make_session(prior_snapshot=None))
        assert "signalPriors" not in with_snapshot
        assert with_snapshot == without
        assert build_decision_prompt(with_snapshot) == build_decision_prompt(without)
        assert "信号先验" not in build_decision_prompt(with_snapshot)
    finally:
        await dispose_engine()


async def test_enabled_without_snapshot_is_also_absent():
    """总闸开但 KV 里还没建过快照 ⇒ 同样整键缺席（「还没建」是正常状态，不是故障）。"""
    await init_db()
    try:
        payload = _payload(_make_session(settings=_prior_settings(), prior_snapshot=None))
        assert "signalPriors" not in payload
    finally:
        await dispose_engine()


# ============================ T-16：装配后的 payload / prompt / wire ============================
async def test_enabled_snapshot_lands_in_payload_and_prompt():
    await init_db()
    try:
        session = _make_session(settings=_prior_settings(), prior_snapshot=_snapshot())
        payload = _payload(session)
        priors = payload["signalPriors"]
        assert priors["long"]["p"] == 0.6 and priors["short"] is None
        assert priors["coverage"] == {"boardWithPrior": 1, "boardTotal": 1}
        assert priors["weighted"]["netStance"] == "long"
        prompt = build_decision_prompt(payload)
        assert "历史统计，非当下保证" in prompt          # 渲染硬约束 ①
        assert "做空 无证据" in prompt                    # 无贡献者如实呈现，**不写 p=0.5**
    finally:
        await dispose_engine()


async def test_weighted_consensus_switch_gates_only_the_weighted_key():
    """总闸②独立：关掉它 ⇒ `weighted` 键缺席，但方向先验读数照常（三闸真的互相独立）。"""
    await init_db()
    try:
        session = _make_session(
            settings=_prior_settings(ai_trade_weighted_consensus_enabled=False),
            prior_snapshot=_snapshot())
        priors = _payload(session)["signalPriors"]
        assert "weighted" not in priors and priors["long"]["p"] == 0.6
    finally:
        await dispose_engine()


async def test_edge_gate_wire_carries_prior_readout_with_switch3_off():
    """G-10 的会话级镜像：总闸③关 ⇒ 分子仍取 ATR，但 `priorP` / `priorEvBps` 照发。"""
    await init_db()
    try:
        session = _make_session(settings=_prior_settings(ai_trade_edge_gate_k_min=2.0),
                                prior_snapshot=_snapshot())
        _payload(session)                     # 装配本轮先验视图（含回灌 CostRuntime）
        session._atr_px = 500.0
        out = session._cost.evaluate(
            TradeDecision(action="open_long", size_fraction=0.5, confidence=0.7, reason="r"),
            last_px=_PX, atr_px=500.0, take_profit_pct=None, bar_seconds=3600,
            prior_ev_bps=session._prior.last_edge_bps("open_long"),
            prior_binding=bool(session.executor.settings.ai_trade_edge_prior_enabled))
        assert out.wire["edgeSource"] == "atr"          # ③关 ⇒ 判定逐字段回退
        assert out.wire["priorP"] == 0.6
        assert out.wire["priorEvBps"] is not None
        assert out.wire["priorWouldVeto"] is not None   # 反事实照发
    finally:
        await dispose_engine()


async def test_edge_gate_binds_prior_when_switch3_on():
    await init_db()
    try:
        session = _make_session(
            settings=_prior_settings(ai_trade_edge_gate_k_min=2.0,
                                     ai_trade_edge_prior_enabled=True),
            prior_snapshot=_snapshot())
        _payload(session)
        out = session._cost.evaluate(
            TradeDecision(action="open_long", size_fraction=0.5, confidence=0.7, reason="r"),
            last_px=_PX, atr_px=500.0, take_profit_pct=None, bar_seconds=3600,
            prior_ev_bps=session._prior.last_edge_bps("open_long"), prior_binding=True)
        assert out.wire["edgeSource"] == "prior"
        assert out.wire["edgeBps"] == session._prior.last_edge_bps("open_long")
    finally:
        await dispose_engine()


async def test_prior_coverage_lands_on_both_cost_snapshot_channels():
    """P1-3：`snapshot()`（门禁生效）与 `snapshot_if_due()`（门禁空转）**两条通道**都带覆盖率。"""
    await init_db()
    try:
        session = _make_session(settings=_prior_settings(), prior_snapshot=_snapshot())
        _payload(session)
        assert session._cost.snapshot()["priorCoverage"] == 1.0
        throttled = session._cost.snapshot_if_due(10)
        assert throttled is not None and throttled["priorCoverage"] == 1.0
        assert throttled["priorAgeS"] is not None
    finally:
        await dispose_engine()


async def test_prior_keys_absent_from_cost_snapshot_when_layer_off():
    """先验层未装配 ⇒ 两键**缺席**（不是 None）⇒ 既有 aiStats 形状逐字节不变。"""
    await init_db()
    try:
        session = _make_session()
        _payload(session)
        assert "priorCoverage" not in session._cost.snapshot()
    finally:
        await dispose_engine()


# ============================ 契约降级告警 ============================
def test_stale_snapshot_yields_a_startup_alert():
    runtime = PriorRuntime(_snapshot(builtAtTs=_START_TS - 30 * 86400), _prior_settings())
    assert [a["data"]["code"] for a in runtime.alerts] == ["AI_TRADE_PRIOR_STALE"]
    assert runtime.build(_board(), None, None) is None


def test_fingerprint_mismatch_yields_contract_alert_and_drops_entry():
    raw = {**_snapshot(), "fingerprints": {1: "changed"}}
    runtime = PriorRuntime(raw, _prior_settings())
    assert [a["data"]["code"] for a in runtime.alerts] == ["AI_TRADE_PRIOR_CONTRACT_MISMATCH"]
    assert runtime.build(_board(), None, None) is None


def test_switch_off_loads_nothing_and_alerts_nothing():
    runtime = PriorRuntime(_snapshot(builtAtTs=_START_TS - 30 * 86400), Settings())
    assert runtime.alerts == [] and runtime.build(_board(), None, None) is None


# ============================ T-20：分组按 instId 取快照 ============================
async def test_group_members_get_their_own_instrument_snapshot(monkeypatch):
    """两成员分组各给一份 ⇒ 各拿自己那份；只给 BTC ⇒ ETH 成员 `prior_snapshot is None` 且不报错。"""
    from app.ai_auto import manager as mgr

    captured: dict[str, dict | None] = {}

    class _Recorder:
        def __init__(self, _sid, _run_id, _specs, inst_id, *_a, **kw):
            captured[inst_id] = kw.get("prior_snapshot")

    monkeypatch.setattr(mgr, "AiTradeSession", _Recorder)
    monkeypatch.setattr(mgr, "make_llm_client_from_config", lambda _p: object())
    monkeypatch.setattr(mgr.AiTradeManager, "_build_executor",
                        staticmethod(lambda *a, **k: object()))
    manager = mgr.AiTradeManager()

    async def _fake_run(_ctx, _plan, _tp, _sl):
        return 1

    monkeypatch.setattr(manager, "_create_group_run", _fake_run)
    btc, eth = _snapshot(), _snapshot(instId="ETH-USDT-SWAP")
    req = mgr.AiTradeGroupStartRequest(
        specs=[PaperStrategySpec(1, "Stub", "atomic", _StubStrategy({}), {})],
        members_in=[], bar="1H", account_row=None, settings=Settings(),
        context_refresh_rounds=10,
        prior_snapshots={"BTC-USDT-SWAP": btc, "ETH-USDT-SWAP": eth})
    ctx = mgr._GroupBuildCtx(req=req, creds=None, provider=None, lever=3.0, sum_pct=100.0,
                             total_budget_usd=1000.0, group_id="g", created_ts=0)
    for inst_id in ("BTC-USDT-SWAP", "ETH-USDT-SWAP"):
        await manager._build_member(ctx, mgr._MemberPlan(inst_id, 50.0, 500.0, {}))
    assert captured["BTC-USDT-SWAP"] is btc and captured["ETH-USDT-SWAP"] is eth

    captured.clear()
    req.prior_snapshots = {"BTC-USDT-SWAP": btc}
    for inst_id in ("BTC-USDT-SWAP", "ETH-USDT-SWAP"):
        await manager._build_member(ctx, mgr._MemberPlan(inst_id, 50.0, 500.0, {}))
    assert captured["BTC-USDT-SWAP"] is btc and captured["ETH-USDT-SWAP"] is None


async def test_no_prior_snapshots_at_all_is_not_an_error(monkeypatch):
    from app.ai_auto import manager as mgr

    captured: dict[str, dict | None] = {}

    class _Recorder:
        def __init__(self, _sid, _run_id, _specs, inst_id, *_a, **kw):
            captured[inst_id] = kw.get("prior_snapshot")

    monkeypatch.setattr(mgr, "AiTradeSession", _Recorder)
    monkeypatch.setattr(mgr, "make_llm_client_from_config", lambda _p: object())
    monkeypatch.setattr(mgr.AiTradeManager, "_build_executor",
                        staticmethod(lambda *a, **k: object()))
    manager = mgr.AiTradeManager()

    async def _fake_run(_ctx, _plan, _tp, _sl):
        return 1

    monkeypatch.setattr(manager, "_create_group_run", _fake_run)
    req = mgr.AiTradeGroupStartRequest(
        specs=[PaperStrategySpec(1, "Stub", "atomic", _StubStrategy({}), {})],
        members_in=[], bar="1H", account_row=None, settings=Settings(),
        context_refresh_rounds=10)
    ctx = mgr._GroupBuildCtx(req=req, creds=None, provider=None, lever=3.0, sum_pct=100.0,
                             total_budget_usd=1000.0, group_id="g", created_ts=0)
    await manager._build_member(ctx, mgr._MemberPlan("BTC-USDT-SWAP", 100.0, 1000.0, {}))
    assert captured["BTC-USDT-SWAP"] is None


def test_event_loop_is_not_required_for_pure_runtime():
    """先验层是纯同步的：不起事件循环也能装配（决策轮零 IO 的结构性保证）。"""
    runtime = PriorRuntime(_snapshot(), _prior_settings())
    assert asyncio.get_event_loop_policy() is not None
    wire = runtime.build(_board(), {"primary": "range"}, None)
    assert wire["long"]["p"] == 0.6 and wire["regimeUsed"] == "all"


def test_prior_fixture_age_within_contract():
    """防复发元用例（discipline-preset-and-integrity E-2 / G-8）：夹具快照永远不许陈旧。

    本文件此前用绝对墙钟常量 `_START_TS = 1_785_000_000` 建夹具，`builtAtTs` 一旦超过
    `ai_trade_prior_max_age_s`（默认 7 天）就会被 `load_snapshot` 判 `stale` ⇒ `PriorRuntime`
    整份丢弃 ⇒ 本文件 7 条断言集体转红——这是一枚**墙钟定时炸弹**，它已经让先验层的会话级回归
    护栏静默失效了 17 天。本用例把「夹具必须新鲜」这件事变成显式判据：任何人再把 `_START_TS`
    改回绝对常量，红的是这一条，且它的失败信息直说原因。
    """
    settings = _prior_settings()
    snapshot = _snapshot()
    age_s = time.time() - float(snapshot["builtAtTs"])
    assert 0 <= age_s < settings.ai_trade_prior_max_age_s, (
        f"夹具快照年龄 {age_s:.0f}s 已超出 ai_trade_prior_max_age_s="
        f"{settings.ai_trade_prior_max_age_s}s —— `_START_TS` 必须相对当前墙钟推导，"
        "绝不可写成绝对时间常量（E-2）")
    # 光看年龄还不够：真正要守的是「装配得起来」。跑一遍运行时，条目不许被丢空。
    assert PriorRuntime(snapshot, settings)._entries, "新鲜快照必须能被 load_snapshot 接受"
