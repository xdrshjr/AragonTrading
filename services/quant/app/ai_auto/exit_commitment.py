"""离场意图兑现纯核心（ai-trade-exit-priority-and-commitment 轴 C · §4.5）。

今天利润保护层的平仓已经享有「未成交即下一次盯市重试」的待遇（`_report_profit_exit` 至少会如实说
「本次平仓未成交，将在下一次盯市重试」），而 **Agent 自己的平仓是一次性的**：`_do_close` 有四条
不平仓的返回路径，其中「原挂单撤销未获确认」那一条连降级码都没有——轨迹上会显示「决策工具 · 平仓」
而没有任何成交、没有任何降级码。本模块把离场从「一次性动作」升级为**有状态、会重试、有超时告警的
意图**，并把这条待遇对齐。

两条不可动摇的边界（改本文件前先读）：

1. **只对市价平仓武装**（【R:P0-3】）。限价平仓走 `_do_close` 的 `limit_px is not None` 分支 ⇒
   `_enter_resting`，**当轮必然没有成交事件**、`projected` 保持原方向。若照「未见成交即武装」写，
   本层必然武装，`retry_interval_s` 到点后 `executor.apply([Signal(side="close")])` **不带
   `order_plan`** ⇒ 市价 ⇒ 与原挂单 `is_same_intent` 不成立 ⇒ **撤掉模型自己挂的限价平仓单并按
   市价平掉**。整个 limit-orders 特性会在离场侧被静默废掉，而界面上还显示着那张限价单。
   `should_retry` 的 `resting_close` 短路是同一条防线的第二层，**两层都要**。
2. **绝不新增下单写路径**（X-3）。兑现重试复用 `executor.apply([Signal(side="close")])` →
   `_do_close`，与 `session._apply_profit_exit` 逐字同款。

本模块无 IO、无 await：状态推进与判据全是纯函数，会话侧只做编排（W-5 武装 / W-6 兑现）。
"""
from __future__ import annotations

from dataclasses import dataclass, replace

# WS 告警码（面板红条 + 【立即离场】按钮的出现条件）。
EXIT_STUCK_CODE = "AI_TRADE_EXIT_STUCK"


@dataclass(frozen=True)
class ExitCommitmentRule:
    """会话级不可变契约；`None` ⇒ 本层不装配 ⇒ 恒不武装、wire 整键缺席。"""

    enabled: bool = True
    retry_interval_s: float = 10.0
    max_attempts: int = 6
    max_age_s: float = 300.0


@dataclass(frozen=True)
class ExitCommitment:
    """离场意图的状态机（空态 = 未武装）。

    Attributes:
        epoch: `(side, seq)` 身份戳，与持仓周期层 / 利润保护层**完全同源**——epoch 一变即作废，
            否则下一笔持仓会继承上一笔的离场意图并被当场平掉。
        armed_wall: 武装时刻（墙钟秒；`max_age_s` 的起点）。
        reason_code: 触发本次离场的放行 / 降级码（供成交 explain 与退出归因）。
        priority_level: 触发本次离场的最终优先级档位（进 wire 与归因）。
        attempts: 已尝试次数（含首次下发之后的每一次兑现重试）。
        last_attempt_wall: 最近一次尝试的墙钟（`retry_interval_s` 的起点）。
        last_error: 最近一次尝试的错误摘要（`None` = 未报错，只是没成交）。
        stuck: 已到顶（次数或时长），**不再重试**，改由面板红条 + 一键离场接手。
    """

    epoch: tuple[str, int] | None = None
    armed_wall: float = 0.0
    reason_code: str = ""
    priority_level: str = "normal"
    attempts: int = 0
    last_attempt_wall: float = 0.0
    last_error: str | None = None
    stuck: bool = False

    @property
    def armed(self) -> bool:
        return self.epoch is not None


