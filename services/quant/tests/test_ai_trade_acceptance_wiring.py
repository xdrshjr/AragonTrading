"""持仓周期验收闭环的**接线**集成测试（ai-trade-horizon-acceptance-loop §6.1 W-1~W-13 / G-*）。

纯核心的单测证明「算得对」，本文件证明「算出来的东西真的有人看得见」——本轮 4 个 P0 里有 3 个
同属「后端算了但用户看不见」，那正是这里要守的失败模式。

脱网、桩回调、桩 LLM；不发任何请求。
"""
from __future__ import annotations

import asyncio
import ast
import json
import subprocess
from pathlib import Path

import pytest

from app.ai_auto.cost_runtime import CostRuntime
from app.ai_auto.decider import AiTradeDecider
from app.ai_auto.exit_attribution import classify_live
from app.ai_auto.funding_ledger import FundingLedger
from app.ai_auto.hold_horizon import HorizonRule, rule_from_wire, rule_wire
from app.ai_auto.horizon_acceptance import (
    ai_stats_keys,
    build_bundle,
    input_from_wire,
    merge_bundle,
)
from app.ai_auto.horizon_runtime import HorizonRuntime
from app.config import Settings
from app.db.session import dispose_engine, init_db
from app.engine.simulator import RiskParams
from app.live_auto.executor import LiveExecutor
from app.live_auto.explain import build_atomic_explain, build_external_close_explain
from app.okx.accounts import AccountCredentials
from app.paper.strategy_factory import PaperStrategySpec
from app.strategies.base import BaseStrategy, StrategyMeta

_REPO_ROOT = Path(__file__).resolve().parents[3]
_RULE = HorizonRule(profile_id="intraday_swing", title="日内波段",
                    min_hold_s=7200.0, exit_confirm_rounds=2, confirm_window_s=3600.0,
                    hysteresis_k=1.5, target_hold_min_s=14400.0,
                    target_hold_max_s=43200.0, expected_hold_bars=12)


class _StubStrategy(BaseStrategy):
    meta = StrategyMeta(key="_stub_acceptance", name="Stub", description="t",
                        default_params={}, params_schema={})

    def warmup(self) -> int:
        return 1

    def on_candle(self, df, i, ctx):
        return []


def _make_session(*, horizon_rule=_RULE, settings=None, run_id=1):
    from app.ai_auto.session import AiTradeSession

    settings = settings or Settings(ai_trade_funding_enabled=False)

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
        leverage=3.0, budget_usd=1000.0, risk=RiskParams(stop_loss_pct=0.05),
        ct_val=0.01, lot_sz=0.01, min_sz=0.01, settings=settings,
        place_order_fn=place_order_fn, close_position_fn=close_position_fn,
        positions_fn=positions_fn, fills_fn=fills_fn)
    decider = AiTradeDecider(None, "sys", context_refresh_rounds=50, settings=settings)
    spec = PaperStrategySpec(1, "S", "atomic", _StubStrategy({}), {})
    return AiTradeSession("aitrade_acc", run_id, [spec], "BTC-USDT-SWAP", "1H",
                          executor, decider, priming=False, priming_bars=10,
                          context_refresh_rounds=50, horizon_rule=horizon_rule)


def _trade(side: str, *, pnl: float = 0.0, fee: float = 0.1, explain=None) -> dict:
    return {"ts": 1, "side": side, "price": 30000.0, "qty": 1.0, "fee": fee,
            "pnl": pnl, "explain": explain or build_atomic_explain(side, "r")}


async def _dispatch(session, trades: list[dict]) -> None:
    await session._dispatch([{"type": "trade", "data": t} for t in trades])


# ---------------- W-1 / W-2 / W-3：成交收口与打标 ----------------
@pytest.mark.asyncio
async def test_open_and_close_build_and_settle_a_funding_epoch():
    """W-1：一次开仓 + 一次平仓 ⇒ 资金费 epoch 建立与结转各一次。"""
    await init_db()
    try:
        session = _make_session()
        ledger = session._cost.funding_ledger
        ledger.enabled = True
        await _dispatch(session, [_trade("long")])
        assert ledger.epoch is not None and ledger.epoch.side == "long"
        await _dispatch(session, [_trade("close", pnl=1.0)])
        assert ledger.epoch is None
    finally:
        await dispose_engine()


