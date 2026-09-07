"""本地净额 vs 交易所结算的窗口对账纯核心（ai-trade-cost-truth-and-net-pnl 轴 C · C-2/C-3）。

无 IO、可单测、**绝不抛**。本层的价值不是「把盈亏改对」，而是**把不可见的偏差变成可见的数字**：
`positions-history` 是**持仓级**结算而本地账本是**往返级**，`net_mode` 与 `long_short_mode` 下二者
的边界并不总是一一对应，把交易所读数直接灌回本地盈亏会让「盈亏正确性 = 网络可用性 × 持仓模式假设」
（C-5，与阶段一「实测费率只进归因、绝不回写盈亏」同一条理由）。

两条必须先读的口径约束：

1. **差额只对「可比口径」作差**（P0-2）。本地净额**按定义不含**资金费与强平罚金（本 spec §一
   「不做什么」①），交易所 `realizedPnl` **按定义含**。直接相减会让任何跨过一次结算点的持仓的
   `diffUsd` 恒等于资金费本身——一个按设计必然误报的告警比没有告警更糟，它训练运维忽略它。故先把
   本地不记的两项**加回交易所侧**：`exchangeComparable = realized + funding + liqPenalty`。
2. **`local_net_usd` 恒取「窗口内已平仓往返的净额之和」**，绝不取 `session_pnl_usd`——后者含未实现
   盈亏，而 `positions-history` 只覆盖已结算持仓，混用会在差额里叠一层与持仓浮盈同幅的结构性偏差。
"""
from __future__ import annotations

from dataclasses import dataclass
import math

from .accounting_replay import TradeAccountingAccumulator
from .trade_economics import finite

# 恒等式自检的相对容差（C-3）：交易所侧的四项相加与 realizedPnl 的残差按此判定是否成立。
_IDENTITY_REL_TOL = 1e-6


@dataclass(frozen=True)
class ReconcileParams:
    """对账容差（绝对值与比例取大，R-4：持仓级 / 往返级边界不一一对应时按窗口合计判）。

    Attributes:
        abs_tol_usd: 绝对容差下限。
        rel_tol: 相对容差（乘在交易所可比口径的绝对值上）。
        strict_final: 终态对账是否也判 drift（fill-truth-and-effective-controls 轴 F-1）。
            **默认 False ⇒ `_status` 逐字回退**（`final` 恒 `ok`）。
        capital_tol_ratio: 按本金收紧绝对容差的比例。**默认 0.0 ⇒ 容差公式逐字等于今天**。
    """

    abs_tol_usd: float
    rel_tol: float
    strict_final: bool = False
    capital_tol_ratio: float = 0.0


def resolve_reconcile_params(settings) -> ReconcileParams:
    """settings → `ReconcileParams`（唯一解析点）。"""
    return ReconcileParams(
        abs_tol_usd=float(settings.ai_trade_reconcile_abs_tol_usd),
        rel_tol=float(settings.ai_trade_reconcile_rel_tol),
        strict_final=bool(getattr(
            settings, "ai_trade_reconcile_final_verdict_strict", False)),
        capital_tol_ratio=max(0.0, float(getattr(
            settings, "ai_trade_reconcile_capital_tol_ratio", 0.0))))


def local_net_view(trades: list[dict], *, net_mode: bool) -> dict:
    """会话内成交流 → 对账用的本地视图（`{"netUsd", "trips"}`）。

    **恒取「已平仓往返的净额之和」，绝不取 `executor.session_pnl_usd`**（P0-2）：后者含未实现盈亏，
    而 `positions-history` 只覆盖已结算持仓，混用会在差额里叠一层与持仓浮盈同幅的结构性偏差。
    `net_mode=False`（总闸关，`trade["pnl"]` 是毛额）时按 `pnl − Σ 腿费` 还原为净额——与
    `db/ai_auto_repositories._Trip.net_pnl` 同一条规则，两侧不可各写一份。

    Args:
        trades: `LiveExecutor.trades`（含开仓腿与平仓腿，按发生序）。
        net_mode: `settings.ai_trade_net_pnl_enabled`（决定 `pnl` 是否已扣费）。

    Returns:
        `{"netUsd": float, "trips": int}`。
    """
    accumulator = TradeAccountingAccumulator({
        "costBasis": "net" if net_mode else "gross"})
    snapshot = accumulator.restore(trades or [])
    return {"netUsd": snapshot["tradingNetUsd"],
            "trips": snapshot["roundTripCount"]}


