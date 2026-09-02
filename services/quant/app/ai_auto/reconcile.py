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
    net = 0.0
    trips = 0
    leg_fee = 0.0
    for trade in trades or []:
        leg_fee += _num((trade or {}).get("fee"))
        if (trade or {}).get("side") in ("long", "short"):
            continue
        trips += 1
        pnl = _num((trade or {}).get("pnl"))
        net += pnl if net_mode else pnl - leg_fee
        leg_fee = 0.0
    return {"netUsd": round(net, 6), "trips": trips}


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
    """官方关系式 `realizedPnl = pnl + fee + fundingFee + liqPenalty` 的运行期数值自检（C-3）。

    §2.3 的官方措辞未能从公开页面逐字取回（`docs-v5` 是 SPA），故本 spec **不把该式当作前提**：
    让代码自己去证，不成立即在看板上如实标注「交易所口径自检未通过」，而不是给出一个建立在错误
    假设上的差额。

    Returns:
        `(identityHolds, identityMaxGapUsd)`。
    """
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
                         capital_usd: float | None = None) -> dict:
    """本地净额 vs 交易所**可比口径**的窗口对账；样本不足即 `status="unavailable"`。

    Args:
        local_net_usd: 窗口内**已平仓往返**净额之和（`Σ trip.net_pnl`，绝不含未实现，P0-2）。
        local_trips: 同一窗口内的本地往返笔数（`coverageMismatch` 的对照数）。
        rows: `okx.trading.fetch_position_history` 的归一产物（三项已归一为「支出为正」）。
        window_start_ts: 会话起点（秒），按 `uTime` 过滤。
        params: 容差。
        final: `True` = stop 收尾那一次。**永不判 drift**（P1-5）：强制对账排在真实资金平仓之后，
            而 `positions-history` 的 `uTime` 对刚平的那一笔存在结算延迟 ⇒ 终态对账会系统性少一笔，
            呈现为一个伪 drift，而这偏偏是用户最常看的那一屏。
        inst_id: 可选的标的过滤（取数已按 instId 发起，此处为二次保险）。
        settlement: 结算覆盖率（`settlement.coverage_wire` 的产物）；`None` ⇒ **整键省略**。
        capital_usd: 会话本金（`executor.budget_usd`）；缺失 ⇒ 本金项不参与容差。

    Returns:
        §5.4 形状的只读 wire；`status ∈ {ok, drift, partial, unavailable}`。

    ⚠️ `final` 的语义在轴 F-1 之后变了：`params.strict_final` 为真且**样本数与本地往返数完全
    对齐**（`coverageMismatch=False`）时，终态对账**照样会判 drift**。原先「终态永不判 drift」的
    理由（刚平的那一笔 `uTime` 有结算延迟 ⇒ 终态会系统性少一笔）是对的，但「少一笔」的观测特征
    恰恰就是 `coverageMismatch`，它已经由 `partial` 分支单独接住——生产实测那 6 条被判 `ok` 却带着
    合计 7.41 USDT 差额的记录**全部 `mismatch=False`**。那是一次「为了消除伪阳性把真阳性一起消掉」
    的过度收敛，而它的代价是徽章写「已对上」、同一行正文写「差 +1.528 USDT」，产品在自相矛盾。
    """
    picked = _select_rows(rows, window_start_ts, inst_id)
    base = {"samples": len(picked), "localTrips": int(local_trips), "final": bool(final),
            "localNetUsd": round(float(local_net_usd), 6),
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
    comparable = realized + funding + liq          # 把本地不记的两项加回去（P0-2）
    diff = float(local_net_usd) - comparable
    tol = _tolerance(comparable, params, capital_usd)
    mismatch = len(picked) != int(local_trips)
    identity_holds, identity_gap = _identity_check(picked, params.abs_tol_usd)
    return {**base, "status": _status(diff, tol, mismatch, final,
                                      strict_final=params.strict_final),
            "coverageMismatch": mismatch,
            # `tolUsd` 是加法键（轴 F-4 的第 1 条要用它）：前端要能把「为什么这也叫已对上」当场
            # 答掉——没有它，`ok` 分支的措辞只能含糊说「在容差内」而说不出容差是多少。
            "tolUsd": round(tol, 6),
            "exchangeRealizedUsd": round(realized, 6),
            "exchangeComparableUsd": round(comparable, 6),
            "diffUsd": round(diff, 6),
            "diffRatio": round(diff / max(1e-9, abs(comparable)), 6),
            "exchangeFeeUsd": round(fee, 6), "exchangeFundingFeeUsd": round(funding, 6),
            "exchangeLiqPenaltyUsd": round(liq, 6),
            "identityHolds": identity_holds, "identityMaxGapUsd": round(identity_gap, 9)}


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