@pytest.mark.asyncio
async def test_exit_source_is_stamped_only_on_close_legs():
    """W-2：只有平仓成交带 `exitSource`——开仓腿没有「退出来源」可言。"""
    await init_db()
    try:
        session = _make_session()
        opened = _trade("long")
        closed = _trade("close", pnl=1.0)
        await _dispatch(session, [opened, closed])
        assert "exitSource" not in opened["explain"]["meta"]
        assert closed["explain"]["meta"]["exitSource"] == "agent"
    finally:
        await dispose_engine()


@pytest.mark.asyncio
async def test_exit_attribution_switch_off_leaves_meta_byte_identical():
    """W-3（G-1）：总闸关 ⇒ `meta` 与改动前逐键相等（整键缺席，不是 None）。"""
    await init_db()
    try:
        off = _make_session(settings=Settings(ai_trade_funding_enabled=False,
                                              ai_trade_exit_attribution_enabled=False))
        closed = _trade("close", pnl=1.0)
        await _dispatch(off, [closed])
        assert "exitSource" not in closed["explain"]["meta"]
    finally:
        await dispose_engine()


@pytest.mark.asyncio
async def test_oco_close_is_attributed_to_the_exchange_not_the_agent():
    """打标必须走机器可读键：交易所 OCO 平仓不得被记成 Agent 主动平仓。"""
    await init_db()
    try:
        session = _make_session()
        explain = build_external_close_explain(pos_side="long", source="oco",
                                               entry_price=1.0, exit_price=2.0, pnl=1.0)
        closed = _trade("close", pnl=1.0, explain=explain)
        await _dispatch(session, [closed])
        assert closed["explain"]["meta"]["exitSource"] == "exchangeOco"
    finally:
        await dispose_engine()


# ---------------- W-10 [P0-3]：成员级实时通路 ----------------
@pytest.mark.asyncio
async def test_status_payload_always_carries_the_four_acceptance_keys():
    """W-10：三键（+ 原始计数器）**恒存在**——`?? null` 的前端写法要求键恒在，才能把
    「本轮没数据」与「后端还没上线这个键」区分开。"""
    await init_db()
    try:
        session = _make_session()
        payload = session._status_payload()
        for key in ("acceptance", "exits", "funding", "acceptanceInput"):
            assert key in payload, key
        assert payload["acceptance"] is None       # 零样本 ⇒ None 而非缺席
        assert json.dumps(payload)                 # 必须 JSON 安全（它走 WS）
    finally:
        await dispose_engine()


@pytest.mark.asyncio
async def test_status_payload_updates_after_a_round_trip():
    """[P0-3] 的实质：跑出一笔往返之后，status 里的读数**真的会变**。"""
    await init_db()
    try:
        session = _make_session()
        await _dispatch(session, [_trade("long"),
                                  _trade("close", pnl=1.0, fee=0.2)])
        payload = session._status_payload()
        assert payload["exits"]["totalExits"] == 1
        assert payload["acceptanceInput"]["roundTrips"] >= 0
    finally:
        await dispose_engine()


# ---------------- W-9 [P0-4]：顶层键与镜像同源 ----------------
@pytest.mark.asyncio
async def test_top_level_exits_and_the_mirror_are_the_same_object():
    """W-9：`exits` 与 `acceptance.exitMix` 序列化后逐键相等（同一对象，不得二次装配）。"""
    await init_db()
    try:
        session = _make_session()
        for _ in range(3):
            await _dispatch(session, [_trade("long"), _trade("close", pnl=1.0)])
        bundle = session.acceptance_bundle()
        assert bundle["acceptance"] is not None
        assert bundle["acceptance"]["exitMix"] is bundle["exits"]
        assert bundle["acceptance"]["funding"] is bundle["funding"]
    finally:
        await dispose_engine()


