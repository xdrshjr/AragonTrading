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


def build_cost_stats(attribution_stats: dict, fee_ledger) -> dict:
    """归因读数 + 费率账本实测读数 → `§6.3` 的三组加法键（`cost` / `edgeGate` /
    `decisionConversionRate`）。

    Args:
        attribution_stats: `AttributionLedger.stats()` 产物。
        fee_ledger: `FeeLedger` 实例或 `None`（未装配账本的会话 ⇒ 实测键一律缺席）。

    Returns:
        可直接并入 `aiStats` 的纯加法 dict（同一份产物亦作会话内 `costSnapshot`）。
    """
    stats = {key: dict(value) if isinstance(value, dict) else value
             for key, value in (attribution_stats or {}).items()}
    if fee_ledger is not None and isinstance(stats.get("cost"), dict):
        stats["cost"].update(fee_ledger.audit_stats())
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


def cost_snapshot_wire(round_no: int, attribution_stats: dict, fee_ledger, *,
                       every: int = _COST_SNAPSHOT_EVERY_ROUNDS) -> dict | None:
    """L0/L1 通道的节流装配（§5.4）：未到轮次 ⇒ `None`（调用方据此不推事件）。

    运维读这份快照的唯一别处路径是会话停止后的 `GET /ai-trade/history/{group_id}`，而 §7.5 的
    放行判据全部是**会话运行期内**的滚动窗口——没有这条通道，运维要看这些数就得先把会话停掉。
    """
    if every <= 0 or round_no <= 0 or round_no % every:
        return None
    return build_cost_stats(attribution_stats, fee_ledger)