def arm(state: ExitCommitment, *, epoch: tuple[str, int] | None, now_wall: float,
        reason_code: str, priority_level: str) -> ExitCommitment:
    """武装一次离场意图；**同一 epoch 上已武装则原样返回**（不重置计时与尝试次数）。

    不重置是刻意的：连续两轮都提 `close` 而两轮都没成交时，`max_age_s` 必须从**第一次**起算，
    否则「已确认离场却兑现不了」这件事永远到不了告警。
    """
    if epoch is None:
        return ExitCommitment()
    if state.armed and state.epoch == epoch:
        return state
    return ExitCommitment(epoch=epoch, armed_wall=float(now_wall),
                          reason_code=str(reason_code or ""),
                          priority_level=str(priority_level or "normal"),
                          last_attempt_wall=float(now_wall))


def should_retry(state: ExitCommitment, rule: ExitCommitmentRule | None, *,
                 position_side: str, epoch: tuple[str, int] | None, now_wall: float,
                 resting_close: bool) -> bool:
    """本次盯市 tick 是否应当再打一次市价平仓。

    自上而下的**否**判据（任一成立即 `False`）：未装配 / 未武装 / 已 `stuck` / 空仓 / epoch 变化 /
    **有平仓挂单在飞** / 未到重试间隔。

    `resting_close=True` ⇒ 恒 `False`（【R:P0-3】）：挂单在飞就是「正在兑现」，本层绝不去撤掉模型
    自己挂的限价平仓单；TTL 到期由既有 `_poll_resting` 收敛。
    """
    if rule is None or not rule.enabled or not state.armed or state.stuck:
        return False
    if position_side not in ("long", "short") or epoch is None or state.epoch != epoch:
        return False
    if resting_close:
        return False
    return float(now_wall) - float(state.last_attempt_wall) >= float(rule.retry_interval_s)


def note_attempt(state: ExitCommitment, rule: ExitCommitmentRule | None, *,
                 now_wall: float, filled: bool, error: str | None) -> ExitCommitment:
    """记一次兑现尝试；成交 ⇒ 返回**空态**（意图完成）；否则计数并按双上限置 `stuck`。"""
    if filled or not state.armed:
        return ExitCommitment()
    attempts = int(state.attempts) + 1
    aged = (rule is not None
            and float(now_wall) - float(state.armed_wall) >= float(rule.max_age_s))
    exhausted = rule is not None and attempts >= int(rule.max_attempts)
    return replace(state, attempts=attempts, last_attempt_wall=float(now_wall),
                   last_error=(str(error) if error else None),
                   stuck=bool(aged or exhausted))


def on_epoch_change(state: ExitCommitment, epoch: tuple[str, int] | None) -> ExitCommitment:
    """持仓身份变化（含归 flat）⇒ 作废意图。空态幂等，可在每个 tick 无脑调用。"""
    return state if (state.armed and state.epoch == epoch) else ExitCommitment()


def build_wire(state: ExitCommitment, rule: ExitCommitmentRule | None, *,
               now_wall: float) -> dict | None:
    """§5.3 形状的 wire；未装配 / 未武装 ⇒ `None` ⇒ 调用方**整键缺席**（不是 `null`）。"""
    if rule is None or not rule.enabled or not state.armed:
        return None
    return {"armed": True, "attempts": int(state.attempts),
            "maxAttempts": int(rule.max_attempts),
            "ageSeconds": round(max(0.0, float(now_wall) - float(state.armed_wall)), 3),
            "maxAgeSeconds": float(rule.max_age_s),
            "retryIntervalSeconds": float(rule.retry_interval_s),
            "priorityLevel": state.priority_level, "reasonCode": state.reason_code,
            "lastError": state.last_error, "stuck": bool(state.stuck)}


def rule_from_settings(settings) -> ExitCommitmentRule | None:
    """运维总闸 → 契约（**唯一装配点**）；关 ⇒ `None` ⇒ 整层空转、wire 整键缺席。"""
    if not getattr(settings, "ai_trade_exit_commitment_enabled", False):
        return None
    return ExitCommitmentRule(
        enabled=True,
        retry_interval_s=float(settings.ai_trade_exit_commitment_retry_interval_s),
        max_attempts=int(settings.ai_trade_exit_commitment_max_attempts),
        max_age_s=float(settings.ai_trade_exit_commitment_max_age_s))