def _num(value: object) -> float:
    """转有限浮点（非数值 / 非有限 ⇒ 0.0）——对账读数里 NaN 比 0 危险得多。"""
    try:
        out = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0
    return out if out == out and out not in (float("inf"), float("-inf")) else 0.0


def _select_rows(rows: list[dict], window_start_ts: int, inst_id: str | None) -> list[dict]:
    """窗口内、且（给了 instId 时）标的匹配的已结算持仓行。"""
    picked = []
    for row in rows or []:
        if int(_num((row or {}).get("uTime"))) < int(window_start_ts):
            continue
        if inst_id and str((row or {}).get("instId") or "") != inst_id:
            continue
        picked.append(row)
    return picked


def _identity_check(rows: list[dict], abs_tol_usd: float) -> tuple[bool, float]:
    """旧对账自检展示；v2 完整性单独按内部带符号恒等式核验全部字段。"""
    gaps = [abs(_num(r.get("identityGapUsd"))) for r in rows]
    scale = sum(abs(_num(r.get("realizedPnl"))) for r in rows)
    max_gap = max(gaps) if gaps else 0.0
    return max_gap <= max(abs_tol_usd, _IDENTITY_REL_TOL * scale), max_gap


def _tolerance(comparable: float, params: ReconcileParams, capital_usd: float | None) -> float:
    """容差 = `max(相对项, min(绝对下限, 本金 × capital_tol_ratio))`。

    `capital_tol_ratio == 0`（默认）或 `capital_usd` 缺失 ⇒ **本金项整个不参与 `min`** ⇒ 公式逐字
    等于今天。理由（轴 F-2）：100 USDT 本金上 `abs_tol_usd=0.5` 就是 0.5% 的本金，偏宽；但轴 A
    落地后差额本来就该收敛到接近零，此刻同时收紧两个判据会让效果无法归因。
    """
    floor = params.abs_tol_usd
    if params.capital_tol_ratio > 0 and capital_usd and capital_usd > 0:
        floor = min(floor, float(capital_usd) * params.capital_tol_ratio)
    return max(params.rel_tol * abs(comparable), floor)


