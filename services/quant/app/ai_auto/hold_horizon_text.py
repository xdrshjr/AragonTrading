"""持仓周期铁律层的**中文文案渲染**（ai-trade-holding-horizon-modes §2.9）。

从 `hold_horizon.py` 切出的纯渲染半边（切法与 `profit_guard_text.py` 同款）：宪章章、每轮只读段、
反思复核官的补充段、自察注记。

不变式（不可回退）：**文案与判定共用同一份真源**——本模块只读 `build_snapshot` 的产物，
**绝不重算任何一个数**（三处各算一份必然漂移，`base_rules` 的 [R-5] 教训）。未装配 ⇒ 全部入口返回
空串垫片 ⇒ 系统 / 决策 / 反思三份 prompt 逐字节回退（G-1 的 sha256 冻结门禁据此成立）。
"""
from __future__ import annotations

from .hold_horizon import (
    RELEASE_EPOCH_ESTIMATED,
    RELEASE_LOSS,
    RELEASE_MAX_HOLD_BARS,
    RELEASE_STALE_PX,
    STATUS_FLAT,
    HorizonRule,
    loss_release_return_pct,
)

# 跨日门槛（秒）：目标持仓上界达到一整天即视为「跨日持仓」，宪章须点名交易所侧兜底（§2.8）。
_CROSS_DAY_SECONDS = 86400.0

_RELEASE_LABEL = {
    RELEASE_LOSS: "浮亏已达解除阈",
    RELEASE_STALE_PX: "判定价不可信",
    RELEASE_EPOCH_ESTIMATED: "持仓起点为估算值",
    RELEASE_MAX_HOLD_BARS: "已达强制时间平仓上限",
}


def _fmt_duration(seconds) -> str:
    """秒 → 人话时长（`7200` ⇒ `2 小时`）；非正 / 不可数 ⇒ `—`。"""
    try:
        value = float(seconds)
    except (TypeError, ValueError):
        return "—"
    if value <= 0:
        return "—"
    if value < 3600:
        return f"{value / 60:.0f} 分钟"
    if value < 86400:
        return f"{value / 3600:g} 小时"
    return f"{value / 86400:g} 天"


def _fmt_pct(value) -> str:
    try:
        return f"{float(value) * 100:.2f}%"
    except (TypeError, ValueError):
        return "—"


def _fmt_px(value) -> str:
    return "—" if value is None else f"{float(value):.6f}"


def format_charter(rule: HorizonRule | None, *, stop_loss_pct: float | None = None,
                   take_profit_pct: float | None = None, leverage: float = 1.0) -> str:
    """系统 prompt 的持仓周期宪章章（§2.9）；未装配 → **空串垫片**（渲染逐字节不变、零回归）。

    软引导层：让 Agent 知道并主动遵守目标周期（否则它会反复撞墙、白烧轮次）；硬保证仍是
    `evaluate_horizon_gate`。**必须如实披露解除阈**——本层是给真实持仓上的锁，模型有权知道钥匙在哪。

    Args:
        rule: 已装配契约；`None` / 未启用 → `""`。
        stop_loss_pct: 单仓交易所侧 OCO 止损%（`loss_release` 阈的派生源；`None` = 走服务端默认）。
        take_profit_pct: 单仓 OCO 止盈%（跨日模式的兜底披露用）。
        leverage: 本会话杠杆（保证金收益率口径的换算因子）。
    """
    if rule is None or not rule.enabled:
        return ""
    release = loss_release_return_pct(rule, stop_loss_pct=stop_loss_pct, leverage=leverage)
    return (
        f"**交易周期模式（{rule.title}·由系统强制执行）**：本会话的目标持仓是 "
        f"{rule.target_label or _target_text(rule)}。你**不是**在做分钟级的高频交易——每一次开平都要"
        "付两笔确定的手续费，而线上样本显示：毛利润为正、却被换手成本吃成净亏损。因此本会话对"
        "**你主动发起的平仓与反手**施加三条确定性约束：\n"
        f"{_gate_lines(rule)}"
        f"  · 浮亏达 −{_fmt_pct(release)}（保证金收益率）时，以上三条**全部解除**，你可随时离场。\n"
        "**这三条只约束你自己发起的换手**：交易所侧止损止盈、利润保护层、会话级止盈止损、强制时间"
        "平仓一律不受本层限制，它们随时可以把仓位平掉。**利润保护层有它自己的最短持有期，与本层无关、"
        "不受本层影响**。\n"
        f"{_exchange_line(rule, stop_loss_pct, take_profit_pct)}"
        "**单根分钟级回撤不构成平仓理由**：执行周期与更高周期的持仓逻辑未失效之前，战术周期的反向"
        "信号只应影响你的离场**价位**，不应影响你的离场**决定**。需要离场时，请先说明「持仓理由已经"
        "失效」的新证据，再给出 `close`。"
    )


