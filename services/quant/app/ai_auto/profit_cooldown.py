"""保护性平仓后的**冷却期**纯核心（ai-trade-profit-protection-adaptive §2.2 / F2）。

第 1 轮上线后暴露的一个形状问题：保护层平仓 → 同一根 K 线内 Agent 因为「信号仍看多」立刻重新开同向
仓 → 价格继续回落 → 二次止损。刚锁住的利润经两次手续费与一次反向波动被抹平。

本模块与 `base_rules.apply_ma_band_gate` / `signals.apply_consensus_gate` **同一范式**：一个纯函数、
一个降级码、把开仓降级为 hold，绝不新增任何下单路径。两条铁律：

1. **`close` / `hold` / `cancel_order` 永不被拦**（`signals.py` 那条「共识不足也永远能平仓保护」的
   先例）——冷却是对「系统替你做的决定」的补偿，不是对模型离场能力的剥夺。
2. **只有保护层自己的平仓才开启冷却**：Agent 主动 `close`、会话级止盈止损、外部平仓一律不开启
   （否则就成了对模型正常交易的惩罚）。写点唯一，在 `_report_profit_exit` 的 `closed=True` 分支。

实盘与回测**共用本模块**（回测把 bar ts 当墙钟，§2.3.2），严禁在回测侧复制一份判定。
"""
from __future__ import annotations

from dataclasses import dataclass

from .decider import TradeDecision

# 被冷却门禁作用的动作（与 base_rules._GATED_ACTIONS 同一集合；其余动作恒直通）。
_GATED_ACTIONS = ("open_long", "open_short")
_SIDE_LABEL = {"long": "做多", "short": "做空"}


@dataclass
class CooldownState:
    """一次保护性平仓开启的冷却态（两个标量 + 归因；纯时间驱动，无需清理任务）。

    Attributes:
        until_wall: 冷却结束墙钟（回测为 bar ts）。
        side: 被保护层平掉的方向（`long` | `short`）。
        reason_code: 触发本次保护平仓的规则码（`profit_guard_trailing` 等）。
        label: 该规则的中文短标签（「移动止盈」/「保本线」/「硬止盈」）。
        scope: `same_side`（只拦同向开仓，默认）| `both`（两个方向都拦）。
    """

    until_wall: float
    side: str
    reason_code: str
    label: str
    scope: str = "same_side"


def _is_active(state: CooldownState | None, now_wall: float) -> bool:
    """冷却是否仍在生效（`remaining <= 0` 即视为未激活；不做任何清理副作用）。"""
    return state is not None and now_wall < state.until_wall


def _blocks(state: CooldownState, action: str) -> bool:
    """本次开仓是否落在冷却的作用域内（`both` 拦两侧，`same_side` 只拦被平掉的那一侧）。"""
    if state.scope == "both":
        return True
    return action == ("open_long" if state.side == "long" else "open_short")


def apply_profit_cooldown(decision: TradeDecision, state: CooldownState | None, *,
                          now_wall: float) -> tuple[TradeDecision, str | None]:
    """利润保护冷却门禁：冷却期内的同向开仓硬降级为 hold（独立于 LLM，仅更保守）。

    未激活、动作在豁免矩阵内、或作用域不匹配时返回**同一个** decision 对象（`is` 相等，沿用
    `apply_ma_band_gate` 的既有约定），绝不顺手 `replace`。

    Args:
        decision: 本轮决策（已过铁律门禁）。
        state: 冷却态；`None` 或已到期 ⇒ 恒直通。
        now_wall: 当前墙钟（回测传当前根 ts，§2.3.2）。

    Returns:
        `(decision, gate_reason)`——`gate_reason` 形如 `profit_cooldown(120s,long)`；
        未降级则 `gate_reason is None`。
    """
    if decision.action not in _GATED_ACTIONS or not _is_active(state, now_wall):
        return decision, None
    if not _blocks(state, decision.action):
        return decision, None
    remaining = max(0, int(round(state.until_wall - now_wall)))
    reason = f"profit_cooldown({remaining}s,{state.side})"
    hold = TradeDecision(action="hold", size_fraction=None, confidence=None, reason=reason)
    return hold, reason


def build_cooldown_view(state: CooldownState | None, *, now_wall: float) -> dict | None:
    """冷却 wire 视图（§4.2）；未激活 ⇒ `None` ⇒ 三处载体一律**整键缺席 / null**。

    `remainingSeconds` 是**下发瞬间**的快照值：前端只用于显示，绝不据它自行判定是否仍在冷却
    （前后端两个时钟各自过期必然漂移，实际以后端每轮重算为准）。
    """
    if not _is_active(state, now_wall):
        return None
    return {"activeUntilTs": int(state.until_wall),
            "remainingSeconds": max(0, int(round(state.until_wall - now_wall))),
            "scope": state.scope, "blockedSide": state.side,
            "reasonCode": state.reason_code, "label": state.label}


def format_profit_cooldown(view: dict | None) -> str:
    """决策 prompt 的冷却段（§2.2.3）；未激活 ⇒ 空串 ⇒ 调用方整段省略（逐字节回退）。

    不写这段的后果是可预期的：模型每轮提议开仓、每轮被降级、每轮把「被拦截」当成新证据去自我怀疑，
    既烧 LLM 又污染上下文（`hold-inertia` 那一轮踩过的坑）。故必须同时给出**剩余时间**与**豁免范围**
    ——只说「被拦截」会让模型以为整个交易能力被剥夺。
    """
    if not view:
        return ""
    side = str(view.get("blockedSide") or "")
    blocked = _SIDE_LABEL.get(side, side)
    scope_text = ("**开仓（做多与做空）都会被系统拦截并降级为观望**" if view.get("scope") == "both"
                  else f"**{blocked}开仓会被系统拦截并降级为观望**"
                       f"（{_SIDE_LABEL.get('short' if side == 'long' else 'long', '反向')}不受影响）")
    return (f"【利润保护 · 冷却期】刚由{view.get('label')}平掉一笔{blocked}持仓。"
            f"接下来 {view.get('remainingSeconds')}s 内{scope_text}。"
            "这是为防止刚锁住的利润被一次立刻反手回吐；"
            "若你判断趋势确实延续，请等冷却结束后再以新的证据开仓。"
            "**平仓 `close`、观望 `hold`、撤单 `cancel_order` 不受冷却限制。**")
