"""利润保护层的**中文文案渲染**（ai-trade-profit-protection §2.6 / adaptive §六）。

从 `profit_guard.py` 切出的**纯渲染半边**：宪章章、每轮 prompt 段、保护线一行、平仓结果措辞。切分的
唯一理由是 CLAUDE.md 的 800 行红线——本轮 F1/F2 把 `profit_guard.py` 由 553 行推到 870 行，而 F0 抽
`ProfitGuardRuntime`、前端抽 `ProfitGuardFields.tsx` 引的正是同一条红线，同一条红线不能在这里被无声
突破。切法与 F0 同款：**纯搬运、零行为变化**，判定纯核心留在 `profit_guard.py`，本模块只读快照。

不变式（不可回退）：**文案与判定共用同一份真源**——线值、有效阈、约束来源全部读后端下发的快照，
本模块不重算任何一个数（三处各算一份必然漂移，[R-5] 的教训）；平仓结果措辞一律由 `pnl` 的**符号**
派生（§六.1b / P0-1），绝不无条件宣称「锁定利润」。
"""
from __future__ import annotations

from .profit_guard import (
    COMMIT_BASIS_ADOPTED,
    COMMIT_BASIS_ATR_FLOOR,
    COMMIT_BASIS_CLAMPED_BREAKEVEN,
    REASON_COMMITTED_EXIT,
    STATUS_FLAT,
    STATUS_UNAVAILABLE,
    TRAIL_BASIS_ATR_CAP,
    TRAIL_BASIS_ATR_FLOOR,
    TRAIL_BASIS_CLAMPED_BREAKEVEN,
    TRAIL_BASIS_SUPPRESSED_AT_ENTRY,
    ProfitGuardRule,
)


# ---------- 文案渲染（与判定共用同一份真源，杜绝「UI 说已保护、判定不触发」，[R-5]）----------
_UNAVAILABLE_LABEL = {
    "stale_feed": "行情数据陈旧",
    "no_price": "实时价不可用",
    "bad_position": "持仓读数异常",
}


def format_profit_guard_charter(rule: ProfitGuardRule | None, *,
                                horizon_armed: bool = False) -> str:
    """系统 prompt 的利润保护宪章章（§2.6 a）；未装配 → **空串垫片**（渲染逐字节不变、零回归）。

    第二层语义**不可删**（放行条件 5 / P1-5）：保护层可能不可用、不可依赖。缺它模型会据一条它以为
    无条件成立的保证去延长持有，而本层极性是 fail-open。

    `horizon_armed`（holding-horizon §2.9 a / D-1）：装配了交易周期模式时，第一层语义由「被保护层
    强平 = 决策失误 + 主动 close 永远更优」改写为**对称**表述。这不是措辞偏好而是据线上证据的推翻：
    16 次往返中 15 次由 Agent 主动退出、净 −2.4741 USDT，唯一一次保护层退出净 +1.8655 USDT——原文案
    在制度上鼓励抢跑退出。道德风险的防线改由确定性门禁（G1/G2/G3）与保留的第二层语义共同承担。
    **`horizon_armed=False` 分支逐字不变**（G-1 的 sha256 冻结门禁据此成立）。
    """
    if rule is None or not rule.enabled:
        return ""
    hard = ("；本会话另启用**硬止盈**：保证金收益达 "
            f"{rule.hard_tp_return_pct * 100:g}% 即由系统直接止盈"
            if rule.hard_tp_return_pct > 0 else "")
    stance = (
        "它是**兜底底线，也是你的退出执行器**——当你的持仓理由仍然成立时，让保护线去执行退出，"
        "与你主动离场同样正确；**提前离场同样是一种错误**，它用两笔确定的手续费换一个尚未成熟的"
        "判断。只有当你能指出「持仓理由已经失效」时才主动 `close`。"
        if horizon_armed else
        "它是**兜底底线，不是你的止盈策略**——被保护层强制平仓意味着你没有在更好的位置"
        "主动离场，应视为一次决策失误。主动的 `close` 永远优先于等待保护层触发。")
    return (
        "**利润保护层（移动止盈 + 保本 · 由系统强制执行）**：本会话启用了利润保护层。持仓浮盈达到"
        f"保证金收益 {rule.arm_return_pct * 100:g}% 后系统会记录峰值，并在利润自峰值回吐超过 "
        f"{rule.giveback_pct * 100:g}%、或价格跌回保本线时**由系统直接市价平仓**"
        f"{hard}。{stance}\n"
        "**另须知悉：保护层在行情或持仓读数异常时会主动不动作**（宁可不动，也绝不据脏数据平掉一笔"
        "真实持仓），届时每轮输入会明确标注「保护层当前不可用」。因此它**不是一条你可以依赖的保证**，"
        "你的退出计划必须在没有它的前提下也成立。"
        f"{_charter_adaptive(rule)}{_charter_cooldown(rule)}{_charter_commitment(rule)}"
    )


