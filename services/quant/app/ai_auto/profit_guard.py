"""利润保护层纯核心（ai-trade-profit-protection §2.2~§2.7）。

诊断报告（`.bug-diagnosis/27942cda…/report.md`）的结论是**设计缺口而非执行故障**：AI 能开仓也能平仓，
但系统里没有任何一条规则要求它把已经到手的浮盈锁住——DOGE R74 开多 → R78 浮盈 +0.302 → 连续 hold →
R91 翻负成 −0.108 才平。故本模块与【基础策略】铁律层（`base_rules.py`）**完全同构**地加一层：
宪章 prompt（软引导，让 Agent 主动管理退出）+ 本模块的确定性判定（硬保证，跑在 5s 盯市循环上）。

本模块是「算状态」「判触发」「渲文案」三件事的**唯一真源**（三处各写一份必然出现 UI 显示「已保护」
而判定不触发的信任崩塌，`base_rules` 的 [R-5] 教训）；纯函数、无 IO、无 LLM、无 broker。

四个不可动摇的不变式（改本文件前先读）：
1. **fail-open**（§1.2/§2.4）：价格 / 持仓读数不可用一律**不动作**——与基础策略层的 fail-closed 极性
   **相反**且两者都对：一个在管「要不要新增敞口」（无法验证合规 ≠ 合规），另一个在管「要不要动一笔
   已经存在的真实仓位」（绝不据脏数据强平真仓，交易所侧 OCO 仍是最后兜底）。
2. **epoch 身份是 `(side, seq)`**（§2.3.1/P0-1）：只靠 side 变化采样判定，会让「保护平仓 → 同一 tick
   内同向重开」的新仓继承旧峰值而当场被强平。`seq` 由会话侧观察到的 `close` 成交驱动。
3. **判定价只用实时价**（§2.2/P1-2）：诊断根因 ④——15m 未收盘时指标仍是上一根，保护必须在收线之前
   生效。调用方传入的 `mark_px` 恒为 `AiTradeSession._current_px()`，绝不是最近确认根收盘价。
4. **收益率一律是保证金收益率口径**（§2.2）：诊断根因 ②——5× 杠杆下标的 10% 等于保证金 +50%，
   阈值必须说人话。
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from ..config import Settings

# 预置规则 id（本期只有这一条移动止盈 / 保本组合；注册表结构留位但不提前造框架）。
PROFIT_GUARD_RULE_ID = "profit_trailing"

# 三个触发码（`Signal.reason` / `trade.reason` / `explain` / 前端 `inferKindFromReason` 共用同一组常量）。
REASON_TRAILING = "profit_guard_trailing"
REASON_BREAKEVEN = "profit_guard_breakeven"
REASON_HARD_TAKE_PROFIT = "profit_guard_hard_take_profit"

_REASON_LABEL = {
    REASON_TRAILING: "移动止盈",
    REASON_BREAKEVEN: "保本线",
    REASON_HARD_TAKE_PROFIT: "硬止盈",
}

# 快照状态机（§4.2）：flat=无持仓（仅承载 lastExit 回执）；unavailable=本轮零动作（§2.4 第 0/0b 条）。
STATUS_FLAT = "flat"
STATUS_IDLE = "idle"
STATUS_ARMED = "armed"
STATUS_BREAKEVEN_ARMED = "breakeven_armed"
STATUS_UNAVAILABLE = "unavailable"
# 计入 `profitGuardArmedRounds` 的两个状态（§2.7/P2-2，轮级口径）。
ARMED_STATUSES = (STATUS_ARMED, STATUS_BREAKEVEN_ARMED)

_UNAVAILABLE_LABEL = {
    "stale_feed": "行情数据陈旧",
    "no_price": "实时价不可用",
    "bad_position": "持仓读数异常",
}


@dataclass(frozen=True)
class ProfitGuardRule:
    """利润保护的会话级不可变配置（随 `AiTradeSession` 生命周期，§5.1）。

    Attributes:
        enabled: 是否装配本层（`False` 等价于不装配）。
        arm_return_pct: 武装移动止盈的**峰值保证金收益率**阈值。
        giveback_pct: 自峰值允许回吐的比例（0.4 = 让出峰值的四成即离场）。
        breakeven_arm_return_pct: 武装保本线的峰值保证金收益率阈值；`0` = 关闭保本。
        breakeven_offset_return_pct: 保本线相对入场的**正向**偏移（保证金收益率口径，覆盖手续费）。
        min_hold_s: 最短持有期（墙钟秒），抑制开仓瞬间的价差 / 一跳噪音。
        hard_tp_return_pct: 硬止盈的保证金收益率；`0` = 关闭（请求体的 `null` 在解析点归一至此）。
        max_px_age_s: 判定价最大允许年龄（秒）；超龄 ⇒ `unavailable` ⇒ 本轮零动作（fail-open）。
    """

    enabled: bool = True
    arm_return_pct: float = 0.05
    giveback_pct: float = 0.40
    breakeven_arm_return_pct: float = 0.03
    breakeven_offset_return_pct: float = 0.002
    min_hold_s: float = 30.0
    hard_tp_return_pct: float = 0.0
    max_px_age_s: float = 90.0


@dataclass
class ProfitGuardState:
    """一个持仓周期（epoch）的全部持久化状态——只存四个字段，其余全部现算（§2.3）。

    刻意**不存** `peak_return_pct`：`_adopt_partial` / `_adopt_fill` 会在部分成交时改写 `entry_price`
    （均价漂移），存收益率就必须处理「基准变了、峰值要不要 rebase」这个必错的分支；而 `peak_px` 是与
    entry 无关的**市场量**，均价变化后所有派生量自动一致，分支消失。

    Attributes:
        seq: 建仓时的 `session._position_seq` 快照，epoch 身份的一半（§2.3.1/P0-1）。
        side: `long` | `short`，epoch 身份的另一半（捕获反手）。
        opened_wall: 本 epoch 起始墙钟（`time.time()`，单一时钟）。
        peak_px: 方向化最优价（long=max / short=min），初值 = `entry_px`，单调推进。
    """

    seq: int
    side: str
    opened_wall: float
    peak_px: float


@dataclass(frozen=True)
class PositionFacts:
    """一次评估所需的持仓 / 行情断面（聚合结构体，避免位置堆参；全部由会话侧读取真源填入）。

    Attributes:
        seq: 会话级单调持仓序号（`_dispatch` 观察到 `close` 成交即 +1）。
        side: 自管持仓方向（`long` | `short`；`flat` 不应调用本模块）。
        entry_px: 自管持仓均价（`executor.entry_price`）。
        leverage: 会话杠杆（保证金收益率口径的换算基准）。
        qty_coin: 持仓币量 = `entry_qty_sz × ct_val`（本地派生浮盈用）。
        mark_px: **判定价**，恒取 `AiTradeSession._current_px()`（§2.2 单源硬约束）。
        now_wall: 当前墙钟（`time.time()`）。
        bars_in_position: 持仓根数（**仅展示**，绝不参与判定；算不出为 None）。
        exchange_tp_px / exchange_sl_px: 交易所侧兜底 OCO 触发价。
        exchange_oco_source: `trade`=取自开仓成交回执（首选真源）｜`derived`=只读转发现算（P1-4）。
        px_age_s: 判定价年龄（秒，`now_wall − 最近一次实时价推送墙钟`）；仅供如实展示与新鲜度豁免。
    """

    seq: int
    side: str
    entry_px: float
    leverage: float
    qty_coin: float
    mark_px: float
    now_wall: float
    bars_in_position: int | None = None
    exchange_tp_px: float | None = None
    exchange_sl_px: float | None = None
    exchange_oco_source: str | None = None
    px_age_s: float | None = None


@dataclass(frozen=True)
class ProtectVerdict:
    """一次保护触发的裁决（`reason_code` 落 `Signal.reason` / `trade.reason`）。"""

    reason_code: str
    label: str          # 中文短标签（status 事件 / explain headline 共用）
    note: dict          # 落 `lastExit` 与下一轮 prompt 的结构化注记（会话侧补 ts）


def _fallback(value, default):
    """入参缺省（None）时回落 settings 默认；显式值（含 0/False）原样保留。"""
    return default if value is None else value


def resolve_profit_guard_rule(raw: dict | None, settings: Settings) -> ProfitGuardRule | None:
    """把请求里的 `profitGuard` 子对象解析为规则（**默认开启的唯一解析点**，§2.8）。

    三层语义：运维总闸关 ⇒ 恒 `None`（逐字回退到本特性合入前）；字段缺省 ⇒ 按 settings 装配开启态
    （老客户端 / 桌面旧包不发该字段也默认开启）；显式 `enabled=false` ⇒ `None`（该会话不装配）。

    Args:
        raw: 请求中的 `profitGuard`（缺省为 None）；越界入参已在 schema 层 422。
        settings: 运行配置（提供总闸与八个默认值）。

    Returns:
        装配后的 `ProfitGuardRule`；总闸关闭或用户显式关闭时为 `None`。
    """
    if not settings.ai_trade_profit_guard_enabled:
        return None
    raw = raw or {}
    if raw.get("enabled") is False:
        return None
    return ProfitGuardRule(
        enabled=True,
        arm_return_pct=float(_fallback(raw.get("armReturnPct"),
                                       settings.ai_trade_profit_guard_arm_return_pct)),
        giveback_pct=float(_fallback(raw.get("givebackPct"),
                                     settings.ai_trade_profit_guard_giveback_pct)),
        breakeven_arm_return_pct=float(_fallback(
            raw.get("breakevenArmReturnPct"),
            settings.ai_trade_profit_guard_breakeven_arm_return_pct)),
        breakeven_offset_return_pct=float(_fallback(
            raw.get("breakevenOffsetReturnPct"),
            settings.ai_trade_profit_guard_breakeven_offset_return_pct)),
        min_hold_s=float(_fallback(raw.get("minHoldSeconds"),
                                   settings.ai_trade_profit_guard_min_hold_s)),
        # P2-1：请求体的 `null` 与 `0` 均表「关闭硬止盈」，在此归一为 settings 默认（0.0），此后不可再分辨。
        hard_tp_return_pct=float(_fallback(raw.get("hardTakeProfitReturnPct"),
                                           settings.ai_trade_profit_guard_hard_tp_return_pct)),
        max_px_age_s=float(_fallback(raw.get("maxPriceAgeSeconds"),
                                     settings.ai_trade_profit_guard_max_px_age_s)))


def rule_wire(rule: ProfitGuardRule | None) -> dict | None:
    """规则 → camelCase 回执镜像（`group_status()` / run `config_json`）；未装配 → None。"""
    if rule is None or not rule.enabled:
        return None
    return {"enabled": True, "armReturnPct": rule.arm_return_pct,
            "givebackPct": rule.giveback_pct,
            "breakevenArmReturnPct": rule.breakeven_arm_return_pct,
            "breakevenOffsetReturnPct": rule.breakeven_offset_return_pct,
            "minHoldSeconds": rule.min_hold_s,
            "hardTakeProfitReturnPct": rule.hard_tp_return_pct,
            "maxPriceAgeSeconds": rule.max_px_age_s}


def advance_state(state: ProfitGuardState | None,
                  facts: PositionFacts) -> ProfitGuardState:
    """推进 epoch：身份变化 ⇒ 新建（peak 从 entry 起算）；身份不变 ⇒ 峰值价方向化单调推进。

    三条判据缺一不可（§2.3.1）：`state is None` 捕获首仓，`side` 变化捕获反手，**`seq` 变化捕获
    「平了再同向开」**——后者是 P0-1 那条会把刚建的真实仓位当场打掉的可达路径。
    """
    if state is None or state.side != facts.side or state.seq != facts.seq:
        state = ProfitGuardState(seq=facts.seq, side=facts.side,
                                 opened_wall=facts.now_wall, peak_px=facts.entry_px)
    if math.isfinite(facts.mark_px) and facts.mark_px > 0:
        state.peak_px = (max(state.peak_px, facts.mark_px) if facts.side == "long"
                         else min(state.peak_px, facts.mark_px))
    return state


def unavailable_reason_for(facts: PositionFacts, rule: ProfitGuardRule | None, *,
                           px_age_s: float | None, stale_feed: bool) -> str | None:
    """fail-open 前置校验（§2.4 第 0 / 0b 条）：返回不可用原因码，可用则 None。

    `px_age_s` 由会话侧算（`now_wall − 最近一次实时价推送墙钟`）——纯函数拿不到会话时钟。
    """
    if not math.isfinite(facts.mark_px) or facts.mark_px <= 0:
        return "no_price"
    if (facts.side not in ("long", "short") or facts.entry_px <= 0
            or facts.leverage <= 0 or facts.qty_coin <= 0):
        return "bad_position"
    if stale_feed:
        return "stale_feed"
    if (rule is not None and px_age_s is not None and math.isfinite(px_age_s)
            and px_age_s > rule.max_px_age_s):
        return "stale_feed"
    return None


def _signed(side: str, value: float) -> float:
    """方向化：long 原样、short 取反（多空全对称的唯一落点）。"""
    return value if side == "long" else -value


def _derive(rule: ProfitGuardRule, state: ProfitGuardState, facts: PositionFacts) -> dict:
    """由 `(entry, leverage, qty_coin, mark, peak)` 现算全部派生量（§2.3 公式表）。"""
    entry, lev = facts.entry_px, facts.leverage
    move_pct = _signed(facts.side, (facts.mark_px - entry) / entry)
    peak_move_pct = _signed(facts.side, (state.peak_px - entry) / entry)
    return_pct = move_pct * lev
    peak_return_pct = peak_move_pct * lev
    giveback = ((peak_return_pct - return_pct) / peak_return_pct
                if peak_return_pct > 0 else 0.0)
    armed = peak_return_pct >= rule.arm_return_pct
    breakeven_armed = (rule.breakeven_arm_return_pct > 0
                       and peak_return_pct >= rule.breakeven_arm_return_pct)
    trail_px = (entry + _signed(facts.side, abs(state.peak_px - entry) * (1 - rule.giveback_pct))
                if armed else None)
    breakeven_px = (entry * (1 + _signed(facts.side, rule.breakeven_offset_return_pct / lev))
                    if breakeven_armed else None)
    hard_tp_px = (entry * (1 + _signed(facts.side, rule.hard_tp_return_pct / lev))
                  if rule.hard_tp_return_pct > 0 else None)
    return {"movePct": move_pct, "returnPct": return_pct, "peakMovePct": peak_move_pct,
            "peakReturnPct": peak_return_pct, "givebackRatio": giveback,
            "unrealizedPnlUsd": facts.qty_coin * _signed(facts.side, facts.mark_px - entry),
            "peakUnrealizedPnlUsd": facts.qty_coin * _signed(facts.side, state.peak_px - entry),
            "armed": armed, "breakevenArmed": breakeven_armed,
            "trailStopPx": trail_px, "breakevenPx": breakeven_px, "hardTakeProfitPx": hard_tp_px}


def _effective_exit_px(side: str, trail_px: float | None,
                       breakeven_px: float | None) -> float | None:
    """有效退出线 = 两条**下方**保护线中更靠近现价的一条（long 取 max，short 取 min）。

    硬止盈**刻意不参与**：它是上方目标位（向上触及即止盈），与「向下触及即平仓」的退出线语义相反，
    混进 max/min 会得到一个既非目标也非保护的数（P2-5）。
    """
    lines = [px for px in (trail_px, breakeven_px) if px is not None]
    if not lines:
        return None
    return max(lines) if side == "long" else min(lines)


def _round_px(value: float | None) -> float | None:
    return None if value is None else round(float(value), 8)


def _round_pct(value: float | None) -> float | None:
    return None if value is None else round(float(value), 6)


def _round_usd(value: float | None) -> float | None:
    return None if value is None else round(float(value), 4)


def build_idle_snapshot(rule: ProfitGuardRule | None) -> dict:
    """无持仓时的空壳快照：只承载 `lastExit` 回执（§2.6 d）。渲染层据 `status` 只出那一行。"""
    return {"ruleId": PROFIT_GUARD_RULE_ID, "status": STATUS_FLAT, "unavailableReason": None,
            "side": None, "armReturnPct": None if rule is None else rule.arm_return_pct,
            "givebackPct": None if rule is None else rule.giveback_pct,
            "minHoldSeconds": None if rule is None else rule.min_hold_s,
            "lastExit": None}


def build_snapshot(rule: ProfitGuardRule, state: ProfitGuardState, facts: PositionFacts, *,
                   unavailable_reason: str | None = None) -> dict:
    """组装 §4.2 的 camelCase wire 快照（纯函数产 wire dict，镜像 `base_rules.build_ma_band`）。

    `unavailable_reason` 非空 ⇒ `status="unavailable"` 且**三条退出线与有效退出线一律 None**——
    绝不显示一条算得出来却不作数的价位（§2.6 b）。交易所侧 OCO 价保留：那是此时唯一还生效的兜底。
    """
    if unavailable_reason is not None:
        d = {"movePct": None, "returnPct": None, "peakReturnPct": None, "givebackRatio": None,
             "unrealizedPnlUsd": None, "peakUnrealizedPnlUsd": None,
             "trailStopPx": None, "breakevenPx": None, "hardTakeProfitPx": None}
        status = STATUS_UNAVAILABLE
        exit_px = None
    else:
        d = _derive(rule, state, facts)
        status = (STATUS_ARMED if d["armed"]
                  else STATUS_BREAKEVEN_ARMED if d["breakevenArmed"] else STATUS_IDLE)
        exit_px = _effective_exit_px(facts.side, d["trailStopPx"], d["breakevenPx"])
    return {
        "ruleId": PROFIT_GUARD_RULE_ID, "status": status,
        "unavailableReason": unavailable_reason,
        "side": facts.side, "entryPx": _round_px(facts.entry_px),
        "livePx": _round_px(facts.mark_px), "peakPx": _round_px(state.peak_px),
        "holdSeconds": round(max(0.0, facts.now_wall - state.opened_wall), 1),
        "barsInPosition": facts.bars_in_position,
        "priceMovePct": _round_pct(d["movePct"]), "returnPct": _round_pct(d["returnPct"]),
        "peakReturnPct": _round_pct(d["peakReturnPct"]),
        "givebackRatio": _round_pct(d["givebackRatio"]),
        "unrealizedPnlUsd": _round_usd(d["unrealizedPnlUsd"]),
        "peakUnrealizedPnlUsd": _round_usd(d["peakUnrealizedPnlUsd"]),
        "trailStopPx": _round_px(d["trailStopPx"]), "breakevenPx": _round_px(d["breakevenPx"]),
        "hardTakeProfitPx": _round_px(d["hardTakeProfitPx"]), "effectiveExitPx": _round_px(exit_px),
        "exchangeTakeProfitPx": _round_px(facts.exchange_tp_px),
        "exchangeStopLossPx": _round_px(facts.exchange_sl_px),
        "exchangeOcoSource": facts.exchange_oco_source,
        "armReturnPct": rule.arm_return_pct, "givebackPct": rule.giveback_pct,
        "minHoldSeconds": rule.min_hold_s,
        "pxAgeSeconds": (None if facts.px_age_s is None else round(float(facts.px_age_s), 1)),
        "lastExit": None,
    }


def _touched(side: str, px: float, line: float | None) -> bool:
    """价格**向下**（short 为向上）触及某条保护线（含等于）。线缺失 ⇒ 未触及。"""
    if line is None:
        return False
    return px <= line if side == "long" else px >= line


def _verdict(reason_code: str, snapshot: dict, exit_px: float | None) -> ProtectVerdict:
    label = _REASON_LABEL[reason_code]
    return ProtectVerdict(
        reason_code=reason_code, label=label,
        note={"reasonCode": reason_code, "label": label,
              "side": snapshot.get("side"), "entryPx": snapshot.get("entryPx"),
              "exitPx": _round_px(exit_px), "peakPx": snapshot.get("peakPx"),
              "returnPct": snapshot.get("returnPct"),
              "peakReturnPct": snapshot.get("peakReturnPct"),
              "givebackRatio": snapshot.get("givebackRatio"),
              "pnlUsd": snapshot.get("unrealizedPnlUsd"),
              "peakPnlUsd": snapshot.get("peakUnrealizedPnlUsd")})


def evaluate_profit_guard(snapshot: dict | None,
                          rule: ProfitGuardRule | None) -> ProtectVerdict | None:
    """三条规则按固定优先级短路判定（§2.4）；**任一分支不成立一律 `None`（不动作）**。

    优先级：硬止盈 → 移动止盈 → 保本。硬止盈在最前是因为它是「已达目标位」而非「已在回吐」，
    两者同时成立时按目标位记因更贴事实。

    Args:
        snapshot: `build_snapshot` 产物；`None` / `status=="unavailable"` ⇒ 直接不动作（fail-open）。
        rule: 已装配规则；`None` / `enabled=False` ⇒ 恒不动作。

    Returns:
        命中的 `ProtectVerdict`；未命中或前置豁免 → `None`。
    """
    if rule is None or not rule.enabled or not snapshot:
        return None
    if snapshot.get("status") in (STATUS_UNAVAILABLE, STATUS_FLAT):
        return None
    side, px = snapshot.get("side"), snapshot.get("livePx")
    if side not in ("long", "short") or px is None:
        return None
    if float(snapshot.get("holdSeconds") or 0.0) < rule.min_hold_s:
        return None                              # 最短持有期：抑制开仓瞬间的价差 / 一跳噪音
    hard_tp_px = snapshot.get("hardTakeProfitPx")
    return_pct = snapshot.get("returnPct")
    if (rule.hard_tp_return_pct > 0 and return_pct is not None
            and return_pct >= rule.hard_tp_return_pct):
        return _verdict(REASON_HARD_TAKE_PROFIT, snapshot, hard_tp_px)
    if _touched(side, px, snapshot.get("trailStopPx")):
        return _verdict(REASON_TRAILING, snapshot, snapshot.get("trailStopPx"))
    if _touched(side, px, snapshot.get("breakevenPx")):
        return _verdict(REASON_BREAKEVEN, snapshot, snapshot.get("breakevenPx"))
    return None


# ---------- 文案渲染（与判定共用同一份真源，杜绝「UI 说已保护、判定不触发」，[R-5]）----------
def format_profit_guard_charter(rule: ProfitGuardRule | None) -> str:
    """系统 prompt 的利润保护宪章章（§2.6 a）；未装配 → **空串垫片**（渲染逐字节不变、零回归）。

    两层语义**不可删**（放行条件 5 / P1-5）：① 被保护层强平 = 一次决策失误；② 保护层可能不可用、
    不可依赖。缺第一层会诱发「反正有兜底」的道德风险（R-2），缺第二层更糟——模型会据一条它以为
    无条件成立的保证去延长持有，而本层极性是 fail-open。
    """
    if rule is None or not rule.enabled:
        return ""
    hard = ("；本会话另启用**硬止盈**：保证金收益达 "
            f"{rule.hard_tp_return_pct * 100:g}% 即由系统直接止盈"
            if rule.hard_tp_return_pct > 0 else "")
    return (
        "**利润保护层（移动止盈 + 保本 · 由系统强制执行）**：本会话启用了利润保护层。持仓浮盈达到"
        f"保证金收益 {rule.arm_return_pct * 100:g}% 后系统会记录峰值，并在利润自峰值回吐超过 "
        f"{rule.giveback_pct * 100:g}%、或价格跌回保本线时**由系统直接市价平仓**"
        f"{hard}。它是**兜底底线，不是你的止盈策略**——被保护层强制平仓意味着你没有在更好的位置"
        "主动离场，应视为一次决策失误。主动的 `close` 永远优先于等待保护层触发。\n"
        "**另须知悉：保护层在行情或持仓读数异常时会主动不动作**（宁可不动，也绝不据脏数据平掉一笔"
        "真实持仓），届时每轮输入会明确标注「保护层当前不可用」。因此它**不是一条你可以依赖的保证**，"
        "你的退出计划必须在没有它的前提下也成立。"
    )


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


def _format_last_exit(last_exit: dict | None) -> str:
    """上一次保护性平仓回执（§2.6 d）：让模型知道它的仓位为何消失。缺省 → 空串。"""
    if not last_exit:
        return ""
    return (f"  ★ 上一笔持仓已由利润保护层强制平仓（{last_exit.get('label')}）："
            f"平仓价 {_fmt_px(last_exit.get('exitPx'))}，"
            f"本笔浮盈 {_fmt_usd(last_exit.get('pnlUsd'))}"
            f"（峰值 {_fmt_usd(last_exit.get('peakPnlUsd'))}）。"
            "这意味着你没有在更好的位置主动离场——本轮请据此调整你的退出计划。")


def _format_lines(snapshot: dict) -> str:
    """三条保护线 + 有效退出线（未武装时如实降级，绝不假数据）。"""
    trail = snapshot.get("trailStopPx")
    breakeven = snapshot.get("breakevenPx")
    hard = snapshot.get("hardTakeProfitPx")
    arm = snapshot.get("armReturnPct")
    trail_text = (f"移动止盈线 {_fmt_px(trail)}（已武装）" if trail is not None
                  else f"移动止盈线：尚未达到武装阈值（需保证金收益 ≥ {float(arm or 0) * 100:g}%）")
    breakeven_text = (f"保本线 {_fmt_px(breakeven)}（已武装）" if breakeven is not None
                      else "保本线：尚未武装")
    hard_text = f"硬止盈 {_fmt_px(hard)}" if hard is not None else "硬止盈：未启用"
    return f"  {trail_text}｜{breakeven_text}｜{hard_text}"


def format_profit_guard_for_prompt(snapshot: dict | None) -> str:
    """决策 prompt 的每轮只读上下文段（§2.6 b）。

    `payload["profitGuard"]` **键缺失**（规则未装配 / 回测自建 payload 不带）⇒ 调用方整段省略 ⇒
    既有冻结断言与回测 payload 逐字节不变。无持仓 / 未武装 / 不可用一律**如实降级**。
    """
    if not snapshot:
        return ""
    last_exit_line = _format_last_exit(snapshot.get("lastExit"))
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
    body = (
        "【利润保护 · 移动止盈 / 保本（系统强制执行）】\n"
        f"  持仓 {snapshot.get('holdSeconds'):.0f}s"
        f"{'' if bars is None else f'（{bars} 根）'}"
        f"｜实时价 {_fmt_px(snapshot.get('livePx'))}"
        f"｜当前浮盈 {_fmt_usd(snapshot.get('unrealizedPnlUsd'))}"
        f"（保证金收益 {_fmt_pct(snapshot.get('returnPct'))}）\n"
        f"  峰值浮盈 {_fmt_usd(snapshot.get('peakUnrealizedPnlUsd'))}"
        f"（保证金收益 {_fmt_pct(snapshot.get('peakReturnPct'))}，"
        f"峰值价 {_fmt_px(snapshot.get('peakPx'))}）→ 已回吐 "
        f"{float(snapshot.get('givebackRatio') or 0.0) * 100:.1f}%\n"
        f"{_format_lines(snapshot)}\n"
        f"{_format_effective_exit(snapshot)}\n"
        f"  {_format_exchange_oco(snapshot)}")
    return "\n".join(part for part in (body, last_exit_line) if part)


def _format_effective_exit(snapshot: dict) -> str:
    exit_px = snapshot.get("effectiveExitPx")
    if exit_px is None:
        return "  ★ 有效退出线：尚未武装，本轮保护层不会平仓——退出完全由你负责"
    return (f"  ★ 有效退出线 {_fmt_px(exit_px)}：价格**向下触及**（做空为向上）即由系统市价平仓，"
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
    return (f"盈利持仓自察：你已连续 {rounds} 轮在浮盈状态下选择继续持有"
            f"（当前保证金收益 {_fmt_pct(return_pct)}，"
            f"自峰值已回吐 {float(snapshot.get('givebackRatio') or 0.0) * 100:.1f}%）。"
            "若本轮仍选择持有，请在研判中明确给出：① **你的退出线**（具体价位或条件），"
            "② 为什么此刻继续承担利润回吐风险优于兑现。这不是必须平仓的指令；"
            "但「浮盈还在」本身不构成继续持有的理由。")