@pytest.mark.asyncio
async def test_control_arm_without_horizon_rule_still_reports_exits():
    """W-9 后半 / [P0-4]：`horizon_rule=None` ⇒ 卡片为 None，**但退出分布非空**。"""
    await init_db()
    try:
        session = _make_session(horizon_rule=None)
        await _dispatch(session, [_trade("long"), _trade("close", pnl=1.0)])
        bundle = session.acceptance_bundle()
        assert bundle["acceptance"] is None
        assert bundle["exits"]["totalExits"] == 1
    finally:
        await dispose_engine()


@pytest.mark.asyncio
async def test_custom_all_defaults_assembles_status_without_raising():
    """放行条件 2 [P0-2]：`custom` 全默认（`target_hold_min_s == 0`）跑通一轮完整 status 装配
    **不抛异常**，验收卡三行 `insufficient`、整卡非 `pass`。

    `custom` 全默认是文档化的安全退出口；v1 的公式会在第 3 行 `86400 / 0` 当场 `ZeroDivisionError`
    ——一个观测层的异常会打断整条 status 装配，把「看不到卡片」升级成「整个 status 事件发不出去」。
    """
    await init_db()
    try:
        session = _make_session(horizon_rule=HorizonRule(profile_id="custom", title="自定义"))
        for _ in range(3):
            await _dispatch(session, [_trade("long"), _trade("close", pnl=1.0)])
        payload = session._status_payload()          # 不抛即为通过的一半
        assert json.dumps(payload)
        card = payload["acceptance"]
        assert card is not None
        rows = {row["key"]: row["verdict"] for row in card["rows"]}
        assert rows["avgHold"] == "insufficient"
        assert rows["withinTargetRate"] == "insufficient"
        assert rows["turnoverPerDay"] == "insufficient"
        assert card["verdict"] != "pass"
    finally:
        await dispose_engine()


@pytest.mark.asyncio
async def test_all_switches_off_leaves_the_prompt_cost_section_byte_identical():
    """G-1：三个总闸全关 ⇒ 决策 payload 无 `fundingAccrued` 键 ⇒ 成本段逐字节回退。

    「整键缺席 ⇒ 整段省略 ⇒ 逐字节回退」是本仓库既定的零回归范式；只要键还在（哪怕值是 None），
    渲染管线就会多出一行，冻结断言当场失败。
    """
    from app.ai_auto.prompts import _format_cost

    await init_db()
    try:
        off = _make_session(settings=Settings(
            ai_trade_funding_enabled=False, ai_trade_funding_ledger_enabled=False,
            ai_trade_exit_attribution_enabled=False, ai_trade_acceptance_enabled=False))
        view = off._cost.cost_view(bar_seconds=3600, position={"side": "long"})
        assert view is None or "fundingAccrued" not in view
        assert _format_cost(view) == _format_cost(
            None if view is None else {k: v for k, v in view.items()
                                       if k != "fundingAccrued"})
        assert off.acceptance_bundle()["acceptance"] is None
    finally:
        await dispose_engine()


# ---------------- W-4：分组合并 ----------------
def test_group_merge_sums_round_trips_across_members():
    """W-4：双成员分组的 `acceptance.roundTrips` 为两者之和（合并计数器后判定一次）。"""

    class _Settings(Settings):
        pass

    settings = Settings()
    parts = {"roundTrips": 15, "holdSecondsSum": 15 * 20 * 3600.0, "holdSamples": 15,
             "withinTargetSamples": 15}

    class _Horizon:
        rule = HorizonRule(profile_id="short_swing", title="短波段",
                           target_hold_min_s=12 * 3600.0, target_hold_max_s=48 * 3600.0)
        exits = None

        def acceptance_input_parts(self):
            return dict(parts)

        def exit_stats(self):
            return None

    class _Cost:
        def lifetime_cost(self):
            return {"roundTrips": 15, "grossPnlUsd": 10.0, "feeUsd": 1.0,
                    "netPnlUsd": 9.0, "absGrossPnlUsd": 10.0}

        def funding_stats(self):
            return {"basis": "accrued", "fundingCostUsd": 0.5, "enabled": True}

    bundles = [build_bundle(_Horizon(), _Cost(), settings=settings,
                            wall_seconds=30 * 86400.0) for _ in range(2)]
    inputs = [input_from_wire(b["acceptanceInput"]) for b in bundles]
    merged = merge_bundle(inputs, rule=_Horizon.rule, settings=settings,
                          fundings=[b["funding"] for b in bundles])
    assert merged["acceptance"]["roundTrips"] == 30


