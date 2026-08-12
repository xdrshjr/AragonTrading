"""成本感知的止损下界（ai-trade-tradability-and-open-funnel 轴 B · G-6~G-9 / G-19c）。

要治的缺陷（D-2）：`resolve_stop_distance` 合入前**只有上界夹紧、全函数没有任何下界**。实测 BTC
1m 上 `2×ATR` 的止损距离中位 11.2 bps，而一笔市价往返成本 13.0 bps ⇒ **62.3% 的止损被打掉时亏的
价格幅度还不如手续费多**，那是一台结构性的手续费机器。
"""
from __future__ import annotations

import ast
import math
import pathlib
from types import SimpleNamespace

from app.ai_auto.decider import TradeDecision
from app.ai_auto.sizing import StopRule, parse_stop_rule, resolve_stop_distance
from app.ai_auto.sizing_runtime import SizingContext, SizingRuntime
from app.ai_auto.sizing_text import format_sizing_charter
from app.config import Settings
from app.engine.simulator import RiskParams

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]

# BTC-USDT-SWAP 1m 的**本轮实测**断面（附录 A）：ATR(14) 中位 5.6 bps、市价往返成本 13.0 bps。
# `2×ATR = 11.2 bps < 13.0 bps` —— 合入前的止损就挂在成本带以内。
_BTC_1M_ATR_BPS = 5.6
_MARKET_COST_BPS = 13.0
_REF_PX = 100_000.0


def _rule(**over) -> StopRule:
    base = dict(stop_atr_mult=2.0, stop_rr=2.0, max_sl_pct=0.20, max_tp_pct=0.50,
                cost_floor_mult=2.0)
    base.update(over)
    return StopRule(**base)


def _atr_px(atr_bps: float) -> float:
    return atr_bps / 1e4 * _REF_PX


# ---------- G-6：下界三分支 ----------
def test_floor_not_bound_when_atr_is_already_wide():
    """ATR 宽 ⇒ 下界不绑定 ⇒ `source == "atr"`，与合入前逐字段相同。"""
    plan, skip = resolve_stop_distance(atr_px=_atr_px(60.0), ref_px=_REF_PX, rule=_rule(),
                                       cost_bps=_MARKET_COST_BPS)
    assert skip is None and plan is not None
    assert plan.source == "atr"
    assert math.isclose(plan.stop_bps, 120.0, rel_tol=1e-9)


def test_floor_binds_on_the_measured_btc_1m_cross_section():
    """G-9：合入前 11.2 bps（< 13 bps 成本）⇒ 合入后必须 ≥ 26 bps（= 2 × 13）。

    数字来自本轮用用户本机 `candles_cache` 的实测（附录 A · BTC-USDT-SWAP 1m）。
    """
    before, _ = resolve_stop_distance(atr_px=_atr_px(_BTC_1M_ATR_BPS), ref_px=_REF_PX,
                                      rule=_rule(cost_floor_mult=0.0), cost_bps=_MARKET_COST_BPS)
    assert before is not None
    assert math.isclose(before.stop_bps, 11.2, rel_tol=1e-6)
    assert before.stop_bps < _MARKET_COST_BPS          # 亏的幅度还不如手续费多

    after, skip = resolve_stop_distance(atr_px=_atr_px(_BTC_1M_ATR_BPS), ref_px=_REF_PX,
                                        rule=_rule(), cost_bps=_MARKET_COST_BPS)
    assert skip is None and after is not None
    assert after.source == "atr_floored"
    assert math.isclose(after.stop_bps, 26.0, rel_tol=1e-9)
    assert math.isclose(after.stop_pct, 2.0 * _MARKET_COST_BPS / 1e4, rel_tol=1e-9)
    assert math.isclose(after.take_pct, 2.0 * after.stop_pct, rel_tol=1e-9)   # rr 仍作用其上


def test_floor_above_the_user_red_line_falls_back_entirely():
    """下界 > `sl_cap` ⇒ `(None, "floor_above_cap")` ⇒ 整条覆盖回退固定 pct。

    挂一个「刚好等于红线」的止损是**假装可行**：若成本下界比用户可见红线还大，说明这个标的/周期
    的成本结构已经彻底不可行。
    """
    plan, skip = resolve_stop_distance(atr_px=_atr_px(_BTC_1M_ATR_BPS), ref_px=_REF_PX,
                                       rule=_rule(max_sl_pct=0.001),   # 10 bps 红线
                                       cost_bps=_MARKET_COST_BPS)
    assert plan is None and skip == "floor_above_cap"


def test_missing_cost_keeps_the_pre_feature_behaviour():
    """`cost_bps is None` ⇒ 下界分支整体不进（回测未接成本时的逐字回退）。"""
    plan, skip = resolve_stop_distance(atr_px=_atr_px(_BTC_1M_ATR_BPS), ref_px=_REF_PX,
                                       rule=_rule(), cost_bps=None)
    assert skip is None and plan is not None and plan.source == "atr"


