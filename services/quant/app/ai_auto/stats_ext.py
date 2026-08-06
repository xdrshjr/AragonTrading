"""统计加法键的装配与合并（ai-trade-cost-accounting-and-edge-gate §3.8 / §5.4）。

两个刻意的设计：

1. **合并落点是 `_finalize_run` 的调用点，不是 `_compute_ai_stats` 内部**（RV-1）。后者是**模块级
   纯函数** `def _compute_ai_stats(records, rounds)`，拿不到 `self`，因此「在其内部合并」在物理上
   不可实现。本仓库已为「会话级直取的加法键」确立了调用点合并范式（`wakeSkips` / `maxHoldStreak` /
   `profitGuardExits` / `profitGuardArmedRounds` 四键皆然），本模块顺势复用。

2. **键冲突即 `raise ValueError`**，绝不静默覆盖。富化块外层已有 `except Exception → warning + pop`
   的 best-effort 兜底，故最坏只丢失富化字段，**绝不阻断真实资金平仓后的落库收尾**。

`build_cost_stats` 是**唯一**的成本统计真源：会话内 `costSnapshot`（trace / status 两条通道）与
会话停止后的 `metricsJson.aiStats` 出自同一次调用，绝不另算一份（否则立刻产生第二个统计口径）。
"""
from __future__ import annotations

# 门禁空转（`k_min == 0`，L0/L1 阶段）时成本快照搭 status 事件的轮次节流（§5.4）。
_COST_SNAPSHOT_EVERY_ROUNDS = 10


def build_cost_stats(attribution_stats: dict, fee_ledger, *, fee_rates: dict | None = None,
                     reconcile: dict | None = None) -> dict:
    """归因读数 + 费率账本实测读数 → `§6.3` 的三组加法键（`cost` / `edgeGate` /
    `decisionConversionRate`），外加轴 A / 轴 C 的两个**顶层**加法键。

    挂载层级的唯一约定（cost-truth-and-net-pnl P1-1）：`feeRates` / `reconcile` 挂**顶层**
    （落库即 `aiStats.feeRates` / `aiStats.reconcile`），`sessionFeeUsd` 挂 `cost` 子对象（与
    `measuredFeeBps` / `feeDeviationBps` 同族）。本函数是 `costSnapshot` 与落库 `aiStats` 的
    **同一个**产物，故两条通道必然同形——两种写法不可能同时成立。

    Args:
        attribution_stats: `AttributionLedger.stats()` 产物。
        fee_ledger: `FeeLedger` 实例或 `None`（未装配账本的会话 ⇒ 实测键一律缺席）。
        fee_rates: `FeeRateCache.wire()` 产物（从未取到 ⇒ `None` ⇒ 该顶层键**缺席**）。
        reconcile: 最近一次对账读数（未跑过 ⇒ `None` ⇒ 该顶层键**缺席**，前端整段隐藏）。

    Returns:
        可直接并入 `aiStats` 的纯加法 dict（同一份产物亦作会话内 `costSnapshot`）。
    """
    stats = {key: dict(value) if isinstance(value, dict) else value
             for key, value in (attribution_stats or {}).items()}
    if fee_ledger is not None and isinstance(stats.get("cost"), dict):
        stats["cost"].update(fee_ledger.audit_stats())
        stats["cost"]["sessionFeeUsd"] = round(fee_ledger.session_fee_usd, 6)
    if fee_rates is not None:
        stats["feeRates"] = fee_rates
    if reconcile is not None:
        stats["reconcile"] = reconcile
    return stats


def merge_cost_stats(base: dict, ext: dict) -> dict:
    """把成本统计并入既有 `aiStats`；**任一键冲突即抛**（防止未来静默覆盖既有统计键）。

    Raises:
        ValueError: `ext` 的某个键已存在于 `base`（附冲突键名，便于定位）。
    """
    merged = dict(base or {})
    clashes = sorted(set(merged) & set(ext or {}))
    if clashes:
        raise ValueError(f"cost stats key clash with existing aiStats: {','.join(clashes)}")
    merged.update(ext or {})
    return merged


def build_sizing_stats(counters, brake_counters, brake_state) -> dict:
    """仓位 / 刹车层的加法键（`aiStats.sizing`；vol-sizing-and-drawdown-brake §3.9）。

    三组读数刻意分开：`scaledRounds` 说明本层**真的缩过**，`quantizeErasedRounds` 说明其中有多少次
    缩减被档位地板原样吃了回去，`brakeSlowRounds` 只说明第一档在那些轮是激活的——三者不可互相换算，
    只凭最后一个宣称「刹车已验证」正是 [P1-2] 要防的假阳性观测。

    Args:
        counters: `SizingRuntime._Counters`（轮级）。
        brake_counters: `drawdown_brake.BrakeCounters`（档位触发级）。
        brake_state: `drawdown_brake.BrakeState`（会话级最大回撤）。

    Returns:
        `{"sizing": {...}}` 形状的纯加法 dict。
    """
    gated = counters.gated_rounds
    ratios = counters.scale_ratios
    shares = brake_counters.halt_unrealized_shares
    return {"sizing": {
        "gatedRounds": gated, "scaledRounds": counters.scaled_rounds,
        "scaleRate": round(counters.scaled_rounds / gated, 4) if gated else 0.0,
        "avgScaleRatio": round(sum(ratios) / len(ratios), 4) if ratios else None,
        "quantizeErasedRounds": counters.quantize_erased_rounds,
        "brakeSlowRounds": brake_counters.slow_rounds,
        "brakeHaltRounds": brake_counters.halt_rounds,
        "lossStreakCooldowns": brake_counters.cooldown_hits,
        "maxDrawdownPct": round(brake_state.max_dd, 6),
        "haltUnrealizedShareAvg": round(sum(shares) / len(shares), 4) if shares else None,
        "atrStopRounds": counters.atr_stop_rounds,
        "reversalSkips": counters.reversal_skips, "restingSkips": counters.resting_skips,
        "corrScaledRounds": counters.corr_scaled_rounds,
        "exposureUnreachableRounds": counters.exposure_unreachable_rounds}}


def merge_sizing_stats(base: dict, ext: dict) -> dict:
    """把仓位统计并入既有 `aiStats`；**任一键冲突即抛**（镜像 `merge_cost_stats`，绝不静默覆盖）。

    Raises:
        ValueError: `ext` 的某个键已存在于 `base`（附冲突键名，便于定位）。
    """
    merged = dict(base or {})
    clashes = sorted(set(merged) & set(ext or {}))
    if clashes:
        raise ValueError(f"sizing stats key clash with existing aiStats: {','.join(clashes)}")
    merged.update(ext or {})
    return merged


def cost_snapshot_wire(round_no: int, attribution_stats: dict, fee_ledger, *,
                       every: int = _COST_SNAPSHOT_EVERY_ROUNDS,
                       fee_rates: dict | None = None,
                       reconcile: dict | None = None) -> dict | None:
    """L0/L1 通道的节流装配（§5.4）：未到轮次 ⇒ `None`（调用方据此不推事件）。

    运维读这份快照的唯一别处路径是会话停止后的 `GET /ai-trade/history/{group_id}`，而 §7.5 的
    放行判据全部是**会话运行期内**的滚动窗口——没有这条通道，运维要看这些数就得先把会话停掉。
    """
    if every <= 0 or round_no <= 0 or round_no % every:
        return None
    return build_cost_stats(attribution_stats, fee_ledger, fee_rates=fee_rates,
                            reconcile=reconcile)
