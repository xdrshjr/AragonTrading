"""持仓周期铁律层的**中文文案渲染**（ai-trade-holding-horizon-modes §2.9）。

从 `hold_horizon.py` 切出的纯渲染半边（切法与 `profit_guard_text.py` 同款）：宪章章、每轮只读段、
反思复核官的补充段、自察注记。

不变式（不可回退）：**文案与判定共用同一份真源**——本模块只读 `build_snapshot` 的产物，
**绝不重算任何一个数**（三处各算一份必然漂移，`base_rules` 的 [R-5] 教训）。未装配 ⇒ 全部入口返回
空串垫片 ⇒ 系统 / 决策 / 反思三份 prompt 逐字节回退（G-1 的 sha256 冻结门禁据此成立）。
"""
from __future__ import annotations

import math

from .exit_intent import RELEASE_EXIT_PENDING
from .exit_priority import (
    CONVICTION_FINAL,
    CONVICTION_HIGH,
    DOWNGRADE_EVIDENCE,
    DOWNGRADE_LEDGER,
    DOWNGRADE_MIN_HOLD,
    DOWNGRADE_QUOTA,
    LEVEL_ADJUDICATED,
    LEVEL_EXPEDITE,
    LEVEL_NONE,
    LEVEL_NORMAL,
    LEVEL_OVERRIDE,
)
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

_SIDE_LABEL = {"long": "多头", "short": "空头"}

# 兜底核实不可得的原因标签（turnover-budget §2.3.3）；缺省 ⇒「尚未核实」。
_BACKSTOP_REASON_LABEL = {
    "disabled": "核实通道未开启",
    "no_creds": "未接入交易所凭证",
    "not_run": "本会话尚未跑过一次核实",
    "error": "最近一次核实失败",
    "stale": "最近一次核实已过期",
}

# 自愈层暂缓原因的 prompt 侧标签（backstop-repair §2.2.7）。值域与 `backstop_repair` 的常量、
# 前端标签表、端点错误体**四处镜像**——任一处新增取值而另三处未跟进，用例即红（放行条件 7）。
_REPAIR_BLOCK_LABEL = {
    "coverage_unknown": "交易所未回报可核对的委托张数",
    "no_local_px": "本地没有可用的止损价",
    "no_mark_px": "当前判定价不可用",
    "px_too_close": "止损价距离现价过近",
    "size_below_min": "未覆盖差额不足最小下单量",
    "order_in_flight": "有一笔限价委托在途",
    "cooling": "两次补挂之间的冷却尚未结束",
    "attempts_exhausted": "补挂次数已达上限",
    "verify_failed": "已补挂但核实仍未覆盖",
}

_RELEASE_LABEL = {
    RELEASE_LOSS: "浮亏已达解除阈",
    RELEASE_STALE_PX: "判定价不可信",
    RELEASE_EPOCH_ESTIMATED: "持仓起点为估算值",
    RELEASE_MAX_HOLD_BARS: "已达强制时间平仓上限",
    # exit-priority 轴 A 的第二把钥匙；漏加这一条，豁免轮 UI 只会说「限制已解除」而说不出为什么。
    RELEASE_EXIT_PENDING: "离场意图已挂起超过上界",
}

# 五档离场优先级的中文标签（前端一个字都不硬编，X-8）。
_LEVEL_LABEL = {
    LEVEL_NONE: "不适用", LEVEL_NORMAL: "P1 · 常规",
    LEVEL_EXPEDITE: "P2 · 加速", LEVEL_ADJUDICATED: "P3 · 复核官背书",
    LEVEL_OVERRIDE: "P3 · 最高优先级",
}