def _charter_adaptive(rule: ProfitGuardRule) -> str:
    """宪章的自适应披露段（F1）；阶梯与上下界全关（v1 默认）⇒ **空串** ⇒ 渲染逐字节回退（G-2）。"""
    tiers = "、".join(f"峰值收益 ≥{threshold * 100:g}% 时收紧到 {giveback * 100:g}%"
                      for threshold, giveback in rule.giveback_tiers)
    tier_text = f"回吐阈值随利润**阶梯收紧**（{tiers}）；" if tiers else ""
    atr_text = ""
    if rule.atr_floor_mult > 0 or rule.atr_cap_mult > 0:
        bounds = " / ".join(part for part in (
            f"下界 {rule.atr_floor_mult:g}×ATR" if rule.atr_floor_mult > 0 else "",
            f"上界 {rule.atr_cap_mult:g}×ATR" if rule.atr_cap_mult > 0 else "") if part)
        atr_text = (f"回吐**距离**再按波动率归一（{bounds}），"
                    "以免在低波动标的上被分钟级噪音扫掉、或在高波动标的上放任过度回吐；")
    if not tier_text and not atr_text:
        return ""
    return ("\n**保护线是自适应的**："
            f"{tier_text}{atr_text}"
            "每轮输入的「利润保护」段会如实给出本轮**有效回吐阈**与**这条线被哪条约束定住**，"
            "请据那个数字而不是宪章里的基准值来规划退出。")


def _charter_commitment(rule: ProfitGuardRule) -> str:
    """宪章的承诺退出线披露段（C1/C3 §2.3.1）；`commit_enabled=False` ⇒ **空串** ⇒ 逐字节回退（G-2）。

    两层诚实语义**不可删**（同 v1 P1-5 道德风险约束）：① 明说「只收紧不放松」，否则模型会以为可随时
    放宽而据一条它以为可变的线延长持有；② [P2-4] 明说它只是进程内兜底、让位于既有 fail-open 条款，
    绝不宣称无条件保证。
    """
    if not rule.commit_enabled:
        return ""
    return (
        "\n**你许下的退出线会被兑现（在保护层可用的前提下）**：当你选择继续持有并给出 `holdExitPx`"
        "（退出价位）时，系统会把它采集为一条保护线，价格触及即**由系统直接市价平仓**。这条线**只能随你"
        "收紧、不能放松**（同一持仓周期内你可以报一条更靠近现价的新线覆盖旧线，但报更松的线不会生效），"
        "且**绝不会劣于保本线**。它不是对你的惩罚，而是替你守住你自己的判断——给了它，你就不必每轮盯盘等"
        "回吐；不给它，则仅由系统的移动止盈线兜底。被你自己的承诺线平出，说明价格确实走到了你说要离场的"
        "位置。**但它和移动止盈线一样只是进程内的兜底**：与上文 fail-open 条款一致，行情或读数异常时保护"
        "层会主动不动作，进程若关闭则这条承诺线随之失效、只剩开仓随单 OCO——所以它**不是**一条你可以无"
        "条件依赖的保证，你的退出计划必须在没有它时也成立。")


def _charter_cooldown(rule: ProfitGuardRule) -> str:
    """宪章的冷却披露段（F2）；`cooldown_s=0` ⇒ **空串** ⇒ 渲染逐字节回退（G-2）。"""
    if rule.cooldown_s <= 0:
        return ""
    scope = ("开仓（含反向）" if rule.cooldown_scope == "both" else "**同向**开仓")
    return ("\n**保护性平仓后有冷却期**：由本层强制平仓后的 "
            f"{rule.cooldown_s:g}s 内，{scope}会被系统降级为观望"
            "（防止刚锁住的利润被一次立刻反手回吐）。"
            "冷却期内**平仓 `close`、撤单 `cancel_order` 与观望不受任何限制**；"
            "冷却是否仍在生效以每轮输入的「冷却期」段为准。")