def test_group_merge_of_nothing_yields_null_keys():
    """无任何成员输入 ⇒ 三键 None（不拿空数据冒充一张卡）。"""
    merged = merge_bundle([], rule=_RULE, settings=Settings(), fundings=[])
    assert merged == {"acceptance": None, "exits": None, "funding": None,
                      "acceptanceInput": None}


# ---------------- W-5：两世界同形 ----------------
def test_ai_stats_keys_shape_is_identical_across_worlds():
    """W-5：回测 `_aggregate_stats` 与实盘 `_finalize_run` 用的是同一个改名函数、同四个键。"""
    keys = ai_stats_keys({"acceptance": None, "exits": None, "funding": None,
                          "acceptanceInput": None})
    assert sorted(keys) == ["acceptanceInput", "exits", "funding", "horizonAcceptance"]


# ---------------- W-8：既有调用点零回归 ----------------
def test_schedule_maintenance_without_mark_px_does_not_accrue_or_raise():
    """W-8：不传 `mark_px` ⇒ 不计提且不抛（既有调用点零回归）。"""
    runtime = CostRuntime("BTC-USDT-SWAP", Settings(), None, None)
    runtime.funding_ledger.enabled = True
    runtime.funding_ledger.note_open({"side": "long", "qty": 1.0}, seq=1, now_wall=0.0)

    async def _run():
        runtime.schedule_maintenance(1000.0, window_start_ts=0, trades=[])
        await runtime.drain_maintenance()

    asyncio.run(_run())
    assert runtime.funding_ledger.crossings == 0


# ---------------- W-13 [P1-7]：新总闸并进析取式 ----------------
def test_funding_ledger_switch_keeps_the_rate_cache_alive():
    """W-13：成本意识关 + 门禁空转 + 账本开 ⇒ `FundingCache` 仍启用。

    不并进这条析取式的话，运维关掉轴 D 的成本意识就会把资金费的**数据源**一起静默关死，
    而验收卡只会说「口径不可得」，说不出是被谁关的。
    """
    settings = Settings(ai_trade_cost_awareness_enabled=False,
                        ai_trade_edge_gate_k_min=0.0,
                        ai_trade_funding_enabled=True,
                        ai_trade_funding_ledger_enabled=True)
    runtime = CostRuntime("BTC-USDT-SWAP", settings, None, None)
    assert runtime.funding.enabled is True
    assert runtime.funding_ledger.enabled is True


def test_funding_master_switch_off_disables_the_ledger():
    settings = Settings(ai_trade_funding_ledger_enabled=False)
    runtime = CostRuntime("BTC-USDT-SWAP", settings, None, None)
    assert runtime.funding_ledger.enabled is False
    assert runtime.funding_stats() is None


def test_settled_funding_gate_names_the_door_that_failed():
    """[P1-7]：三道门各自的原因必须能说出来（模拟盘 / 对账关 / 未跑过 / 截断）。"""
    disabled = CostRuntime("BTC-USDT-SWAP",
                           Settings(ai_trade_reconcile_enabled=False), None, None)
    assert disabled._settled_funding_usd() == (None, "disabled")

    no_creds = CostRuntime("BTC-USDT-SWAP", Settings(), None, None)
    assert no_creds._settled_funding_usd() == (None, "no_creds")

    with_creds = CostRuntime("BTC-USDT-SWAP", Settings(), None,
                             AccountCredentials("a", "b", "c"))
    assert with_creds._settled_funding_usd() == (None, "not_run")
    with_creds.last_reconcile = {"status": "ok", "coverageMismatch": False,
                                 "samples": 120, "exchangeFundingFeeUsd": 1.0}
    assert with_creds._settled_funding_usd() == (None, "truncated")
    with_creds.last_reconcile = {"status": "ok", "coverageMismatch": True,
                                 "samples": 5, "exchangeFundingFeeUsd": 1.0}
    assert with_creds._settled_funding_usd() == (None, "untrusted")
    with_creds.last_reconcile = {"status": "ok", "coverageMismatch": False,
                                 "samples": 5, "exchangeFundingFeeUsd": 1.25}
    assert with_creds._settled_funding_usd() == (1.25, None)