def _target_text(rule: HorizonRule) -> str:
    """`custom` 无 `target_label` 时的兜底描述（全默认 ⇒ 如实说「未设定」，绝不编一个区间）。"""
    if rule.target_hold_min_s <= 0 and rule.target_hold_max_s <= 0:
        return "由你自行把握（本会话未设定目标区间）"
    return f"{_fmt_duration(rule.target_hold_min_s)} – {_fmt_duration(rule.target_hold_max_s)}"


def _gate_lines(rule: HorizonRule) -> str:
    """三道闸的宪章行；空转的闸**整行省略**（写「本闸未启用」只是噪音）。"""
    lines = []
    if rule.min_hold_s > 0:
        lines.append(f"  · 开仓后 {_fmt_duration(rule.min_hold_s)} 内不接受你主动平仓。\n")
    if rule.exit_confirm_rounds > 1:
        window = (f"，最多等待 {_fmt_duration(rule.confirm_window_s)}（到时即按你的意愿放行）"
                  if rule.confirm_window_s > 0 else "")
        lines.append(f"  · 离场理由需连续 {rule.exit_confirm_rounds} 轮成立才执行{window}。\n")
    if rule.hysteresis_k > 0:
        lines.append(f"  · 获利了结需浮盈覆盖 {rule.hysteresis_k:g}× 往返成本"
                     "（浮亏离场不受此限）。\n")
    if not lines:
        lines.append("  · 本会话未开启任何硬性换手约束（自定义模式全默认）。\n")
    return "".join(lines)


def _exchange_line(rule: HorizonRule, stop_loss_pct, take_profit_pct) -> str:
    """跨日模式的交易所侧兜底披露（§2.8）；非跨日 ⇒ 空串（不写与本轮无关的噪音）。

    诚实约束（沿用 `_format_unavailable_fallback` 的 v1 终审 RV-1）：两个 pct 皆为空 ⇒ 交易所侧
    **真的没挂单**，此时绝不能宣称还有一条并不存在的兜底。
    """
    if rule.target_hold_max_s < _CROSS_DAY_SECONDS:
        return ""
    parts = []
    if stop_loss_pct:
        parts.append(f"止损 {_fmt_pct(stop_loss_pct)}")
    if take_profit_pct:
        parts.append(f"止盈 {_fmt_pct(take_profit_pct)}")
    if not parts:
        return ("**本会话跨日持有，但交易所侧未挂止盈止损**——进程退出后没有任何持久化兜底，"
                "退出完全由你与本进程负责。\n")
    return (f"本会话跨日持有，开仓时已随单挂出交易所侧{' / '.join(parts)}——"
            "即使本进程退出它们依然生效，故你**不必每轮盯盘**，可以按目标周期规划退出。\n")


