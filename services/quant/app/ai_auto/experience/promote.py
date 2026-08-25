"""档位晋升 / 衰减 / 符号翻转（§4.7）—— 纯函数、零 IO。

四档 + 两个用户意志位（`pinned` / `muted`）：

| 档位 | 门槛 | 披露 |
|---|---|---|
| `candidate` | `n < min_samples` 或 `|t| < t_threshold` | 不进 prompt，UI 可见 |
| `normal` | `n ≥ min_samples` 且 `|t| ≥ t_threshold` | L1 可选 |
| `iron` | `n ≥ iron_samples` 且 `|t| ≥ iron_t` 且 `sign_streak ≥ 2` | L0 可选 |
| `archived` | 被 `refines` 指向的旧版本 | 仅 UI 版本链 |

两条不可让步的规则：

- **I-4 · 叙事不得独立成铁律**：`source ∈ {narrative, manual, imported}` 永远不能自动晋升到
  `iron`。要进 L0，要么被 ≥`min_samples` 条确定性事件佐证（那时它的档位由统计给出），要么由用户
  **显式置顶**（一次人类动作）。理由是 R-1：模型写一条经验 → 下次读到 → 照做 → 再写一遍，最终把
  一个从未被验证的偏见铸成铁律，然后按它花真钱。
- **符号一致性必须有一个活得过物化的落点**（评审 P0-2 配套）：`prev_sign` / `sign_streak` 随
  `stats_json` 前滚。没有这一步，`iron` 的第三条门槛在结构上永远判不成立——v1 的「全量替换」
  正是这么塌的：装配成功、日志全绿、L0 恒空、一个字都不喂。
"""
from __future__ import annotations

from dataclasses import dataclass

TIER_CANDIDATE = "candidate"
TIER_NORMAL = "normal"
TIER_IRON = "iron"
TIER_ARCHIVED = "archived"

# 自动晋升到 `iron` 需要的连续同号物化次数（§4.7 第三条门槛）。
IRON_SIGN_STREAK = 2

_AUTO_IRON_SOURCES = ("stat",)


@dataclass(frozen=True)
class PromoteRule:
    """晋升判据（全部来自 `Settings`，聚合形参满足「形参 ≤5」）。"""

    min_samples: int = 8
    iron_samples: int = 20
    t_threshold: float = 2.0
    iron_t_threshold: float = 2.5
    stale_days: float = 90.0
    global_min_symbols: int = 3

    @classmethod
    def from_settings(cls, settings) -> "PromoteRule":
        return cls(
            min_samples=int(settings.ai_trade_experience_min_samples),
            iron_samples=int(settings.ai_trade_experience_iron_samples),
            t_threshold=float(settings.ai_trade_experience_t_threshold),
            iron_t_threshold=float(settings.ai_trade_experience_iron_t_threshold),
            stale_days=float(settings.ai_trade_experience_stale_days),
            global_min_symbols=int(settings.ai_trade_experience_global_min_symbols))


@dataclass(frozen=True)
class SignRoll:
    """符号前滚的结果：新符号 / 新连续计数 / 本次是否发生翻转。"""

    sign: int
    streak: int
    flipped: bool


def roll_sign(prev_sign: int, prev_streak: int, mean_net_bps) -> SignRoll:
    """把本次物化的效应符号并进历史。

    Args:
        prev_sign: 上一次物化时 `meanNetBps` 的符号（-1 / 0 / +1）。
        prev_streak: 此前连续同号的物化次数。
        mean_net_bps: 本次的净额均值；`None`（样本不足）⇒ 符号视为 0，历史**保持不变**——
            一次没有样本的重算不该把攒了两次的连续性清零。

    Returns:
        `SignRoll`；`flipped=True` 当且仅当两侧符号都非零且相反。
    """
    if mean_net_bps is None:
        return SignRoll(sign=int(prev_sign), streak=int(prev_streak), flipped=False)
    sign = 0 if mean_net_bps == 0 else (1 if mean_net_bps > 0 else -1)
    if sign == 0:
        return SignRoll(sign=0, streak=0, flipped=False)
    if sign == prev_sign:
        return SignRoll(sign=sign, streak=int(prev_streak) + 1, flipped=False)
    flipped = prev_sign != 0 and sign != prev_sign
    return SignRoll(sign=sign, streak=1, flipped=flipped)


def resolve_tier(stats: dict, rule: PromoteRule, *, source: str = "stat",
                 pinned: bool = False, sign_streak: int = 0) -> str:
    """判定档位。

    Args:
        stats: `BucketStats.wire()`（读 `n` / `tStat`）。
        rule: 晋升判据。
        source: 经验来源；非 `stat` 的**永远不能自动进 `iron`**（不变式 I-4）。
        pinned: 用户是否显式置顶（唯一能让叙事 / 手写经验进 `iron` 的路径）。
        sign_streak: 连续同号物化次数（`iron` 第三条门槛）。

    Returns:
        `candidate` | `normal` | `iron`。（`archived` 由版本链单独赋值，不走本函数。）
    """
    if pinned:
        return TIER_IRON
    n = int(stats.get("n") or 0)
    t_abs = abs(float(stats.get("tStat") or 0.0))
    if n < rule.min_samples or t_abs < rule.t_threshold:
        return TIER_CANDIDATE
    if (source in _AUTO_IRON_SOURCES and n >= rule.iron_samples
            and t_abs >= rule.iron_t_threshold and int(sign_streak) >= IRON_SIGN_STREAK):
        return TIER_IRON
    return TIER_NORMAL


def evidence_weight(stats: dict, rule: PromoteRule) -> float:
    """证据强度 `sat(support) · conf(|t|)` ∈ [0,1]（检索打分的一个乘子，§4.6 步骤 3）。"""
    import math

    n = max(0, int(stats.get("n") or 0))
    sat = math.log1p(n) / math.log1p(max(1, rule.iron_samples))
    conf = abs(float(stats.get("tStat") or 0.0)) / max(1e-9, rule.iron_t_threshold)
    return round(_clip01(sat) * _clip01(conf), 6)


def resolve_scope(stats: dict, signature: dict, rule: PromoteRule) -> str:
    """`symbol` 还是 `global`：跨 ≥`global_min_symbols` 个标的支撑的无标的桶才算通用经验。

    这是 R-4（跨标的污染）的第一道闸：PEPE 1m 的教训绝不能因为「它没写标的」就自动适用于 BTC 1H。
    """
    from . import taxonomy as tax

    if tax.AXIS_SYMBOL in signature:
        return "symbol"
    return "global" if int(stats.get("symbols") or 0) >= rule.global_min_symbols else "symbol"


def is_stale(stats: dict, rule: PromoteRule, *, now_wall: float) -> bool:
    """该经验是否已沉降（最近一条支撑事件距今 > `stale_days`）。

    判据用的是 `stats.lastTs`（**最近一条支撑事件的墙钟**），与节点的 `last_seen_ts` 同值同源——
    绝不是「最近一次物化的时刻」，后者由 `updated_ts` 承载（评审 P1-7）。
    """
    last = float(stats.get("lastTs") or 0)
    if last <= 0:
        return False
    return (float(now_wall) - last) > (rule.stale_days * 86400.0)


def _clip01(value: float) -> float:
    return 0.0 if value < 0 else (1.0 if value > 1 else float(value))
