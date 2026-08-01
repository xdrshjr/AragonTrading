"""空仓重复唤醒跳过守卫（ai-trade-reflection-hold-inertia-fix F4c，无副作用纯函数）。

诊断根因 ③：墙钟定时唤醒不判「行情是否有新数据」，同一份 15 分钟数据被重复决策约 5 次，每次都
产出一条 hold 流水进入 journal 与摘要素材，把「继续观望」当作被反复验证的独立证据。本守卫在
`session._maybe_interval_wake` 认领 `_deciding` 槽位之前调用：空仓、无挂单、自上一决策轮以来
无新确认根、且实时价相对上一决策轮变动小于阈值时跳过本次唤醒（省一次 LLM 调用 + 少一条重复
hold 流水）。持仓/挂单轮永不跳过（盯市职责不受影响）；`threshold=0` 完全关闭守卫（逐字回退
现行为）。镜像 `reflection.py` 的「小纯函数模块」先例——session.py 已越 800 行上限，不再喂逻辑。
"""
from __future__ import annotations


def should_skip_interval_wake(*, position_side: str, has_resting: bool,
                              bar_index: int, last_round_bar_index: int | None,
                              px: float | None, last_round_px: float | None,
                              threshold: float) -> bool:
    """本次定时唤醒是否应跳过（True=跳过）；任一条件不满足即唤醒（False）。

    Args:
        position_side: 当前投影持仓方向（executor.projected；仅 "flat" 可跳过）。
        has_resting: 是否有未成交挂单（挂单轮永不跳过——Agent 须能盯它并择机撤单）。
        bar_index: 当前最近确认根的窗口索引。
        last_round_bar_index: 上一决策轮启动时的确认根索引；None（尚无决策轮）→ 不跳过。
        px: 本次唤醒将使用的实时价；非有效正数 → 不跳过（价不可信时宁可唤醒）。
        last_round_px: 上一决策轮启动时的参考价；同上。
        threshold: 价格变动阈值（fraction，如 0.0005=0.05%）；<=0 = 守卫关闭，恒不跳过。

    Returns:
        True 当且仅当：守卫开启、空仓、无挂单、无新确认根、且两价均有效且相对变动 < threshold。
    """
    if threshold <= 0:
        return False
    if position_side != "flat" or has_resting:
        return False
    if last_round_bar_index is None or last_round_bar_index != bar_index:
        return False
    if px is None or last_round_px is None or px <= 0 or last_round_px <= 0:
        return False
    return abs(px / last_round_px - 1.0) < threshold


def should_skip_wake(wake: dict, *, threshold_pct: float, threshold_atr_mult: float,
                     max_skip_streak: int = 0) -> bool:
    """结构化唤醒对象版守卫（ai-trade-wake-aware-context §2.5）；上方既有函数语义一字不改。

    判据**不在本函数里重算**——`kind == "heartbeat"` 本身就是「空仓 + 无挂单 + 无新根 + 未越阈」的
    合取，`wake_reason.classify_wake` 已经算过一次。两处各算一次正是「prompt 说数据没变、守卫却认为
    变了」这类信任崩塌的经典成因。

    Args:
        wake: `classify_wake` 产物（读 `kind` 与 `skipStreak` 两个键）。
        threshold_pct: pct 回退判据（fraction）；与下一项同时 <=0 即**关守卫**（恒不跳过）。
        threshold_atr_mult: ATR 判据倍数（未折算的配置值）。
        max_skip_streak: 连续跳过上限，到即强制放行一轮（饿死兜底）；0 = 不封顶。

    Returns:
        True 当且仅当：守卫开启、`kind == "heartbeat"`、且连续跳过数未触到上限。

    注（饿死兜底）：ATR 判据把跳过率显著推高后，需要一条**上界**保证模型在长时间静止行情里不被完全
    饿死——否则「AI 自动交易」会在盘整期变成事实上的停机，而用户看不到任何原因。放行轮的 `kind` 仍
    如实是 `heartbeat`（条件化措辞正是为这种轮次写的），不需要新 kind。
    """
    if threshold_pct <= 0 and threshold_atr_mult <= 0:
        return False
    if wake.get("kind") != "heartbeat":
        return False
    if max_skip_streak > 0 and int(wake.get("skipStreak") or 0) >= max_skip_streak:
        return False
    return True