def format_horizon_for_prompt(wire: dict | None) -> str:
    """决策 prompt 的每轮只读上下文段（§2.9）；键缺失 ⇒ `""` ⇒ 调用方整段省略（逐字节回退）。

    无持仓时只出一行「本轮空仓、本层不约束开仓」——那正是模型最需要知道的一件事：**开仓不受本层
    限制**，不写会让它以为周期模式也在拦开单。
    """
    if not wire:
        return ""
    head = f"【交易周期 · {wire.get('title')}（{wire.get('targetLabel') or '自定义'}）】"
    if wire.get("status") == STATUS_FLAT:
        return f"{head}\n  当前空仓：本层**不约束开仓**；开仓后将按上述目标周期约束你的主动平仓。"
    lines = [head, _hold_line(wire), _confirm_line(wire), _hysteresis_line(wire),
             _release_line(wire), _exchange_snapshot_line(wire), _blocked_note_line(wire)]
    return "\n".join(part for part in lines if part)


def _hold_line(wire: dict) -> str:
    """G1 一行：已持仓多久、还差多久（数全部取自快照，不重算）。"""
    hold = _fmt_duration(wire.get("holdSeconds"))
    bars = wire.get("barsInPosition")
    bars_text = "" if bars is None else f"（{bars} 根）"
    remaining = float(wire.get("minHoldRemainingSeconds") or 0.0)
    if float(wire.get("minHoldSeconds") or 0.0) <= 0:
        return f"  已持仓 {hold}{bars_text}；本会话未设最短持仓窗口。"
    gate = ("最短持仓窗口已满足，可按你的判断离场"
            if remaining <= 0 else f"距最短持仓窗口还差 {_fmt_duration(remaining)}")
    return f"  已持仓 {hold}{bars_text}｜{gate}。"


def _confirm_line(wire: dict) -> str:
    """G2 一行；`exit_confirm_rounds <= 1` ⇒ 整行省略（本闸空转）。"""
    rounds = int(wire.get("exitConfirmRounds") or 1)
    if rounds <= 1:
        return ""
    streak = int(wire.get("exitConfirmStreak") or 0)
    if wire.get("confirmWindowElapsed"):
        return f"  离场连续确认：{streak}/{rounds}，**确认窗口已到，本轮按你的意愿放行**。"
    remaining = wire.get("confirmWindowRemainingSeconds")
    window = ("" if remaining is None
              else f"；确认窗口剩余 {_fmt_duration(remaining)}（到时即放行）")
    return f"  离场连续确认：{streak}/{rounds}{window}。"


def _hysteresis_line(wire: dict) -> str:
    """G3 一行；k=0 或成本不可算 ⇒ 整行省略（fail-open，不写「本闸不生效」的噪音）。"""
    required = wire.get("requiredMoveBps")
    if required is None:
        return ""
    move = wire.get("moveBps")
    move_text = "—" if move is None else f"{float(move):.1f}bps"
    if move is not None and float(move) <= 0:
        return (f"  换手迟滞：当前为浮亏（{move_text}），**认错离场不受迟滞约束**"
                "，需要止损随时可以 close。")
    return (f"  换手迟滞：获利了结需浮盈 ≥ {float(required):.1f}bps"
            f"（{wire.get('turnoverHysteresisK')}× 往返成本 {wire.get('costBps')}bps），"
            f"当前 {move_text}。")


def _release_line(wire: dict) -> str:
    """豁免一行：命中即如实标注解了哪几道闸；未命中则告知解除阈在哪（模型有权知道钥匙）。"""
    threshold = _fmt_pct(wire.get("lossReleaseReturnPct"))
    if not wire.get("released"):
        return f"  以上限制在浮亏达 −{threshold}（保证金收益率）时全部解除。"
    reason = wire.get("releaseReason")
    gates = "、".join(wire.get("releasedGates") or ()) or "—"
    return (f"  ★ 本轮限制已解除（{_RELEASE_LABEL.get(reason, reason)}；解除 {gates}）"
            "——你可以按自己的判断离场。")