# ---------- G-7：`cost_floor_mult=0` 逐字段回退（冻结门禁）----------
def test_mult_zero_is_byte_identical_to_the_pre_feature_output():
    for atr_bps in (2.0, 5.6, 30.0, 400.0):
        floored, skip_a = resolve_stop_distance(atr_px=_atr_px(atr_bps), ref_px=_REF_PX,
                                                rule=_rule(cost_floor_mult=0.0),
                                                cost_bps=_MARKET_COST_BPS)
        bare, skip_b = resolve_stop_distance(atr_px=_atr_px(atr_bps), ref_px=_REF_PX,
                                             rule=_rule(cost_floor_mult=0.0))
        assert skip_a is None and skip_b is None
        assert floored == bare
        assert floored is None or floored.source == "atr"


def test_parse_stop_rule_reads_the_new_setting():
    rule = parse_stop_rule(Settings(ai_trade_stop_atr_mult=2.0))
    assert rule is not None and rule.cost_floor_mult == 2.0
    zero = parse_stop_rule(Settings(ai_trade_stop_atr_mult=2.0, ai_trade_stop_cost_floor_mult=0.0))
    assert zero is not None and zero.cost_floor_mult == 0.0


# ---------- G-6b：七态一路走到 wire + 两个新计数 ----------
def _runtime(**over) -> SizingRuntime:
    return SizingRuntime(None, _rule(**over), None, inst_id="BTC-USDT-SWAP", bar_seconds=60)


def _decision() -> TradeDecision:
    return TradeDecision(action="open_long", size_fraction=0.5, confidence=0.9, reason="r")


def _resolve(runtime: SizingRuntime, atr_bps: float, cost_bps: float | None, executor=None):
    return runtime.resolve(_decision(), SizingContext(
        ref_px=_REF_PX, atr_px=_atr_px(atr_bps), cost_bps=cost_bps, step=0.0), executor)


def test_stop_source_reaches_the_wire_as_atr_floored():
    runtime = _runtime()
    _, plan, wire = _resolve(runtime, _BTC_1M_ATR_BPS, _MARKET_COST_BPS)
    assert plan is not None and plan.source == "atr_floored"
    assert wire["stopSource"] == "atr_floored"
    assert runtime.counters.stop_floored_rounds == 1
    # `atrStopRounds` 度量「ATR 覆盖到了多少开仓」，两态都要计（`discipline_runtime` 的唯一读点）。
    assert runtime.counters.atr_stop_rounds == 1


def test_floor_above_cap_reaches_the_wire_and_leaves_risk_untouched():
    """G-6b：`floor_above_cap` **一路走到 wire**（v1 的裸 `None` 写法里这个状态根本到不了）。"""
    risk = RiskParams(stop_loss_pct=0.03, take_profit_pct=0.06, max_hold_bars=99)
    executor = SimpleNamespace(risk=risk, resting=None, projected="flat",
                               budget_usd=1000.0, leverage=3.0)
    runtime = _runtime(max_sl_pct=0.001)
    _, plan, wire = _resolve(runtime, _BTC_1M_ATR_BPS, _MARKET_COST_BPS, executor)
    assert plan is None
    assert wire["stopSource"] == "floor_above_cap"
    assert runtime.counters.floor_above_cap_rounds == 1
    # `executor.risk` **逐字段不变**：「plan 为 None 就不碰 risk」这条不变式原样保留。
    assert executor.risk is risk
    assert (risk.stop_loss_pct, risk.take_profit_pct, risk.max_hold_bars) == (0.03, 0.06, 99)


def test_skipped_states_win_whenever_a_plan_actually_existed():
    """两个 `fixed` 的成因必须分得清（这正是 [P0-2] 要的那半个信息）。

    - **有 plan** 但因「挂单在飞 / 有持仓 / 反手」没写进 risk ⇒ `skipped_*`：计划真的算出来了，
      是落地那一步被前置条件挡住的；
    - **plan 从未产生**（下界高于用户红线）⇒ `floor_above_cap`：此时说「因为有挂单所以跳过」
      是一句假话——根本没有可跳过的计划。

    `skip_reason` 只在 `source` 落成 `"fixed"` 时才顶上去，故上面两条互不遮蔽。
    """
    resting = SimpleNamespace(risk=RiskParams(stop_loss_pct=0.03, take_profit_pct=0.06),
                              resting={"ordId": "1"}, projected="flat",
                              budget_usd=1000.0, leverage=3.0)
    wide = _runtime()
    _, plan, wire = _resolve(wide, 60.0, _MARKET_COST_BPS, resting)   # ATR 宽 ⇒ plan 真的存在
    assert plan is not None
    assert wire["stopSource"] == "skipped_resting"
    assert wide.counters.floor_above_cap_rounds == 0

    capped = _runtime(max_sl_pct=0.001)
    _, plan2, wire2 = _resolve(capped, _BTC_1M_ATR_BPS, _MARKET_COST_BPS, resting)
    assert plan2 is None
    assert wire2["stopSource"] == "floor_above_cap"
    assert capped.counters.resting_skips == 0