def build_reconciliation(*, local_net_usd: float, local_trips: int, rows: list[dict],
                         window_start_ts: int, params: ReconcileParams,
                         final: bool = False, inst_id: str | None = None,
                         settlement: dict | None = None,
                         capital_usd: float | None = None,
                         comparison_coverage: dict | None = None) -> dict:
    """本地已平净额的窗口对账，v2 金额仅在身份和窗口证据完整时返回。

    旧 status 保持兼容；strict_final 开启且笔数一致才在终态判 drift。
    rows 的手续费、资金费及强平支出已取反，settledPnl 保留原收益符号。
    """
    picked = _select_rows(rows, window_start_ts, inst_id)
    comparable_view = comparison_view(rows, picked, comparison_coverage)
    base = {"samples": len(picked), "localTrips": int(local_trips), "final": bool(final),
            "localNetUsd": round(float(local_net_usd), 6) if local_net_usd is not None else None,
            **comparable_view,
            "windowStartTs": int(window_start_ts)}
    if settlement:                      # 加法键，`None` / 空 dict ⇒ 整键省略（`G-F3`）
        base["settlementCoverage"] = settlement
    if not picked:
        # **不报 0 差额**：没样本不等于对上了。
        return {**base, "status": "unavailable", "coverageMismatch": False, "diffUsd": None,
                "diffRatio": None, "exchangeRealizedUsd": None, "exchangeComparableUsd": None,
                "exchangeFeeUsd": None, "exchangeFundingFeeUsd": None,
                "exchangeLiqPenaltyUsd": None, "identityHolds": None, "identityMaxGapUsd": None}
    realized = sum(_num(r.get("realizedPnl")) for r in picked)
    fee = sum(_num(r.get("fee")) for r in picked)
    funding = sum(_num(r.get("fundingFee")) for r in picked)
    liq = sum(_num(r.get("liqPenalty")) for r in picked)
    settlement_adjustment = sum(_num(r.get("settledPnl")) for r in picked)
    comparable = realized + funding + liq - settlement_adjustment
    diff = float(local_net_usd) - comparable if local_net_usd is not None else 0.0
    tol = _tolerance(comparable, params, capital_usd)
    mismatch = len(picked) != int(local_trips)
    identity_holds, identity_gap = _identity_check(picked, params.abs_tol_usd)
    return {**base, "status": ("unavailable" if local_net_usd is None else
                _status(diff, tol, mismatch, final, strict_final=params.strict_final)),
            "coverageMismatch": mismatch,
            # `tolUsd` 是加法键（轴 F-4 的第 1 条要用它）：前端要能把「为什么这也叫已对上」当场
            # 答掉——没有它，`ok` 分支的措辞只能含糊说「在容差内」而说不出容差是多少。
            "tolUsd": round(tol, 6),
            "exchangeRealizedUsd": round(realized, 6),
            "exchangeComparableUsd": round(comparable, 6),
            "diffUsd": round(diff, 6) if local_net_usd is not None else None,
            "diffRatio": round(diff / max(1e-9, abs(comparable)), 6)
            if local_net_usd is not None else None,
            "exchangeFeeUsd": round(fee, 6), "exchangeFundingFeeUsd": round(funding, 6),
            "exchangeLiqPenaltyUsd": round(liq, 6),
            "identityHolds": identity_holds, "identityMaxGapUsd": round(identity_gap, 9)}


def comparison_view(rows: list[dict], picked: list[dict], evidence: dict | None) -> dict:
    """验证身份、窗口、分页和官方恒等式，未证明完整则不产生可比金额。"""
    coverage = dict(evidence or {})
    reason = _comparison_reason(rows, picked, coverage)
    coverage.update(comparable=reason is None, reason=reason,
                    coverage="complete" if reason is None else "partial")
    keys = ("exchangeTradingNetUsd", "exchangeFundingCostUsd",
            "exchangeNetAfterFundingUsd", "exchangeRealizedPnlUsd",
            "liquidationCostUsd", "settlementAdjustmentUsd")
    result = {key: None for key in keys}
    result["comparisonCoverage"] = coverage
    if reason is not None:
        return result
    realized = math.fsum(float(row["realizedPnl"]) for row in picked)
    funding = math.fsum(float(row["fundingFee"]) for row in picked)
    liquidation = math.fsum(float(row["liqPenalty"]) for row in picked)
    settlement = math.fsum(float(row["settledPnl"]) for row in picked)
    trading = realized + funding + liquidation - settlement
    result.update(exchangeTradingNetUsd=trading,
        exchangeFundingCostUsd=funding, exchangeNetAfterFundingUsd=trading - funding,
        exchangeRealizedPnlUsd=realized, liquidationCostUsd=liquidation,
        settlementAdjustmentUsd=settlement)
    return result


def _comparison_reason(rows, picked, coverage):
    if not picked:
        return "no_samples"
    if len(rows) >= 100 or not coverage.get("pagesComplete"):
        return "incomplete_pages"
    if not coverage.get("accountId") or not coverage.get("identityMatched"):
        return "missing_identity"
    trips = set(coverage.get("closedTripIds") or [])
    links = coverage.get("tripPositionLinks") or {}
    ids = [str(row.get("posId") or "") for row in picked]
    expected = {str(value) for value in coverage.get("positionIds") or []}
    if not trips or set(links) != trips or set(links.values()) != expected:
        return "missing_identity"
    if "" in ids or len(ids) != len(set(ids)) or set(ids) != expected:
        return "position_set_mismatch"
    start = finite(coverage.get("windowStartTs"))
    end = finite(coverage.get("windowEndTs"))
    fetched = finite(coverage.get("fetchedAtTs"))
    if start is None or end is None or fetched is None or start > end or fetched < end:
        return "incomplete_window"
    if coverage.get("currency") != "USDT":
        return "currency_mismatch"
    for row in picked:
        reason = _comparison_row_reason(row, coverage, start, end)
        if reason:
            return reason
    return None