def _exchange_snapshot_line(wire: dict) -> str:
    """交易所侧兜底一行（只读转发 `profit_guard` 快照，§2.8 第 3 条；两者皆空 ⇒ 如实说没有）。"""
    sl, tp = wire.get("exchangeStopLossPx"), wire.get("exchangeTakeProfitPx")
    if sl is None and tp is None:
        return "  交易所侧未挂止盈止损——此刻没有任何持久化兜底。"
    return f"  交易所侧兜底：止盈 {_fmt_px(tp)} / 止损 {_fmt_px(sl)}（进程退出后仍生效）。"


def _blocked_note_line(wire: dict) -> str:
    """连续被拦时的自察注记（§2.9(e)）；键缺席 ⇒ 空串 ⇒ prompt 逐字节回退。

    它**不改变任何判定**，只是让模型知道自己在撞墙——否则 G2 的连续确认计数是一个模型察觉不到的
    黑箱，它会在每一轮重复同一次点击。
    """
    if not wire.get("blockedStreakNote"):
        return ""
    remaining = float(wire.get("minHoldRemainingSeconds") or 0.0)
    gap = ("" if remaining <= 0
           else f"（最短持仓窗口还差 {_fmt_duration(remaining)}）")
    return (f"  ★ 你已连续 {wire.get('blockedStreak')} 轮提出离场而被持仓周期约束拦下{gap}。"
            "这不是让你重复同一个判断——请要么明确指出「持仓理由已经失效」的**新证据**，"
            "要么把这段时间用于规划离场**价位**（限价委托 / 目标位），而不是每一轮重复同一次点击。")


def format_reflection_note(wire: dict | None) -> str:
    """反思复核官系统 prompt 的补充段（§2.9(c)）；未装配 ⇒ `""` ⇒ 反思 prompt 逐字节回退。

    只**追加**不替换既有的「close 是风险收敛动作」一段：那一段仍然成立，本段补的是它缺失的对称性
    ——「过早的 close」与「该平不平」是两种同样真实的错误。
    """
    if not wire:
        return ""
    target = wire.get("targetLabel") or "本会话设定的区间"
    return (
        f"\n本会话设定了持仓周期模式（{wire.get('title')}，目标持仓 {target}）。因此「过早的 "
        "`close`」与「该平不平」是**对称**的两种错误：前者用两笔确定的手续费换一个未成熟的判断。"
        "复核一笔主动平仓时，请同样检查它的依据是否只是分钟级噪音、是否与该模式的时间尺度相称。"
        "系统另有确定性的最短持仓窗口与连续确认约束，你无需替它执行，但你的裁决不应与它相悖。"
    )


def format_backtest_notes(rule: HorizonRule | None, bar_seconds: int) -> list[str]:
    """回测结果的分辨率免责（§2.10 / [P2-7]）；未装配 ⇒ `[]`。

    **必须点名具体模式**：`hourly_swing`（5m 执行 + 180s 窗口）在回测里 G1 恒不生效，不写清楚用户
    仍会拿这份回测去为实盘的 G1 背书。
    """
    if rule is None or not rule.enabled:
        return []
    notes = [
        f"交易周期模式「{rule.title}」在回测中按根推进：持仓时长的分辨率就是一根 K 线，故最短持仓"
        f"窗口的实际生效粒度是 ceil({rule.min_hold_s:g}s / 一根)。回测无 5 秒盯市、无部分成交、"
        "无交易所降级态，触发时机必然更粗。"]
    if 0 < rule.min_hold_s < bar_seconds:
        notes.append(f"⚠ 本模式（{rule.profile_id}）的最短持仓窗口 {rule.min_hold_s:g}s 小于一根 K 线"
                     f"（{bar_seconds}s），**回测中 G1 恒不生效**；本次回测结论只覆盖离场连续确认与"
                     "换手迟滞两道闸，不得据此为实盘的最短持仓窗口背书。")
    if 0 < rule.confirm_window_s < bar_seconds:
        notes.append(f"⚠ 本模式的离场确认窗口 {rule.confirm_window_s:g}s 小于一根 K 线，回测中该窗口"
                     "恒于次根即已到期，连续确认闸相对实盘偏松。")
    return notes