# ---------- G-8：回测与实盘解出同一个 StopPlan ----------
def test_backtest_and_live_resolve_the_same_stop_plan():
    """轴 B 的同构门禁。

    **[P1-1] 本条只覆盖 `StopPlan`，不覆盖 `size_fraction`**：`p_win` 在回测侧仍恒 `None`
    ⇒ `kelly_fraction` 恒 `no_prior` ⇒ 凯利项恒不参与 `min`，两侧仓位**依然不同构**。那是本轮
    如实记下的欠账（§十一 第 1 条）——请勿把 G-8 误读成「回测与实盘仓位已对齐」。
    """
    executor = SimpleNamespace(risk=RiskParams(stop_loss_pct=0.03, take_profit_pct=0.06),
                               resting=None, projected="flat", budget_usd=1000.0, leverage=3.0)
    _, live_plan, live_wire = _resolve(_runtime(), _BTC_1M_ATR_BPS, _MARKET_COST_BPS, executor)
    _, bt_plan, bt_wire = _resolve(_runtime(), _BTC_1M_ATR_BPS, _MARKET_COST_BPS, None)
    assert live_plan == bt_plan
    assert live_wire["stopSource"] == bt_wire["stopSource"] == "atr_floored"


# ---------- G-19c：全仓调用点扫描 ----------
def test_every_call_site_unpacks_the_two_tuple():
    """`resolve_stop_distance` 的返回类型变更是一次跨文件破坏性改动（实盘与回测双通道共用同一
    入口）。把 `tuple` 当 `StopPlan` 用的报错点会离现场很远，故按 AST 扫死每一个调用点。"""
    offenders: list[str] = []
    for path in sorted((_REPO_ROOT / "services/quant").rglob("*.py")):
        if "__pycache__" in str(path):
            continue
        tree = ast.parse(path.read_text("utf-8"))
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call)
                    and getattr(node.func, "id", None) == "resolve_stop_distance"):
                continue
            parent = _statement_of(tree, node)
            if not _consumes_a_pair(parent, node):
                offenders.append(f"{path.name}:{node.lineno}")
    assert offenders == []


def _statement_of(tree: ast.AST, target: ast.Call) -> ast.AST | None:
    """找到包住该调用的最内层语句（赋值 / 断言 / 返回）。"""
    best = None
    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.Assert, ast.Return, ast.Expr)):
            if any(child is target for child in ast.walk(node)):
                best = node if best is None or node.lineno >= getattr(best, "lineno", 0) else best
    return best


def _consumes_a_pair(stmt: ast.AST | None, call: ast.Call) -> bool:
    """该语句是否把返回值当**二元组**用：解包赋值 / 与 tuple 字面量比较 / `[0]` 下标。"""
    if stmt is None:
        return False
    if isinstance(stmt, ast.Assign) and isinstance(stmt.targets[0], ast.Tuple):
        return True
    for node in ast.walk(stmt):
        if isinstance(node, ast.Tuple) and node is not stmt:
            return True
        if isinstance(node, ast.Subscript) and getattr(node.value, "func", None) is call.func:
            return True
    return False


# ---------- 轴 B 的**模型侧披露**：宪章章必须说出这条下界 ----------
def test_sizing_charter_discloses_the_cost_floor():
    """止损位是模型自己给的，而系统会把落进成本带以内的止损抬出去——不说就是让它误判。

    这是轴 B 唯一的模型侧落点（终审补：合入前它算了一个 `stopFloor` 读数，但全仓没有任何消费者
    ⇒ 死数据）。只写倍数、不写绝对 bps：宪章在启动时装配一次，而往返成本逐轮现算，写死一个数就
    会与门禁那侧说两套话。
    """
    text = format_sizing_charter(None, _rule(cost_floor_mult=2.0))
    assert "成本下界" in text and "2× 当轮往返成本" in text
    assert "回退为固定百分比" in text          # 与 `sl_cap` 冲突时的行为同样要说出来
    assert "bps" not in text                   # 绝不在启动期文案里写死一个逐轮会变的绝对值


def test_sizing_charter_falls_back_verbatim_when_the_floor_is_off():
    """`cost_floor_mult=0`（轴 B 未装配）⇒ 子句整段省略 ⇒ 宪章逐字回退（零回归）。"""
    off = format_sizing_charter(None, _rule(cost_floor_mult=0.0))
    assert "成本下界" not in off
    # 冻结：关掉下界后，这一章与合入前**逐字相同**。
    assert off == ("【仓位与风险层 · 系统强制执行】\n"
                   "· 止损距离按确认根 ATR 定标为 2×ATR、止盈为其 2 倍，"
                   "在开仓那一刻随单挂到交易所侧；不再是固定百分比。")