def _has_exchange_oco(snapshot: dict) -> bool:
    """交易所侧是否真的挂着兜底 OCO（两个触发价至少有一个）。"""
    return (snapshot.get("exchangeTakeProfitPx") is not None
            or snapshot.get("exchangeStopLossPx") is not None)


def _format_exchange_oco(snapshot: dict) -> str:
    """交易所侧兜底 OCO 一行；来自现算而非成交回执时标「估算」（§六.5 诚实约束）。"""
    tp, sl = snapshot.get("exchangeTakeProfitPx"), snapshot.get("exchangeStopLossPx")
    if not _has_exchange_oco(snapshot):
        return "交易所侧兜底：未挂止盈止损"
    tag = "（估算）" if snapshot.get("exchangeOcoSource") == "derived" else ""
    return (f"交易所侧兜底{tag}：止盈 {_fmt_px(tp)} / 止损 {_fmt_px(sl)}")


def _format_unavailable_fallback(snapshot: dict) -> str:
    """保护层不可用时，如实交代兜底责任归属（§六.5 诚实约束）。

    `risk.stop_loss_pct` / `take_profit_pct` 双双为 None 时（运维把 `AI_TRADE_DEFAULT_SL_PCT` /
    `_TP_PCT` 置 0 即可达，`manager._resolve_risk` 的 `sl or None`）交易所侧根本没挂单。此时若照写
    「仅交易所侧止盈止损仍生效」，等于在保护层已经停手的那一刻，告诉模型还有一条**并不存在**的防线
    ——这正是 P1-5 要防的道德风险的最坏形态（既没有保护层，又以为有兜底）。
    """
    if _has_exchange_oco(snapshot):
        return f"仅{_format_exchange_oco(snapshot)} 仍生效。请据此自行管理退出。"
    return "且交易所侧**未挂**止盈止损——此刻没有任何自动兜底，退出完全由你负责。"


def _fmt_px(value: float | None) -> str:
    return "—" if value is None else f"{float(value):.6f}"


def _fmt_pct(value: float | None) -> str:
    return "—" if value is None else f"{float(value) * 100:+.2f}%"


def _fmt_usd(value: float | None) -> str:
    return "—" if value is None else f"{float(value):+.3f} USDT"


def describe_exit_outcome(pnl_usd: float | None) -> str:
    """保护性平仓的结果措辞——**由 `pnl` 的符号派生**（§六.1b / P0-1，全仓库唯一真源）。

    绝不无条件写「锁定利润」：保本线触发、硬止盈滑点不利、或 ATR 下界把移动止盈线推到入场价附近时，
    这笔出场可能恰好打平甚至净亏（本地派生浮盈不含手续费）。在一笔净亏出场上宣称「利润已锁」，与
    v1 终审 RV-1（不得宣称并不存在的兜底）是同一类诚实性缺陷，只是换了个位置。
    """
    if pnl_usd is not None and float(pnl_usd) > 0:
        return f"锁定利润 {_fmt_usd(pnl_usd)}"
    return f"按保护线离场，本笔 {_fmt_usd(pnl_usd)}"


def _format_last_exit(last_exit: dict | None, horizon_armed: bool = False) -> str:
    """上一次保护性平仓回执（§2.6 d）：让模型知道它的仓位为何消失。缺省 → 空串。

    `horizon_armed`（holding-horizon §2.9 b）：装配了交易周期模式时结尾改述为对称口径——原文案
    「你没有在更好的位置主动离场」会把每一次正常的保护线退出都记成一次失分，从而在下一轮把模型推向
    更早的抢跑。`False` 分支逐字不变。
    """
    if not last_exit:
        return ""
    tail = ("保护线已按你设定的持仓周期完成了这次退出。请据此校准你的退出计划，但不必因此提前离场。"
            if horizon_armed else
            "这意味着你没有在更好的位置主动离场——本轮请据此调整你的退出计划。")
    return (f"  ★ 上一笔持仓已由利润保护层强制平仓（{last_exit.get('label')}）："
            f"平仓价 {_fmt_px(last_exit.get('exitPx'))}，"
            f"{describe_exit_outcome(last_exit.get('pnlUsd'))}"
            f"（峰值 {_fmt_usd(last_exit.get('peakPnlUsd'))}）。{tail}")