def _comparison_row_reason(row, coverage, start, end):
    if row.get("closed") is not True:
        return "position_not_closed"
    if row.get("instId") != coverage.get("instId"):
        return "missing_identity"
    if row.get("currency") != "USDT":
        return "currency_mismatch"
    opened, closed = finite(row.get("cTime")), finite(row.get("uTime"))
    if opened is None or closed is None or not start <= opened <= closed <= end:
        return "incomplete_window"
    fields = ("pnl", "fee", "fundingFee", "liqPenalty", "settledPnl", "realizedPnl")
    amounts = [finite(row.get(key)) for key in fields]
    if any(value is None for value in amounts):
        return "missing_adjustment"
    gross, fee, funding, liquidation, settlement, realized = amounts
    expected = gross - fee - funding - liquidation + settlement
    if not math.isclose(realized, expected, rel_tol=_IDENTITY_REL_TOL, abs_tol=1e-9):
        return "identity_mismatch"
    return None


def _status(diff: float, tol: float, mismatch: bool, final: bool, *,
            strict_final: bool = False) -> str:
    """判定链 ⑥⑦⑧：覆盖不齐即降为 `"partial"`；终态是否判 drift 由 `strict_final` 决定（轴 F-1）。

    `strict_final=False`（默认，也是总闸 14 的回退档）⇒ 逐字回退今天：`final` 恒 `ok`。
    """
    if final:
        if mismatch:
            return "partial"
        if strict_final and abs(diff) > tol:
            return "drift"          # 样本对齐却对不上账，就是对不上账
        return "ok"
    return "drift" if abs(diff) > tol else "ok"


def drift_message(rec: dict, streak: int) -> str:
    """漂移告警文案（点名差额与三条成因，**不得宣称已自动修正**——本层只观测、不回写盈亏）。

    ⚠️ **文案里不得出现字面 `**`**：后端文案在 JSX 里是纯文本节点，Markdown 粗体标记会原样显示
    成星号（门禁 `G-F4` / `G-X3`）。

    三条成因按本轮实测的可能性排序（轴 F-3）：第一条附上「按本地标记价入账」的**实际笔数**，
    它正是轴 A 要修的那件事——用户看到的应当是一个解释，而不是一个孤立的红。
    """
    coverage = rec.get("settlementCoverage") or {}
    local_mark = coverage.get("localMark")
    first = ("① 外部平仓按本地标记价入账"
             + (f"（本会话已有 {local_mark} 笔）" if local_mark else "")
             + "：仓位实际是被交易所侧的止盈止损成交掉的，成交价与侦测那一刻的标记价并不相同；")
    second = ("② 交易所侧存在未接管持仓"
              + ("（样本数与本地往返数不一致）" if rec.get("coverageMismatch") else "")
              + "；")
    return (f"本地盈亏与交易所结算连续 {streak} 次对不上："
            f"本地 {rec.get('localNetUsd')} USDT / 交易所可比口径 {rec.get('exchangeComparableUsd')} USDT"
            f"（差 {rec.get('diffUsd')}，{_pct(rec.get('diffRatio'))}，容差 {rec.get('tolUsd')}）。"
            f"已付手续费 {rec.get('exchangeFeeUsd')}、资金费 {rec.get('exchangeFundingFeeUsd')}。"
            f"可能成因：{first}{second}③ 该标的上存在非本会话的手工持仓。"
            "系统只观测、不改写盈亏。")


def _pct(ratio: object) -> str:
    return "—" if ratio is None else f"{_num(ratio) * 100:.2f}%"