# 四种降档原因的中文（**必须如实进 prompt**：静默降档会让模型在下一轮重复完全相同的错误）。
_DOWNGRADE_LABEL = {
    DOWNGRADE_EVIDENCE: "未给出可核对的新证据",
    DOWNGRADE_LEDGER: "跨轮离场账本还不够厚",
    DOWNGRADE_QUOTA: "本会话最高优先级配额已用尽",
    DOWNGRADE_MIN_HOLD: "距开仓不足绝对地板时长",
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


def _fmt_ago(seconds) -> str:
    """「多久之前」的人话；秒级也要说得出口（`_fmt_duration` 在 60s 以下会把 42s 说成「1 分钟」，
    而兜底核实的新鲜度恰恰是这一段最有信息量——「42s 前核实过」与「1 分钟前」读感差很远）。"""
    try:
        value = float(seconds)
    except (TypeError, ValueError):
        return "刚刚"
    return f"{value:.0f}s" if value < 60 else _fmt_duration(value)


def _fmt_pct(value) -> str:
    try:
        return f"{float(value) * 100:.2f}%"
    except (TypeError, ValueError):
        return "—"


def _fmt_px(value) -> str:
    return "—" if value is None else f"{float(value):.6f}"


def format_charter(rule: HorizonRule | None, *, stop_loss_pct: float | None = None,
                   take_profit_pct: float | None = None, leverage: float = 1.0,
                   backstop_armed: bool = False) -> str:
    """系统 prompt 的持仓周期宪章章（§2.9）；未装配 → **空串垫片**（渲染逐字节不变、零回归）。

    软引导层：让 Agent 知道并主动遵守目标周期（否则它会反复撞墙、白烧轮次）；硬保证仍是
    `evaluate_horizon_gate`。**必须如实披露解除阈**——本层是给真实持仓上的锁，模型有权知道钥匙在哪。

    Args:
        rule: 已装配契约；`None` / 未启用 → `""`。
        stop_loss_pct: 单仓交易所侧 OCO 止损%（`loss_release` 阈的派生源；`None` = 走服务端默认）。
        take_profit_pct: 单仓 OCO 止盈%（跨日模式的兜底披露用）。
        leverage: 本会话杠杆（保证金收益率口径的换算因子）。
        backstop_armed: 本会话是否具备**向交易所核实**兜底的通道（turnover-budget §2.3.3）。
            跨日那句「即使本进程退出它们依然生效，故你不必每轮盯盘」是模型放松盯盘的直接授权
            来源，它必须与证据强度一致：没有核实通道就不能说这句话。
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
        f"{_exchange_line(rule, stop_loss_pct, take_profit_pct, backstop_armed)}"
        "**单根分钟级回撤不构成平仓理由**：执行周期与更高周期的持仓逻辑未失效之前，战术周期的反向"
        "信号只应影响你的离场**价位**，不应影响你的离场**决定**。需要离场时，请先说明「持仓理由已经"
        "失效」的新证据，再给出 `close`。"
        f"{_exit_priority_charter(rule)}"
    )


def _exit_priority_charter(rule: HorizonRule) -> str:
    """宪章的「离场优先通道」段（§4.6.4）；本层未装配 ⇒ **空串垫片** ⇒ 三份 prompt 逐字节回退。

    这一段是本轮对用户诉求的直接回答：**当你在多轮研判中确认应当退场时，系统有一条最高优先级的
    通道支持你走掉**，而不是被硬条件卡死。但优先级越高、举证责任越重——每一次放行都会留下能回头
    算账的凭据（X-9），否则这条通道会在三天内退化成「每一轮都喊 final」。
    """
    priority = rule.exit_priority
    if priority is None or not getattr(priority, "enabled", False):
        return ""
    return (
        "\n**离场优先通道**：如果你判断持仓理由已经失效、需要立即离场，可在 "
        "`submit_trade_decision` 里带上 `exitConviction`：\n"
        f"  · `{CONVICTION_HIGH}` —— 离场理由已成熟：系统将跳过「离场连续确认」这一条约束"
        "（**不含换手迟滞**：那一条是「浮盈还不够付一次往返成本」，跳过它就是确定性地做一笔净亏"
        "的往返，不能由一句自述解开）。\n"
        f"  · `{CONVICTION_FINAL}` —— 你已在多轮研判中确认持仓理由失效：系统将跳过全部三条约束"
        f"立即执行。使用 `{CONVICTION_FINAL}` 必须同时在 `exitEvidence` 里指名「持仓理由失效」的"
        f"**新证据**（不是重复上一轮的判断），且需要连续 {priority.override_rounds} 轮的离场意图"
        f"与开仓后至少 {_fmt_duration(priority.override_min_hold_s)}。\n"
        f"  · 本会话 `{CONVICTION_FINAL}` 配额 {priority.max_overrides_per_session} 次；"
        "配额用尽后按 `high` 处理，**且不再进入复核裁决**（配额是硬的，不是一句「请复核官通融」"
        "能绕过的）。\n"
        "  · 你的每一次 `final` 都会被记录，并在会话结束时与真实结果对照。**它是给真正的转折点"
        "准备的，不是给每一轮的犹豫准备的。**\n"
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
    lines.extend(_turnover_gate_lines(rule))
    if not lines:
        lines.append("  · 本会话未开启任何硬性换手约束（自定义模式全默认）。\n")
    return "".join(lines)


def _turnover_gate_lines(rule: HorizonRule) -> list[str]:
    """开仓侧换手纪律的两条宪章行（turnover-budget §2.2.7）；空转的闸**整行省略**。

    与上面三条的关键差别必须写进去：那三条约束「平」，这两条约束「开」。不点破这一点，模型会把
    它读成「连平仓也更严了」，然后在最该止损的时候犹豫——那是本层最不该造成的副作用。
    """
    lines: list[str] = []
    if rule.reentry_cooldown_s > 0:
        lines.append(f"  · 平掉一笔持仓后 {_fmt_duration(rule.reentry_cooldown_s)} 内"
                     "不接受你**同向重新开仓**（反向不受限）。\n")
    if rule.turnover_per_day is not None:
        lines.append(f"  · **本会话**每日最多 {rule.turnover_per_day:g} 次完整往返，"
                     f"突发额度 {rule.turnover_capacity:g} 次（会话重启后预算重新计算）；"
                     "超出即暂停开新仓。\n")
    if lines:
        lines.append("  · 以上两条**只约束建立新敞口的那一腿**：平仓 `close`、观望 `hold`、"
                     "撤单 `cancel_order` 与同向加仓永不受限；你若提议反手，"
                     "**平仓那一腿一定会执行**，只有反向的新开仓那一腿会被推迟。\n")
    return lines


def _exchange_line(rule: HorizonRule, stop_loss_pct, take_profit_pct,
                   backstop_armed: bool = False) -> str:
    """跨日模式的交易所侧兜底披露（§2.8）；非跨日 ⇒ 空串（不写与本轮无关的噪音）。

    诚实约束（沿用 `_format_unavailable_fallback` 的 v1 终审 RV-1）：两个 pct 皆为空 ⇒ 交易所侧
    **真的没挂单**，此时绝不能宣称还有一条并不存在的兜底。

    turnover-budget §2.3.3 把同一条纪律推进一层：**pct 非 0 也不等于单还在**。这两个数的真源是
    本地成交回执，而「即使本进程退出它们依然生效，故你不必每轮盯盘」正是模型放松盯盘的直接授权
    来源。没有核实通道（`backstop_armed=False`）时必须改口——让模型据一句未经核实的承诺去放松
    盯盘，是本特性最大的一处安全债。
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
    if not backstop_armed:
        return (f"本会话跨日持有，开仓时已随单挂出交易所侧{' / '.join(parts)}——但这两个数来自"
                "**本地下单回执**，本会话**尚无向交易所核实的通道**，故请不要把「不必盯盘」"
                "当作前提。\n")
    return (f"本会话跨日持有，开仓时已随单挂出交易所侧{' / '.join(parts)}——"
            "即使本进程退出它们依然生效，故你**不必每轮盯盘**，可以按目标周期规划退出。"
            "本会话会定期向交易所核实它们是否仍然挂着，核实结果每轮如实告诉你；"
            "**一旦核实不到，请立即收紧退出计划**。\n")


def format_horizon_for_prompt(wire: dict | None) -> str:
    """决策 prompt 的每轮只读上下文段（§2.9）；键缺失 ⇒ `""` ⇒ 调用方整段省略（逐字节回退）。

    [P0-3] 空仓分支**不再早退**：`flat` 恰是 G4/G5 唯一真正会拦人的状态。原句「本层不约束开仓」
    今天已经是假的——落地后的形态会是「每轮告诉模型开仓不受限 → 模型开仓 → 被降级为 hold →
    模型把被拦当成新证据自我怀疑」，即 `hold-inertia` 那条教训，而且是系统主动说反话。
    两道新闸都未激活时 `_turnover_line` 返回空串，拼接后与本轮之前**逐字节相同**（S-13 守它）。
    """
    if not wire:
        return ""
    head = f"【交易周期 · {wire.get('title')}（{wire.get('targetLabel') or '自定义'}）】"
    if wire.get("status") == STATUS_FLAT:
        return "\n".join(part for part in [f"{head}\n  {_flat_line(wire)}",
                                           _turnover_line(wire)] if part)
    lines = [head, _hold_line(wire), _confirm_line(wire), _hysteresis_line(wire),
             _release_line(wire), _exit_priority_line(wire), _exit_commitment_line(wire),
             _turnover_line(wire), _exchange_snapshot_line(wire),
             _blocked_note_line(wire)]
    return "\n".join(part for part in lines if part)


def _flat_line(wire: dict) -> str:
    """空仓那一句（[P0-3]）：措辞取决于**换手纪律层是否装配**，而不是两道闸此刻是否激活。

    这是本轮对 spec §2.2.7 内部矛盾的取舍（见 §十一 实施发现）：spec 既要求「原句里『不约束开仓』
    四个字必须改掉」，又要求「两闸未激活时拼接后与今天逐字节相同」——两者不可兼得。按**装配**分支
    才同时守住两端：
      · 未装配（总闸关 / custom 全默认 ⇒ `turnover` 整键缺席）⇒ 逐字返回今天这句，G-1 冻结门禁成立；
      · 已装配 ⇒ 绝不再说「不约束开仓」。此时哪怕两道闸此刻恰好都没激活，它们**下一轮就可能拦**，
        按「此刻是否激活」分支等于把一句随时会变成假话的承诺继续说出口。
    """
    if not isinstance(wire.get("turnover"), dict):
        return "当前空仓：本层**不约束开仓**；开仓后将按上述目标周期约束你的主动平仓。"
    return "当前空仓：本层**不约束你的平仓能力**；开仓后将按上述目标周期约束主动平仓。"


def _turnover_line(wire: dict) -> str:
    """换手纪律一行（turnover-budget §2.2.7）；两个闸都未激活 ⇒ **空串** ⇒ 整行省略。

    沿用 `format_profit_cooldown` 的既有教训：只说「被拦」而不说「还剩多久 / 什么不受限」，
    会让模型每轮重复提议、每轮被拦、每轮把被拦当成新证据——既烧 LLM 又污染上下文。故这一行
    必须同时给出**剩余时间**、**豁免范围**与**反手的处置**。
    """
    turnover = wire.get("turnover")
    if not isinstance(turnover, dict):
        return ""
    parts: list[str] = []
    if turnover.get("cooldownActive"):
        side = _SIDE_LABEL.get(str(turnover.get("cooldownSide") or ""), "同向")
        scope = ("**开仓（做多与做空）都会被拦截**" if turnover.get("cooldownScope") == "both"
                 else f"**{side}开仓会被拦截并降级为观望**（反向不受影响）")
        parts.append(f"刚平掉一笔{side}持仓，接下来 "
                     f"{_fmt_duration(turnover.get('cooldownRemainingSeconds'))}内{scope}")
    if turnover.get("budgetEnabled") and float(turnover.get("tokens") or 0.0) < 1.0:
        nxt = turnover.get("nextTokenSeconds")
        eta = "" if nxt is None else f"，下一枚约 {_fmt_duration(nxt)}后到账"
        parts.append(f"**本会话**换手预算余 {float(turnover.get('tokens') or 0.0):.1f}"
                     f" / {float(turnover.get('capacity') or 0.0):g} 枚"
                     f"（上限 {turnover.get('perDay')} 次/日，会话重启后重新计算）{eta}")
    if not parts:
        return ""
    return ("  【交易周期 · 换手纪律】" + "；".join(parts) + "。\n"
            "  **平仓 `close`、观望 `hold`、撤单 `cancel_order` 不受任何影响；"
            "若你此刻持仓并提议反手，平仓那一腿一定会执行，只有反向的新开仓那一腿会被推迟。**\n"
            "  这两条限制的目的与你的目标持仓周期一致：一次往返的手续费是确定的，而一次立刻的"
            "重开需要一个比上一次更强的理由。若你认为现在确实存在这样的理由，请把它写进 "
            "`reason`——它会被记录下来，用于事后核对预算闸是否设得过紧。")


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
    # 轴 A：一次犹豫不再抹掉整条论证链，故必须让模型看得见「你上一轮没提，本轮按衰减计入」——
    # 否则它会以为账本已经清零而重新从头论证一遍。`lapses == 0` ⇒ 逐字节回退今天那句。
    lapses = int(wire.get("exitIntentLapses") or 0)
    decay = "" if lapses <= 0 else f"（其间 {lapses} 轮未提离场，已按衰减计入）"
    if wire.get("confirmWindowElapsed"):
        return (f"  离场连续确认：{streak}/{rounds}{decay}，"
                "**确认窗口已到，本轮按你的意愿放行**。")
    remaining = wire.get("confirmWindowRemainingSeconds")
    window = ("" if remaining is None
              else f"；确认窗口剩余 {_fmt_duration(remaining)}（到时即放行）")
    return f"  离场连续确认：{streak}/{rounds}{decay}{window}。"


def _exit_priority_line(wire: dict) -> str:
    """离场优先级一行（§4.6.4）；`exitPriority` 键缺席 / 本轮非平仓 ⇒ 整行省略（逐字节回退）。

    降档轮**必须写清原因**：「你声明了 final，但因为 X 本轮按 high 处理」是模型下一轮修正行为的
    唯一依据；静默降档会让它在下一轮重复完全相同的错误。
    """
    priority = wire.get("exitPriority")
    if not isinstance(priority, dict) or priority.get("level") in (None, LEVEL_NONE):
        return ""
    level = str(priority.get("level"))
    gates = "、".join(priority.get("releasedGates") or ()) or "—"
    head = f"  离场优先级：{_LEVEL_LABEL.get(level, level)}"
    body = "（本轮未解除任何约束）" if gates == "—" else f"（已解除 {gates}）"
    downgrade = priority.get("downgradeReason")
    tail = ("" if not downgrade else
            f"；你声明了 {priority.get('declared')}，但{_DOWNGRADE_LABEL.get(downgrade, downgrade)}"
            f"，本轮按 {CONVICTION_HIGH} 处理")
    used, cap = priority.get("overridesUsed"), priority.get("maxOverridesPerSession")
    quota = "" if used is None or cap is None else f"｜最高优先级配额已用 {used}/{cap} 次"
    pending = "；**本轮已挂起，交由复核官裁决**" if priority.get("pending") else ""
    return f"{head}{body}{tail}{quota}{pending}。"


def _exit_commitment_line(wire: dict) -> str:
    """离场兑现一行（§4.6.4）；未武装 ⇒ 整键缺席 ⇒ 整行省略。

    **受阻那一支至关重要**：它是模型唯一能知道「我已经下过平仓指令但它没成交」的途径，缺了它
    模型会在下一轮重新研判一遍已经作过的决定。
    """
    commit = wire.get("exitCommitment")
    if not isinstance(commit, dict) or not commit.get("armed"):
        return ""
    attempts, cap = commit.get("attempts"), commit.get("maxAttempts")
    age = _fmt_duration(commit.get("ageSeconds") or 0.0)
    if commit.get("stuck"):
        return (f"  ⚠ **你此前已确认离场，但连续 {attempts} 次平仓都未成交**（已持续 {age}），"
                "系统已停止自动重试；交易所侧止盈止损仍生效，用户可在面板上一键离场。"
                "请据此重新研判，不要把它当成一次尚未做出的决定。")
    return (f"  离场意图正在兑现：第 {attempts}/{cap} 次尝试（已持续 {age}），"
            "系统会在下一次盯市继续重试——**你无需重复下达同一条平仓指令**。")


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
    """交易所侧兜底一行（§2.8 第 3 条 + turnover-budget §2.3.3 的五分支 + 本轮的自愈子句）。

    `backstop` 键缺席（总闸关 / 回测 / 未装配）⇒ **逐字返回本轮之前的那两句**（冻结用例守，
    保证「不开轴 B 的会话 prompt 一个字节都不变」）。键在场时按 `basis` 分五支，核心纪律：
    **核实到才敢说「进程退出后仍生效」**；`coverageRatio is None` 是「还不知道」，绝不渲染成
    「确认裸露」那句确定的坏消息（[P0-2] / D-14）。

    `backstop.repair` 子键在场时**至多追加一句**（backstop-repair §2.2.7）——兜底行已经是 prompt
    里最长的一段；子键缺席（自愈层未装配 / `idle`）⇒ 五支逐字节回退。
    """
    return _backstop_basis_line(wire) + _repair_clause(wire.get("backstop"))


def _backstop_basis_line(wire: dict) -> str:
    """五分支的兜底基线句（本轮**一个字节都没改**，冻结用例读的就是它）。"""
    sl, tp = wire.get("exchangeStopLossPx"), wire.get("exchangeTakeProfitPx")
    backstop = wire.get("backstop")
    if not isinstance(backstop, dict):
        if sl is None and tp is None:
            return "  交易所侧未挂止盈止损——此刻没有任何持久化兜底。"
        return f"  交易所侧兜底：止盈 {_fmt_px(tp)} / 止损 {_fmt_px(sl)}（进程退出后仍生效）。"
    if backstop.get("basis") != "exchange":
        return _unverified_backstop_line(backstop, sl, tp)
    ago = _fmt_ago(backstop.get("checkedAgoSeconds"))
    if backstop.get("mismatch"):
        return ("  ⚠ **本地记录声称挂过止损，但向交易所核实的结果是未覆盖（0%）**——"
                "此刻没有任何持久化兜底，本进程是唯一的退出执行者。"
                "请据此收紧你的退出计划，不要按「有兜底」规划持有。")
    if not backstop.get("hasStop"):
        return (f"  交易所侧现在**没有**任何止损委托（{ago}前核实）——"
                "退出完全由你与本进程负责。")
    stop_px = _fmt_px(backstop.get("stopLossPx"))
    ratio = backstop.get("coverageRatio")
    if ratio is None:
        return (f"  交易所侧确认存在一张止损委托 {stop_px}（{ago}前核实）——"
                "**但交易所未回报可核对的委托张数，本会话无法确认它是否覆盖了全部持仓**。"
                "请按「至少有一张、但未必覆盖全部」规划退出。")
    if float(ratio) < 0.999:
        return (f"  ⚠ 交易所侧止损只覆盖了当前持仓的 {float(ratio) * 100:.0f}%"
                f"（{ago}前核实）——未覆盖部分在本进程退出后**没有**兜底。")
    return (f"  交易所侧兜底：止损 {stop_px}（已于 {ago}前向交易所核实，覆盖 "
            f"{float(ratio) * 100:.0f}%）——**进程退出后仍生效**。")


def _repair_clause(backstop) -> str:
    """自愈层的加法子句（backstop-repair §2.2.7）；键缺席 / `idle` ⇒ `""` ⇒ 逐字节回退。

    每一句都以「当前**有没有**兜底」结尾，不以「系统做了什么」结尾——模型需要的是事实，
    不是工单状态。`placed` 一律说「已发出，正在核实」而**不说「已补挂」**：OKX 回了一个 algoId
    不等于那张单真的挂在那里（§九 D-6）。
    """
    repair = (backstop or {}).get("repair") if isinstance(backstop, dict) else None
    if not isinstance(repair, dict):
        return ""
    state = str(repair.get("state") or "")
    px, sz = _fmt_px(repair.get("plannedStopPx")), repair.get("plannedSz")
    if state == "planned":
        return (f" 系统已算出补挂方案（止损 {px} / {sz} 张），但本会话为「仅提醒」模式"
                "——在人工挂出前，请按「没有兜底」规划退出。")
    if state in ("placing", "placed"):
        return (f" 系统已发出一张补挂止损（{px} / {sz} 张），正在向交易所核实；"
                "核实通过前不要按「已有兜底」放松退出计划。")
    if state == "verified":
        done = (repair.get("lastResult") or {}).get("sz")
        return f" 其中 {done} 张由系统自动补挂并已核实通过。"
    if state == "failed":
        last = repair.get("lastResult") or {}
        # 读**预渲染**的中文原因（`backstop_repair._note_failure`），不读裸码：归一码的值域由
        # `okx.trading` 拥有，在本文件抄一张表，下一次新增码就会让 prompt 里出现一个原始英文串。
        return (f" ⚠ 系统尝试补挂止损失败（{last.get('codeLabel') or last.get('code')}），"
                "当前仍无持久化兜底。")
    if state == "blocked":
        return _repair_blocked_clause(repair)
    return ""


def _repair_blocked_clause(repair: dict) -> str:
    """`blocked` 的三支措辞：越过 / 过期 / 其余（前两支各有各的行动含义，不得合并）。"""
    reason = str(repair.get("blockedReason") or "")
    if reason == "px_through":
        return (f" ⚠ 现价已越过本地止损价 {_fmt_px(repair.get('plannedStopPx'))}，"
                "系统拒绝补挂一张会立刻触发的止损（那等于立刻市价平仓）——退出决策完全在你手上。")
    if reason == "px_stale":
        return (" 系统本可补挂止损，但当前判定价已过期，无法确认现价是否仍在止损的有利侧，"
                "故暂缓——当前仍无持久化兜底。")
    return (f" 系统本可补挂止损但被暂缓（{_REPAIR_BLOCK_LABEL.get(reason, reason or '未知原因')}），"
            "当前仍无持久化兜底。")


def _unverified_backstop_line(backstop: dict, sl, tp) -> str:
    """`local` / `unavailable` 两态的同一句话：本地口径 + **本会话尚未向交易所核实**。

    两态刻意共用一句：对模型而言「还没查」与「查失败」的**行动含义完全相同**——都不能据此
    假定兜底在进程退出后仍然生效。差别只在括号里的原因，那是给人看的。
    """
    reason = _BACKSTOP_REASON_LABEL.get(str(backstop.get("unavailableReason") or ""),
                                        "尚未核实")
    parts = []
    if sl is not None:
        parts.append(f"止损 {_fmt_px(sl)}")
    if tp is not None:
        parts.append(f"止盈 {_fmt_px(tp)}")
    if not parts:
        return "  交易所侧未挂止盈止损——此刻没有任何持久化兜底。"
    return (f"  交易所侧兜底（**本地回执口径**）：{' / '.join(parts)}——"
            f"**本会话尚未向交易所核实**（{reason}），"
            "故不能据此假定它在进程退出后仍然生效。")


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
        f"{_pending_exit_note(wire)}"
    )