# ---------------- 退出账本恒构造（[P0-4] 运行时层）----------------
def test_horizon_runtime_records_exits_even_without_a_rule():
    """未装配周期模式的会话上，`note_close` 仍记退出样本（`hold_seconds` 为 None）。"""
    runtime = HorizonRuntime(None)
    runtime.note_close(_trade("close", pnl=2.0), 100.0)
    stats = runtime.exit_stats()
    assert stats["totalExits"] == 1
    assert stats["bySource"][0]["avgHoldSeconds"] is None
    assert runtime.acceptance_input_parts() is None


def test_horizon_runtime_backtest_mode_does_not_double_record():
    """回测由 runner 自己记退出（不受周期模式门控）⇒ 运行时侧必须让位，避免重复计数。"""
    runtime = HorizonRuntime(_RULE, record_exits=False)
    runtime.note_open({"side": "long"}, 0.0, seq=1)
    runtime.note_close(_trade("close", pnl=1.0), 100.0)
    assert runtime.exit_stats() is None
    assert runtime.acceptance_input_parts()["roundTrips"] == 1


def test_acceptance_input_parts_are_raw_not_rounded():
    """[P1-2]：`stats()` 是展示口径（round），`acceptance_input_parts()` 是原始计数器。"""
    runtime = HorizonRuntime(_RULE)
    runtime.note_open({"side": "long"}, 0.0, seq=1)
    runtime.note_close(_trade("close"), 10.0 / 3)
    parts = runtime.acceptance_input_parts()
    assert parts["holdSecondsSum"] == 10.0 / 3
    assert runtime.stats()["avgHoldSeconds"] == round(10.0 / 3, 3)


# ---------------- W-7 / G-4 / G-5 / G-6：冻结门禁 ----------------
def test_external_close_explain_is_byte_identical_apart_from_source():
    """W-7：`live_auto` 共用文件只多一个顶层加法键，其余逐键相等。"""
    explain = build_external_close_explain(pos_side="long", source="oco",
                                           entry_price=1.0, exit_price=2.0, pnl=1.0)
    without = {k: v for k, v in explain.items() if k != "source"}
    assert sorted(without) == ["headline", "kind", "metrics", "schema", "side", "summary"]
    assert classify_live(explain) == "exchangeOco"


def _git_diff_names() -> set[str]:
    out = subprocess.run(["git", "diff", "--name-only", "HEAD"], cwd=_REPO_ROOT,
                         capture_output=True, text=True, check=False)
    return {line.strip() for line in out.stdout.splitlines() if line.strip()}


def test_shared_execution_files_have_zero_diff():
    """G-4 / G-6：`SimBroker` / `cost_model` / `cost_text` 零改动。

    `SimBroker` 被全仓库所有回测与两条自动交易链路共用；后两者是 [P0-1] 的边界——结算周期未知
    这条信息只被 `FundingLedger` 消费，成本层继续走它自己的 8h 保守估算（§九 D-9）。

    **`live_auto/executor.py` 已从本冻结集移出**（ai-trade-discipline-preset-and-integrity 轴 D，
    spec §4.1 显式列出该文件）：本轮在 `_make_trade` 这个**唯一成交构造点**做 bar 对齐，修的正是
    「非 K 线唤醒产生的成交落不到任何一根 K 线上」（D-3）。改一个共用文件必须有等价的护栏顶上，
    否则移出冻结集就是悄悄放宽：`live_auto` 那条路径的零回归改由
    `test_ai_trade_switches_never_leak_into_non_ai_executor` 的**键集冻结** +
    `test_ai_trade_trade_clock.py::test_live_auto_without_bar_seconds_falls_back_verbatim`
    的**取值冻结**共同承担，两条都直接断言行为而不是断言「文件没被碰过」。
    """
    frozen = {"services/quant/app/engine/simulator.py",
              "services/quant/app/ai_auto/cost_model.py",
              "services/quant/app/ai_auto/cost_text.py"}
    changed = _git_diff_names()
    if not changed:
        pytest.skip("git diff 不可用（非仓库环境）")
    assert frozen & changed == set()