_TRAIL_BASIS_SUFFIX = {
    TRAIL_BASIS_ATR_FLOOR: "（已按波动率放宽至下界）",
    TRAIL_BASIS_ATR_CAP: "（已按波动率收紧至上界）",
    TRAIL_BASIS_CLAMPED_BREAKEVEN: "（已夹至保本线：本层不做劣于保本的出场）",
}


def _format_trail_line(snapshot: dict) -> str:
    """移动止盈线一行：**必须交代「被谁定住」**（§六.1；五个分支同一函数，前后端不各写一份）。"""
    trail = snapshot.get("trailStopPx")
    if snapshot.get("trailBasis") == TRAIL_BASIS_SUPPRESSED_AT_ENTRY:
        return ("移动止盈线：本轮作废（波动率下界把它推到了入场价之下，且未启用保本线）"
                "——本轮保护层不会据它平仓")
    if trail is None:
        arm = snapshot.get("armReturnPct")
        return f"移动止盈线：尚未达到武装阈值（需保证金收益 ≥ {float(arm or 0) * 100:g}%）"
    giveback_eff = snapshot.get("givebackPctEffective")
    distance = snapshot.get("trailDistancePx")
    detail = "" if giveback_eff is None else f"｜回吐阈 {float(giveback_eff) * 100:g}%"
    span = "" if distance is None else f"｜距峰值 {_fmt_px(distance)}"
    suffix = _TRAIL_BASIS_SUFFIX.get(snapshot.get("trailBasis"), "")
    return f"移动止盈线 {_fmt_px(trail)}（已武装{detail}{span}）{suffix}"


def _format_lines(snapshot: dict) -> str:
    """三条保护线 + 有效退出线（未武装时如实降级，绝不假数据）。"""
    breakeven = snapshot.get("breakevenPx")
    hard = snapshot.get("hardTakeProfitPx")
    breakeven_text = (f"保本线 {_fmt_px(breakeven)}（已武装）" if breakeven is not None
                      else "保本线：尚未武装")
    hard_text = f"硬止盈 {_fmt_px(hard)}" if hard is not None else "硬止盈：未启用"
    return f"  {_format_trail_line(snapshot)}｜{breakeven_text}｜{hard_text}"


# 承诺线 basis 的中文后缀（[P2-5]，独立映射，勿复用 `_TRAIL_BASIS_SUFFIX` 以免语义漂移）；
# `adopted` 须显式含此键（无后缀），否则会走 `.get(default)` 兜底。
_COMMIT_BASIS_SUFFIX = {
    COMMIT_BASIS_ATR_FLOOR: "（已按波动率放宽至下界）",
    COMMIT_BASIS_CLAMPED_BREAKEVEN: "（已夹至保本线）",
    COMMIT_BASIS_ADOPTED: "",
}


def _format_committed_line(snapshot: dict) -> str:
    """承诺退出线一行（§2.3.2）；`committedExitPx` 为空 ⇒ 空串（调用方 join 时整行省略、逐字节回退）。"""
    committed = snapshot.get("committedExitPx")
    if committed is None:
        return ""
    suffix = _COMMIT_BASIS_SUFFIX.get(snapshot.get("committedExitBasis"), "")
    return (f"  你的承诺退出线 {_fmt_px(committed)}{suffix}"
            "——价格触及即由系统平仓（这是你上一轮许下的）")