def _pending_exit_note(wire: dict) -> str:
    """挂起裁决那一句（§6.4）；非挂起轮 ⇒ 空串 ⇒ 反思 prompt 逐字节回退。

    括号里的内容不是装饰：挂起现在**只可能**由「账本还不够厚」触发（配额、证据、时间地板三种
    降档根本不会走到反思），复核官必须知道自己被请来替代的到底是哪一项，而不是被含糊地要求
    「你看着办」。
    """
    priority = wire.get("exitPriority")
    if not isinstance(priority, dict) or not priority.get("pending"):
        return ""
    streak = priority.get("intentStreak")
    rounds = priority.get("overrideRounds")
    gap = ("" if streak is None or rounds is None
           else f"（跨轮离场账本 {streak}/{rounds}，还差 {max(0, int(rounds) - int(streak))} 轮）")
    return ("\n**本轮是被持仓周期层挂起的离场提议**"
            f"{gap}：模型已声明这是终局判断并给出了证据，只是账本还不够厚。"
            "你的裁决将**直接决定它是否执行**——`confirm` 即立即平仓，`abort` 即继续持有。"
            "请只就「持仓理由是否确已失效」作答，不要替系统执行它自己的轮数约束。")


def format_backtest_notes(rule: HorizonRule | None, bar_seconds: int, *,
                          turnover=None) -> list[str]:
    """回测结果的分辨率免责（§2.10 / [P2-7]）；未装配 ⇒ `[]`。

    **必须点名具体模式**：`hourly_swing`（5m 执行 + 180s 窗口）在回测里 G1 恒不生效，不写清楚用户
    仍会拿这份回测去为实盘的 G1 背书。

    Args:
        rule: 已装配周期契约。
        bar_seconds: 本次回测的执行周期秒数。
        turnover: 回测侧实际装配的 `TurnoverRule`（`None` = 两道新闸在本次回测里整层空转 ⇒
            三句新免责整体省略，既有回测 notes 逐字节不变）。
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
    notes.extend(_turnover_backtest_notes(turnover, rule, bar_seconds))
    notes.extend(_exit_priority_backtest_notes(rule))
    return notes


def _exit_priority_backtest_notes(rule: HorizonRule) -> list[str]:
    """离场优先级在回测世界的两条免责（exit-priority R-10 / D-4）；未装配 ⇒ `[]`。"""
    if rule.exit_priority is None:
        return []
    notes = ["回测**不装配**离场意图兑现重试（轴 C）：`SimBroker` 每单必成，「离场已下发但未成交」"
             "在回测世界里不存在，故本次回测结论不覆盖这一轴。"]
    if rule.exit_pending_max_s > 0:
        notes.append(
            f"离场意图账本在回测里按**根时间戳**推进（不是墙钟）：挂起上界 "
            f"{_fmt_duration(rule.exit_pending_max_s)} 与最高优先级的绝对地板，"
            "在回测中衡量的是「根时间跨度」而非真实等待时长，与实盘存在系统性差异。")
    return notes


def _turnover_backtest_notes(turnover, rule: HorizonRule,
                             bar_seconds: int) -> list[str]:
    """开仓侧换手纪律与轴 B 的三句回测免责（turnover-budget §2.4.4）；未装配 ⇒ `[]`。"""
    if turnover is None:
        return []
    notes = [
        "回测**不装配**交易所侧兜底核实（回测没有交易所），G6 覆盖闸恒放行；本次回测结论"
        "不覆盖「交易所侧兜底是否真的挂着」这一轴。",
        # [P2-5]：反手被换手闸拦下时降级为平仓（退出腿保留、再入场腿否掉），退出来源分布随之偏移。
        "被换手闸拦下的**反手**会降级为平仓（退出腿保留、再入场腿否掉），故这类退出在退出来源"
        "分布里记为「Agent 主动平仓」而非「Agent 反手」；两轮之间对比该分布时须知道这一点。"]
    cooldown = float(getattr(turnover, "reentry_cooldown_s", 0.0) or 0.0)
    if cooldown <= 0:
        return notes
    grain = ("**回测中恒不生效**" if cooldown < bar_seconds
             else f"{math.ceil(cooldown / bar_seconds):g} 根")
    notes.insert(0, f"回测按根推进，再入场冷却的实际生效粒度是 ceil({cooldown:g}s / 一根)："
                    f"本模式（{rule.profile_id}）冷却 {cooldown:g}s、一根 {bar_seconds}s ⇒ {grain}。")
    return notes