def test_backtest_cost_model_funding_args_come_from_one_source():
    """G-5 **冻结方向反转**（ai-trade-backstop-repair-and-net-backtest 轴 B）。

    本轮之前这条用例冻结的是「两处成本估算现场**保持** `funding=None, funding_enabled=False`」，
    理由是：那时修它们会当场改变 G3 迟滞与 edge 门禁在回测里的判定，与该轮「不动任何既有门禁
    判定式」的边界正面冲突（登记为欠账 D-9）。轴 B 的边界不同——它**就是**来还这笔账的，方向是
    让回测向实盘收敛（回测按「资金费为 0」判定、实盘按真实资金费判定，模式越长回测越乐观）。

    故本用例改为冻结新契约，守的东西一个没少：两处判定现场的资金费入参必须来自**同一个**
    `_cost_funding`（不得出现第二条估算路径），且不得再退回任何字面量。仍按 AST 断言，不靠肉眼。
    """
    source = (_REPO_ROOT / "services/quant/app/ai_auto/backtest.py").read_text("utf-8")
    tree = ast.parse(source)
    by_callee: dict[str, list[dict]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) in (
                "query_for_preview", "evaluate_edge_gate"):
            by_callee.setdefault(node.func.id, []).append(
                {kw.arg: kw.value for kw in node.keywords})
    # 现场一：G3 换手迟滞的往返成本估算（`_horizon_facts`）——两个入参都是 `_cost_funding`
    # 解包出来的名字（另一处 `query_for_preview` 是 notes 的口径披露，不是判定现场）。
    judged = [kw for kw in (by_callee.get("query_for_preview") or [])
              if isinstance(kw.get("funding"), ast.Name)]
    assert len(judged) == 1
    assert isinstance(judged[0]["funding_enabled"], ast.Name)
    # 现场二：最小 edge 门禁（`_run_decision`）。
    gate = by_callee.get("evaluate_edge_gate") or []
    assert len(gate) == 1
    assert isinstance(gate[0]["funding"], ast.Name)
    assert isinstance(gate[0]["funding_enabled"], ast.Name)
    # 唯一真源：判定现场恰好各调一次 `_cost_funding`，多出来的就是第二条估算路径。
    sources = [n for n in ast.walk(tree)
               if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
               and n.func.attr == "_cost_funding"]
    assert len(sources) == 2


def test_no_new_self_settings_in_session():
    """[P1-11]：会话内读配置一律 `self.executor.settings`——`AiTradeSession` 上没有
    `self.settings`，写错会在第一笔平仓成交上 AttributeError（那行跑在成交收口里）。"""
    source = (_REPO_ROOT / "services/quant/app/ai_auto/session.py").read_text("utf-8")
    assert "self.settings" not in source


# ---------------- 历史契约复原 ----------------
def test_horizon_rule_survives_a_wire_round_trip_for_history_merge():
    """[P1-9]：历史详情按 `config_json.tradingHorizon` 复原契约后才谈得上判定。"""
    restored = rule_from_wire(rule_wire(_RULE))
    assert restored is not None
    assert restored.target_hold_min_s == _RULE.target_hold_min_s
    assert restored.target_hold_max_s == _RULE.target_hold_max_s
    assert rule_from_wire(None) is None