def format_profit_guard_for_prompt(snapshot: dict | None) -> str:
    """决策 prompt 的每轮只读上下文段（§2.6 b）。

    `payload["profitGuard"]` **键缺失**（规则未装配 / 回测自建 payload 不带）⇒ 调用方整段省略 ⇒
    既有冻结断言与回测 payload 逐字节不变。无持仓 / 未武装 / 不可用一律**如实降级**。
    """
    if not snapshot:
        return ""
    # holding-horizon §2.9 b：`horizonArmed` 由 `HorizonRuntime.inject` 打在同一份保护快照上——
    # 契约未装配的会话该键缺席 ⇒ `False` ⇒ 本段逐字节回退。
    last_exit_line = _format_last_exit(snapshot.get("lastExit"),
                                       bool(snapshot.get("horizonArmed")))
    status = snapshot.get("status")
    if status == STATUS_FLAT:
        return last_exit_line          # 无持仓：只可能有一条平仓回执，没有回执则整段省略
    if status == STATUS_UNAVAILABLE:
        age = snapshot.get("pxAgeSeconds")
        head = ("【利润保护 · 当前不可用】"
                f"{_UNAVAILABLE_LABEL.get(snapshot.get('unavailableReason'), '数据异常')}"
                f"{'' if age is None else f'（判定价已 {age:.0f}s 未更新）'}，"
                "保护层本轮不做任何判定、不会平仓；"
                f"{_format_unavailable_fallback(snapshot)}")
        return "\n".join(part for part in (head, last_exit_line) if part)
    bars = snapshot.get("barsInPosition")
    head_block = (
        "【利润保护 · 移动止盈 / 保本（系统强制执行）】\n"
        f"  持仓 {snapshot.get('holdSeconds'):.0f}s"
        f"{'' if bars is None else f'（{bars} 根）'}"
        f"｜实时价 {_fmt_px(snapshot.get('livePx'))}"
        f"｜当前浮盈 {_fmt_usd(snapshot.get('unrealizedPnlUsd'))}"
        f"（保证金收益 {_fmt_pct(snapshot.get('returnPct'))}）\n"
        f"  峰值浮盈 {_fmt_usd(snapshot.get('peakUnrealizedPnlUsd'))}"
        f"（保证金收益 {_fmt_pct(snapshot.get('peakReturnPct'))}，"
        f"峰值价 {_fmt_px(snapshot.get('peakPx'))}）→ 已回吐 "
        f"{float(snapshot.get('givebackRatio') or 0.0) * 100:.1f}%")
    # 承诺退出线一行仅在非空时进 body（§2.3.2）：为空时 join 跳过 ⇒ 与 v2 逐字节一致（G-1）。
    body = "\n".join(part for part in (
        head_block, _format_lines(snapshot), _format_committed_line(snapshot),
        _format_effective_exit(snapshot), f"  {_format_exchange_oco(snapshot)}") if part)
    return "\n".join(part for part in (body, last_exit_line) if part)


def _format_effective_exit(snapshot: dict) -> str:
    exit_px = snapshot.get("effectiveExitPx")
    if exit_px is None:
        return "  ★ 有效退出线：尚未武装，本轮保护层不会平仓——退出完全由你负责"
    # [P1-3] 归因只读后端 `effectiveExitSource`（唯一真源），不前端比价推断；非承诺线定住时后缀为空
    # ⇒ 逐字节回退 v2（G-1）。
    src = ("（当前由你的承诺线定住）"
           if snapshot.get("effectiveExitSource") == REASON_COMMITTED_EXIT else "")
    return (f"  ★ 有效退出线 {_fmt_px(exit_px)}{src}：价格**向下触及**（做空为向上）即由系统市价平仓，"
            "你无需等待")


def format_profit_hold_note(snapshot: dict | None, rounds) -> str:
    """盈利持仓自察段（§2.6 c / F3）：仅浮盈为正且连续观望达阈值时由调用方注入。

    措辞必须**对称**（沿用 `_format_hold_streak` 踩过的坑）：既打断「盈利也无脑持有」，也不得暗示
    「必须马上平仓」——否则注记本身会变成过度交易的推手。
    """
    if not snapshot or not isinstance(rounds, int) or rounds <= 0:
        return ""
    return_pct = snapshot.get("returnPct")
    if return_pct is None or return_pct <= 0:
        return ""
    # C1 §2.3.4：已有承诺线时把「散文索要退出线」升级为「结构化收紧」引导；无承诺线时逐字节回退 v2。
    committed = snapshot.get("committedExitPx")
    exit_ask = (f"① 你当前的承诺退出线是 {_fmt_px(committed)}，如需收紧请给出更靠近现价的 `holdExitPx`"
                "（系统只会据它向现价收紧、不会放松），"
                if committed is not None
                else "① **你的退出线**（具体价位或条件），")
    return (f"盈利持仓自察：你已连续 {rounds} 轮在浮盈状态下选择继续持有"
            f"（当前保证金收益 {_fmt_pct(return_pct)}，"
            f"自峰值已回吐 {float(snapshot.get('givebackRatio') or 0.0) * 100:.1f}%）。"
            f"若本轮仍选择持有，请在研判中明确给出：{exit_ask}"
            "② 为什么此刻继续承担利润回吐风险优于兑现。这不是必须平仓的指令；"
            "但「浮盈还在」本身不构成继续持有的理由。")
