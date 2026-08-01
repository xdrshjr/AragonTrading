"""唤醒原因判定（ai-trade-wake-aware-context 轴 B，纯函数、无 IO、无副作用）。

诊断根因：`payload["trigger"]` 只有 `candle` / `interval` 两个值，模型无法区分「新 K 线刚收」
「上一轮还在跑、这根 K 线的首次研判被推迟到现在」「价格刚走出一个 ATR 量级的异动」「你有一笔限价单
挂了 4 分钟」「什么都没发生，只是心跳」——五种局面对应完全不同的注意力分配，却共用一句话。

本模块把它扩成七种 `kind` 的结构化判定，并让**同一个判定**同时驱动跳过守卫（`wake_guard`）与 prompt
措辞（`wake_text`）：两处各算一次是「prompt 说数据没变、守卫却认为变了」这类信任崩塌的经典成因。
阈值随判定结果一起下发（`moveThresholdAtrEff` / `horizonScale`），使「判据是多少、实际是多少」对人与
模型都可见——静默的门禁在本仓库有前科。
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from .profit_guard import ARMED_STATUSES

# 八种唤醒原因，按判定优先级排列（首个命中即为 kind，其余成立项进 alsoTrue）。
# `signal_flip` 插在 `bar_advanced` 之后、`resting_order` 之前：真的收了新根永远优先；除此之外，
# 「某个策略的立场正在翻转」是信息量最高的一件事（wake-scheduler-closed-loop §2.4）。
WAKE_KINDS = ("candle_close", "bar_advanced", "signal_flip", "resting_order", "profit_tier",
              "price_breakout", "position_watch", "heartbeat")


@dataclass(frozen=True)
class WakeReadings:
    """调用方（会话）能直接读到的本次唤醒断面（聚合结构体，守 CLAUDE.md「形参 ≤5」）。

    刻意**只**收 executor 与窗口的现成读数：利润保护快照 status 与 ATR 由 `WakeRuntime` 从
    `note_guard_status` / `note_atr` 记下的值回读，调用方不得另取一次（同一个量两处各取一次是本仓库
    反复踩过的漂移源）。
    """

    trigger: str                       # "candle" | "interval"（既有二值，wire 不变）
    position_side: str                 # executor.projected
    has_resting: bool                  # 是否有未成交限价委托
    bar_index: int                     # 当前最近确认根的窗口索引
    px: float | None                   # 本次唤醒将使用的实时价
    resting_age_s: float | None = None # 挂单已挂秒数（无挂单为 None）


@dataclass(frozen=True)
class WakeFacts:
    """判定所需的全部事实（由 `WakeRuntime.classify` 组装，纯函数只读不取）。"""

    trigger: str = "interval"
    position_side: str = "flat"
    has_resting: bool = False
    bar_index: int = 0
    last_round_bar_index: int | None = None
    px: float | None = None
    last_round_px: float | None = None
    atr_px: float | None = None
    guard_status: str | None = None           # 本 tick 的利润保护快照 status
    last_round_guard_status: str | None = None
    since_last_round_s: float = 0.0
    resting_age_s: float | None = None
    # 轴 C：未确认根预演出的投影净共识翻转（`signal_preview.flip_signature` 的 wire + 防抖读数）。
    # None ⇒ `flip` 整键缺席 ⇒ 非翻转轮的 wire 逐字节回退 a7ed70c。
    flip: dict | None = None


def horizon_scale(wake_interval_s: float, bar_seconds: int) -> float:
    """把「执行周期一根的 ATR」折算到「一个唤醒间隔」的时间尺度（随机游走 √t 律）。

    不折算是量纲错误而非精度问题：ATR 度量的是一根执行周期的典型波幅，而 `|Δpx|` 度量的是一个唤醒
    间隔内的位移，两者时间跨度可差三个数量级。1D 执行 + 2 分钟唤醒下 `0.5×ATR(1D)` ≈ 1.5%，两分钟内
    不可能越过 ⇒ 空仓定时唤醒被**永久跳过**，而定时轮恰恰是战术周期证据服务的对象。

    Args:
        wake_interval_s: 唤醒间隔（秒）。
        bar_seconds: 执行周期一根的秒数。

    Returns:
        `sqrt(min(1, wake/bar))`；`wake >= bar` ⇒ 1.0（**只缩不放**：唤醒比 K 线还慢时，阈值不该
        超过一根的典型波幅）；任一入参非正 ⇒ 1.0（退化为原始 ATR 判据，与 fail-open 极性一致）。
    """
    if wake_interval_s <= 0 or bar_seconds <= 0:
        return 1.0
    return math.sqrt(min(1.0, float(wake_interval_s) / float(bar_seconds)))


def moved(facts: WakeFacts, *, move_atr_mult: float, move_pct: float,
          horizon_scale: float = 1.0) -> tuple[bool, float | None, float | None]:
    """价格是否已走出「值得再问一次 AI」的位移（守卫与措辞的**唯一**判定真源）。

    Args:
        facts: 本次唤醒事实（用 `px` / `last_round_px` / `atr_px` 三项）。
        move_atr_mult: ATR 判据倍数；`<=0` 或 ATR 不可用时逐字回退 pct 判据。
        move_pct: pct 回退判据（fraction，0.0005=0.05%），与 `should_skip_interval_wake` 同式。
        horizon_scale: `horizon_scale()` 的折算系数，只作用于判据侧、不改返回的物理量。

    Returns:
        `(是否越阈, 相对变动百分数（带符号）, 折合 ATR 倍数——**未**经 scale 折算的原始倍数)`。
        价不可信（任一价为 None 或 <=0）⇒ `(True, None, None)`：价拿不准时宁可唤醒（与 F4c 现行
        极性逐字一致）。第三元刻意是原始倍数——它是给人和模型看的物理量（「走了 0.91 根 30m ATR」），
        折算只发生在判据侧；两者都随 wire 下发，读者能自行反算门槛，杜绝「静默门禁」。
    """
    px = facts.px
    last = facts.last_round_px
    if px is None or last is None or px <= 0 or last <= 0:
        return True, None, None
    delta = abs(px - last)
    pct = round((px / last - 1.0) * 100.0, 6)
    atr_px = facts.atr_px
    atr_mult = round(delta / atr_px, 6) if atr_px is not None and atr_px > 0 else None
    if atr_mult is not None and move_atr_mult > 0:
        return delta >= move_atr_mult * atr_px * horizon_scale, pct, atr_mult
    return abs(px / last - 1.0) >= move_pct, pct, atr_mult


def classify_wake(facts: WakeFacts, *, move_atr_mult: float, move_pct: float,
                  horizon_scale: float = 1.0) -> dict:
    """判定本次唤醒的原因，产出结构化 wire（camelCase，进 payload / 事件 / 轨迹 / 归因）。

    判定顺序即优先级（首个命中即为 `kind`），其余同时成立的条件进 `alsoTrue`——判定只决定标签，
    **绝不丢证据**。`heartbeat` 是兜底态（空仓 + 无挂单 + 无新根 + 未越阈），它本身就是那四条的合取，
    故 `wake_guard.should_skip_wake` 直接读它、不再重算一次。

    Args:
        facts: 本次唤醒事实（`WakeRuntime.classify` 组装）。
        move_atr_mult: 价格异动的 ATR 判据倍数（配置值，未折算）。
        move_pct: ATR 不可用时的 pct 回退判据（fraction）。
        horizon_scale: 时间尺度折算系数（见 `horizon_scale()`）。

    Returns:
        唤醒 wire dict；`skipStreak` 恒为 0，由 `WakeRuntime` 按会话计数覆盖。
    """
    is_moved, pct, atr_mult = moved(facts, move_atr_mult=move_atr_mult,
                                    move_pct=move_pct, horizon_scale=horizon_scale)
    bar_advanced = (facts.last_round_bar_index is None
                    or facts.bar_index != facts.last_round_bar_index)
    in_position = facts.position_side != "flat"
    conditions = (
        ("candle_close", facts.trigger == "candle"),
        ("bar_advanced", bar_advanced),
        ("signal_flip", facts.flip is not None),
        ("resting_order", facts.has_resting),
        ("profit_tier", in_position
         and facts.guard_status != facts.last_round_guard_status
         and facts.guard_status in ARMED_STATUSES),
        ("price_breakout", is_moved),
        ("position_watch", in_position),
    )
    fired = [name for name, ok in conditions if ok]
    kind = fired[0] if fired else "heartbeat"
    wire = {
        "kind": kind,
        "trigger": facts.trigger,
        "executionBar": None,           # 由 WakeRuntime 回填（文案的 ATR 量纲周期名，见 wake_text）
        "sinceLastRoundS": round(max(0.0, float(facts.since_last_round_s)), 3),
        "priceMovePct": pct,
        "priceMoveAtr": atr_mult,
        "moveThresholdAtr": move_atr_mult,
        "moveThresholdPct": move_pct,
        "horizonScale": round(horizon_scale, 6),
        "moveThresholdAtrEff": round(move_atr_mult * horizon_scale, 6),
        "skipStreak": 0,
        "barAdvanced": bar_advanced,
        "restingAgeS": facts.resting_age_s,
        "guardStatus": facts.guard_status,
        "lastGuardStatus": facts.last_round_guard_status,
        "alsoTrue": list(fired[1:]),
    }
    if facts.flip is not None:      # 缺席即整键不出现（与 tacticalContext / baseRules 同款范式）
        wire["flip"] = facts.flip
    return wire