# ---------------- W-12 [P1-9]：历史·分组详情在后端合并成一张卡 ----------------
async def _seed_history(metrics_by_member: list[dict | None]) -> str:
    """直插两条 run（同一个 group）；`metrics` 为 None 即模拟本特性上线前的老记录。"""
    from sqlalchemy import delete, select

    from app.db.models import Strategy, StrategyRun, Trade
    from app.db.session import get_sessionmaker

    await init_db()
    group_id = "aiacc_merge"
    async with get_sessionmaker()() as s:
        run_ids = (await s.execute(select(StrategyRun.id).where(
            StrategyRun.group_id == group_id))).scalars().all()
        if run_ids:
            await s.execute(delete(Trade).where(Trade.run_id.in_(run_ids)))
            await s.execute(delete(StrategyRun).where(StrategyRun.id.in_(run_ids)))
        strategy_id = (await s.execute(
            select(Strategy.id).order_by(Strategy.id.asc()).limit(1))).scalar_one()
        for idx, metrics in enumerate(metrics_by_member):
            s.add(StrategyRun(
                strategy_id=strategy_id, inst_id=f"X{idx}-USDT-SWAP", bar="1H",
                mode="ai-trade", status="stopped", start_ts=1, end_ts=2,
                config_json=json.dumps({"budgetUsd": 100.0, "allocationPct": 50.0,
                                        "strategyNames": ["S"], "groupId": group_id,
                                        "totalBudgetUsd": 200.0,
                                        "tradingHorizon": rule_wire(_RULE)}),
                metrics_json=None if metrics is None else json.dumps(metrics),
                group_id=group_id, group_started_ts=1,
                group_total_budget_usd=200.0))
        await s.commit()
    return group_id


def _member_metrics(round_trips: int) -> dict:
    hold_sum = round_trips * 20 * 3600.0
    return {"aiStats": {"acceptanceInput": {
        "roundTrips": round_trips, "holdSecondsSum": hold_sum,
        "holdSamples": round_trips, "withinTargetSamples": round_trips,
        "truncatedSamples": 0, "grossPnlUsd": 10.0, "feeUsd": 1.0, "netPnlUsd": 9.0,
        "absGrossPnlUsd": 10.0, "fundingCostUsd": 0.5, "fundingBasis": "accrued",
        "wallSeconds": 30 * 86400.0,
        "exitCounts": {"agent": round_trips}, "exitNetUsd": {"agent": 9.0},
        "exitHoldSum": {"agent": hold_sum}, "exitHoldSamples": {"agent": round_trips},
        "exitWins": {"agent": round_trips}}}}


@pytest.mark.asyncio
async def test_group_history_detail_merges_members_into_one_card():
    """W-12：双 run 的 `summary.horizonAcceptance.roundTrips` 为两者之和（**一张**卡）。"""
    from app.db.ai_auto_repositories import get_ai_trade_group_detail
    from app.db.session import get_sessionmaker

    try:
        group_id = await _seed_history([_member_metrics(12), _member_metrics(13)])
        async with get_sessionmaker()() as s:
            detail = await get_ai_trade_group_detail(s, group_id)
        assert detail["summary"]["horizonAcceptance"]["roundTrips"] == 25
        assert detail["summary"]["exits"]["totalExits"] == 25
    finally:
        await dispose_engine()


@pytest.mark.asyncio
async def test_group_history_detail_drops_the_card_when_a_member_lacks_input():
    """W-12 后半：任一成员缺 `acceptanceInput`（老记录）⇒ 整键缺席。

    绝不用部分成员的数据冒充整组结论——那比不显示更危险。
    """
    from app.db.ai_auto_repositories import get_ai_trade_group_detail
    from app.db.session import get_sessionmaker

    try:
        group_id = await _seed_history([_member_metrics(12), None])
        async with get_sessionmaker()() as s:
            detail = await get_ai_trade_group_detail(s, group_id)
        assert "horizonAcceptance" not in detail["summary"]
        assert "exits" not in detail["summary"]
    finally:
        await dispose_engine()


def test_funding_ledger_ct_val_is_taken_from_the_fee_ledger():
    """名义额 = 张数 × ctVal × 价：漏掉合约乘数会让资金费差一个量级。"""
    class _Fee:
        ct_val = 0.01

    runtime = CostRuntime("BTC-USDT-SWAP", Settings(), _Fee(), None)
    assert runtime.funding_ledger.ct_val == 0.01
    assert isinstance(runtime.funding_ledger, FundingLedger)
