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
from ..engine.indicators import atr

# 预置规则 id（本期只有这一条移动止盈 / 保本组合；注册表结构留位但不提前造框架）。
PROFIT_GUARD_RULE_ID = "profit_trailing"

# 回吐距离被哪条约束定住（§2.1.1 第 2/3 条）：前三个来自「阶梯 / ATR 上下界」，后两个来自入场价夹紧
# （夹紧是最后一道处理，优先级高于前三个）。全链路如实透出，使「为什么这条线在这里」可被解释（R-11）。
TRAIL_BASIS_GIVEBACK = "giveback"
TRAIL_BASIS_ATR_FLOOR = "atr_floor"
TRAIL_BASIS_ATR_CAP = "atr_cap"
TRAIL_BASIS_CLAMPED_BREAKEVEN = "clamped_breakeven"
TRAIL_BASIS_SUPPRESSED_AT_ENTRY = "suppressed_at_entry"

# 承诺退出线（commitment）被哪条约束定住（§2.2.1）。**独立命名空间**（[P2-5]）：绝不复用 trail 侧字面，
# 免得「夹至保本」在两条线上语义漂移；前端 `COMMIT_BASIS_SUFFIX` 须为这三个字面各配后缀（adopted=空后缀）。
COMMIT_BASIS_ADOPTED = "adopted"
COMMIT_BASIS_ATR_FLOOR = "atr_floor"
COMMIT_BASIS_CLAMPED_BREAKEVEN = "clamped_breakeven"

# 利润阶梯硬约束（§2.1.1 第 1 条）：档数上限 + 比例值域。阈值严格递增、回吐单调不增在
# `normalize_giveback_tiers` 里强制——赚得越多却允许回吐得越多，等于把本轮要修的 bug 写进配置（R-2）。
GIVEBACK_TIERS_MAX = 6

# 三个触发码（`Signal.reason` / `trade.reason` / `explain` / 前端 `inferKindFromReason` 共用同一组常量）。
REASON_TRAILING = "profit_guard_trailing"
REASON_BREAKEVEN = "profit_guard_breakeven"
REASON_HARD_TAKE_PROFIT = "profit_guard_hard_take_profit"
# 第四条触发码（§2.2.3）：模型自己许下、经棘轮兑现的承诺退出线。`inferKindFromReason` 的
# `profit_guard_` 前缀会自动把它归入 profit_guard kind（前端分类零改）。
REASON_COMMITTED_EXIT = "profit_guard_committed_exit"

# 轴 B-4：保本线相对含费成本的最小净额（bps）。与收益锁的 buffer 同量纲、不开放配置。
BREAKEVEN_COST_BUFFER_BPS = 2.0
# 抬高后的保本线须低于峰值至少这么多才武装（与 `profit_lock.MIN_GAP_BPS` 同一不变式）。
BREAKEVEN_MIN_GAP_BPS = 3.0

_REASON_LABEL = {
    REASON_TRAILING: "移动止盈",
    REASON_BREAKEVEN: "保本线",
    REASON_HARD_TAKE_PROFIT: "硬止盈",
    REASON_COMMITTED_EXIT: "承诺退出线",
}

# 快照状态机（§4.2）：flat=无持仓（仅承载 lastExit 回执）；unavailable=本轮零动作（§2.4 第 0/0b 条）。
STATUS_FLAT = "flat"
STATUS_IDLE = "idle"
STATUS_ARMED = "armed"
STATUS_BREAKEVEN_ARMED = "breakeven_armed"
STATUS_UNAVAILABLE = "unavailable"
# 计入 `profitGuardArmedRounds` 的两个状态（§2.7/P2-2，轮级口径）。
ARMED_STATUSES = (STATUS_ARMED, STATUS_BREAKEVEN_ARMED)



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
        giveback_tiers: 利润阶梯 `((峰值收益阈值, 回吐比例), …)`，阈值升序；`()` = 关闭阶梯。
        atr_floor_mult: 回吐**距离**下界 = 该倍数 × ATR；`0` = 关闭下界。
        atr_cap_mult: 回吐**距离**上界 = 该倍数 × ATR；`0` = 关闭上界。
        cooldown_s: 保护性平仓后的冷却秒数；`0` = 关闭冷却。
        cooldown_scope: `same_side`（只拦同向开仓）| `both`。
        commit_enabled: 承诺退出线总闸（本轮 C2/C3）；`False`（末位默认）⇒ 承诺码/宪章段/决策字段
            逐字节回退 v2，任何既有 `ProfitGuardRule(...)` 构造零回归。

    本轮新增字段带 dataclass 默认值 = v1/v2 行为（阶梯空、上下界关、冷却关、承诺关），故任何直接
    `ProfitGuardRule(...)` 的既有构造点与用例判定结果逐字节等价（G-1）。
    """

    enabled: bool = True
    arm_return_pct: float = 0.05
    giveback_pct: float = 0.40
    breakeven_arm_return_pct: float = 0.03
    breakeven_offset_return_pct: float = 0.002
    min_hold_s: float = 30.0
    hard_tp_return_pct: float = 0.0
    max_px_age_s: float = 90.0
    giveback_tiers: tuple[tuple[float, float], ...] = ()
    atr_floor_mult: float = 0.0
    atr_cap_mult: float = 0.0
    cooldown_s: float = 0.0
    cooldown_scope: str = "same_side"
    commit_enabled: bool = False


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
        atr_px: 确认根 ATR(period)（价格单位）；`None` ⇒ 上下界整体不生效（退化为纯阶梯回吐）。
            **只进入回吐距离计算，绝不参与任何触发判定的比较**（G-5）；允许滞后一根（波动尺度天然是
            确认根统计量），与 `mark_px` 的实时性要求刻意不同源。
        committed_exit_px: 已棘轮的承诺退出线（§2.2.3）；`None` ⇒ 本 epoch 尚无承诺，判定退化为 v2 两线。
        committed_exit_basis: 承诺线被哪条约束定住（`COMMIT_BASIS_*`，仅展示）。
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
    atr_px: float | None = None
    # 承诺退出线（§2.2.3，第三条下方保护线）；默认 None ⇒ 既有构造零改、判定零变化（G-1/G-3）。
    committed_exit_px: float | None = None
    committed_exit_basis: str | None = None
    # 轴 B-4：按持仓方向估的往返成本（bps）；非 None ⇒ 保本线偏移不低于「成本 + 2 bps」。
    # 默认 None ⇒ 保本线逐字回退到保证金口径偏移（5× 下只有 4 bps，低于往返成本）。
    cost_floor_bps: float | None = None


@dataclass(frozen=True)
class ProtectVerdict:
    """一次保护触发的裁决（`reason_code` 落 `Signal.reason` / `trade.reason`）。"""

    reason_code: str
    label: str          # 中文短标签（status 事件 / explain headline 共用）
    note: dict          # 落 `lastExit` 与下一轮 prompt 的结构化注记（会话侧补 ts）


def _fallback(value, default):
    """入参缺省（None）时回落 settings 默认；显式值（含 0/False）原样保留。"""
    return default if value is None else value


# ---------- F1：利润阶梯（解析 / 校验 / 选档，env 串与请求体共用同一条通路）----------
def normalize_giveback_tiers(pairs) -> tuple[tuple[float, float], ...]:
    """校验并归一利润阶梯；违反任一硬约束即 `ValueError`（调用点转 422 / 启动失败）。

    硬约束（§2.1.1 第 1 条）：阈值**严格递增**、回吐比例**单调不增**且 ∈ (0,1]、≤ 6 档、首档阈值 > 0。
    单调不增是语义硬约束而非风格偏好——「赚得越多却允许回吐得越多」正是本轮要修的 bug 本身（R-2）。

    Args:
        pairs: 可迭代的 `(峰值收益阈值, 回吐比例)`；空 ⇒ `()`（关闭阶梯，退回固定 `giveback_pct`）。

    Returns:
        升序不可变元组；空输入 → `()`。

    Raises:
        ValueError: 档数越界 / 数值非有限 / 阈值非严格递增 / 回吐比例非单调不增或越界。
    """
    tiers = tuple((float(t), float(g)) for t, g in pairs or ())
    if not tiers:
        return ()
    if len(tiers) > GIVEBACK_TIERS_MAX:
        raise ValueError(f"利润阶梯最多 {GIVEBACK_TIERS_MAX} 档")
    prev_threshold = 0.0
    prev_giveback = None
    for threshold, giveback in tiers:
        if not (math.isfinite(threshold) and math.isfinite(giveback)):
            raise ValueError("利润阶梯含非有限数值")
        if threshold <= prev_threshold:
            raise ValueError("利润阶梯的峰值收益阈值必须严格递增且首档 > 0")
        if not (0 < giveback <= 1):
            raise ValueError("利润阶梯的回吐比例须在 (0, 1] 之间")
        if prev_giveback is not None and giveback > prev_giveback:
            raise ValueError("利润阶梯的回吐比例必须单调不增（赚得越多不可允许回吐更多）")
        prev_threshold, prev_giveback = threshold, giveback
    return tiers


def parse_giveback_tiers(raw) -> tuple[tuple[float, float], ...]:
    """把 env 串 / 请求体列表解析为阶梯元组（**唯一解析点**，两条入口共用同一校验）。

    支持三种形状：`None`/空串 ⇒ `()`；`"0.10:0.35,0.25:0.28"` 逗号分隔的 `阈值:回吐`；
    `[{"peakReturnPct":0.1,"givebackPct":0.35}, …]` 或等价的二元序列（请求体）。

    Raises:
        ValueError: 串格式非法，或归一时违反 `normalize_giveback_tiers` 的任一硬约束。
    """
    if raw is None:
        return ()
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return ()
        pairs = []
        for chunk in text.split(","):
            part = chunk.strip()
            if not part:
                continue
            threshold, _, giveback = part.partition(":")
            if not giveback:
                raise ValueError(f"利润阶梯格式非法（应为 阈值:回吐）：{part}")
            pairs.append((float(threshold), float(giveback)))
        return normalize_giveback_tiers(pairs)
    pairs = []
    for item in raw:
        if isinstance(item, dict):
            pairs.append((item.get("peakReturnPct"), item.get("givebackPct")))
        else:
            pairs.append((item[0], item[1]))
    return normalize_giveback_tiers(pairs)


def tiers_wire(tiers: tuple[tuple[float, float], ...]) -> list[dict]:
    """阶梯 → camelCase 回执镜像（`rule_wire` / run `config_json` 用）。"""
    return [{"peakReturnPct": threshold, "givebackPct": giveback}
            for threshold, giveback in tiers]


def resolve_giveback_pct(rule: ProfitGuardRule, peak_return_pct: float) -> float:
    """选档：取满足 `peak_return ≥ 阈值` 的**最后一档**回吐比例；无匹配 ⇒ 基准 `giveback_pct`。

    阶梯为空（v1 默认）时恒返回 `rule.giveback_pct`，故判定与 v1 逐字节等价（G-1）。
    """
    giveback = rule.giveback_pct
    for threshold, tier_giveback in rule.giveback_tiers:
        if peak_return_pct >= threshold:
            giveback = tier_giveback
    return giveback


# ---------- F1：ATR 读数（实盘与回测共用同一条降级判据，P2-5）----------
def sanitize_atr(value) -> float | None:
    """ATR 读数归一：非有限 / ≤0 / 不可转 ⇒ `None`（⇒ 上下界整体不生效，退化为纯阶梯回吐）。

    `indicators.atr` 用 `ewm(min_periods=period)`，前 `period-1` 行恒为 `NaN`；回测的暖机在「无 HTF +
    关闭体制上下文 + 策略暖机很小」的组合下可以小于 14，故取值后**必须**过这条判据（P2-5）。
    """
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) and out > 0 else None


def safe_atr(df, period: int) -> float | None:
    """确认根 ATR(period)：样本不足 / 非有限 / ≤0 一律 `None`（fail-open，绝不假数据）。

    复用 `engine/indicators.atr`（`market_digest._atr_reading` 已是同一函数的既有消费者），
    不另造第二份公式——两份 ATR 迟早漂移，而这条线要对真实资金动手。
    """
    if df is None or len(df) < period + 1:
        return None
    try:
        return sanitize_atr(atr(df, period).iloc[-1])
    except (KeyError, ValueError, IndexError):
        return None                 # 帧缺列 / 空帧：如实降级，不把脏数带进保护线


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
                                     settings.ai_trade_profit_guard_max_px_age_s)),
        # F1/F2 五个新字段同款「缺省即回落 settings」语义；`givebackTiers: []` 是显式关闭阶梯的退出口
        # （与 `None`=用 settings 串严格区分，故此处仍走 `_fallback` 而非 `or`）。
        giveback_tiers=parse_giveback_tiers(_fallback(
            raw.get("givebackTiers"), settings.ai_trade_profit_guard_giveback_tiers)),
        atr_floor_mult=float(_fallback(raw.get("atrFloorMult"),
                                       settings.ai_trade_profit_guard_atr_floor_mult)),
        atr_cap_mult=float(_fallback(raw.get("atrCapMult"),
                                     settings.ai_trade_profit_guard_atr_cap_mult)),
        cooldown_s=float(_fallback(raw.get("cooldownSeconds"),
                                   settings.ai_trade_profit_guard_cooldown_s)),
        cooldown_scope=str(_fallback(raw.get("cooldownScope"),
                                     settings.ai_trade_profit_guard_cooldown_scope)),
        # 承诺退出线总闸（本轮 C2/C3）：唯一来源是运维 settings（请求体不暴露此键——它是系统能力，
        # 不是每会话可调参数）；关则承诺码/宪章段/决策字段逐字节回退 v2（G-1）。
        commit_enabled=bool(settings.ai_trade_profit_guard_commitment_enabled))


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
            "maxPriceAgeSeconds": rule.max_px_age_s,
            "givebackTiers": tiers_wire(rule.giveback_tiers),
            "atrFloorMult": rule.atr_floor_mult, "atrCapMult": rule.atr_cap_mult,
            "cooldownSeconds": rule.cooldown_s, "cooldownScope": rule.cooldown_scope,
            "commitEnabled": rule.commit_enabled}


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


def _clamp_trail_distance(span: float, giveback_eff: float, rule: ProfitGuardRule,
                          atr_px: float | None) -> tuple[float, str]:
    """回吐**距离**的 ATR 归一（§2.1.1 第 2 条）：低于下界抬到下界、高于上界压到上界。

    下界防「按比例算出的距离小于分钟级噪音 ⇒ 趋势还在时就被扫掉」（与原始 bug 相反、同样吃利润的
    失败模式）；上界防「浮盈很大时放任 20 个点回吐」。`atr_px is None`（样本不足 / 非有限）或倍数为 0
    ⇒ 该侧不生效。返回 `(距离, trailBasis)`。
    """
    distance = span * giveback_eff
    if atr_px is None:
        return distance, TRAIL_BASIS_GIVEBACK
    if rule.atr_floor_mult > 0 and distance < rule.atr_floor_mult * atr_px:
        return rule.atr_floor_mult * atr_px, TRAIL_BASIS_ATR_FLOOR
    if rule.atr_cap_mult > 0 and distance > rule.atr_cap_mult * atr_px:
        return rule.atr_cap_mult * atr_px, TRAIL_BASIS_ATR_CAP
    return distance, TRAIL_BASIS_GIVEBACK


def _clamp_trail_to_floor(trail_px: float, side: str, entry_px: float,
                          breakeven_px: float | None) -> tuple[float | None, str | None, bool]:
    """入场价夹紧（§2.1.1 第 3 条 / P0-1）：**本层绝不做一次注定净亏的强平**。

    ATR 下界会把回吐距离撑大，从而可能把 `trail_px` 推到入场价之下（高波动 + 刚武装）。原始 v1 公式
    恒有 `trail` 严格优于 `entry`，本轮必须显式补回这条保证：

    - **保本线已武装**（默认路径）⇒ 夹到保本线，两线重合、归因如实记为保本线在兜底。
    - **保本线被用户关闭**（`breakevenArmReturnPct=0`，schema 与 config 都允许）⇒ 整条线**作废**：
      `_touched("long", px, entry)` 在 `px ≤ entry` 即为真，毛盈亏恰为 0、净盈亏 = −（开+平手续费）。
      让一个名为「利润保护」的层去做一次必然净亏的强平，既与它存在的理由冲突，也与它的 fail-open
      极性冲突（宁可不动，也绝不据一个不作数的线动一笔真实持仓）。

    Returns:
        `(trail_px, 覆盖后的 basis 或 None, 夹紧是否绑定)`。
    """
    if breakeven_px is not None:
        worse = trail_px < breakeven_px if side == "long" else trail_px > breakeven_px
        if worse:
            return breakeven_px, TRAIL_BASIS_CLAMPED_BREAKEVEN, True
        return trail_px, None, False
    at_or_below_entry = trail_px <= entry_px if side == "long" else trail_px >= entry_px
    if at_or_below_entry:
        return None, TRAIL_BASIS_SUPPRESSED_AT_ENTRY, True
    return trail_px, None, False


def _derive_trail(rule: ProfitGuardRule, state: ProfitGuardState, facts: PositionFacts, *,
                  armed: bool, peak_return_pct: float,
                  breakeven_px: float | None) -> dict:
    """移动止盈线的三段式派生：**阶梯选档 → ATR 归一 → 入场价夹紧**（§2.1.1，顺序不可换）。

    零回归（G-1）：阶梯为空 ⇒ `giveback_eff == rule.giveback_pct`；`atr_*=0` 或 ATR 不可用 ⇒
    basis 恒为 `giveback` ⇒ 走与 v1 **逐字相同**的那一行表达式；此时 `trail` 恒优于保本线与入场价
    （武装需 peak_return ≥ 5%，保本偏移只有 0.2%/lev）⇒ 夹紧与抑制两条分支都不绑定。
    """
    giveback_eff = resolve_giveback_pct(rule, peak_return_pct)
    if not armed:
        return {"trailStopPx": None, "givebackPctEffective": giveback_eff,
                "trailBasis": None, "trailDistancePx": None, "trailClampedAtEntry": False}
    span = abs(state.peak_px - facts.entry_px)
    distance, basis = _clamp_trail_distance(span, giveback_eff, rule, facts.atr_px)
    # basis 未被 ATR 改写时沿用 v1 的**逐字表达式**（而非等价的 `peak − dist`），保证浮点位级等价。
    trail_px = (facts.entry_px + _signed(facts.side, span * (1 - giveback_eff))
                if basis == TRAIL_BASIS_GIVEBACK
                else state.peak_px - _signed(facts.side, distance))
    trail_px, clamp_basis, clamped = _clamp_trail_to_floor(
        trail_px, facts.side, facts.entry_px, breakeven_px)
    return {"trailStopPx": trail_px,
            "givebackPctEffective": giveback_eff,
            "trailBasis": clamp_basis or basis,
            # 抑制态整条线作废 ⇒ 距离也一律 None（绝不显示一个算得出来却不作数的数字，§2.1.3）；
            # 夹到保本线时距离必须按**实际那条线**回算，否则 prompt 的「距峰值 X」与 trailStopPx 对不上
            # （用户与模型据 peak − X 会反算出一条不存在的线，三键自洽正是 R-11 要的可解释性）。
            "trailDistancePx": (None if trail_px is None
                                else abs(state.peak_px - trail_px) if clamped else distance),
            "trailClampedAtEntry": clamped}


def _derive(rule: ProfitGuardRule, state: ProfitGuardState, facts: PositionFacts) -> dict:
    """由 `(entry, leverage, qty_coin, mark, peak, atr)` 现算全部派生量（§2.3 公式表 + §2.1）。"""
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
    offset = rule.breakeven_offset_return_pct / lev
    if facts.cost_floor_bps is not None:
        floor = (facts.cost_floor_bps + BREAKEVEN_COST_BUFFER_BPS) / 1e4
        if floor > offset:
            offset = floor
            # 高杠杆下保证金口径武装点可能低于成本下界：线落在峰值上方 ⇒ 武装即市价平仓。
            breakeven_armed = (breakeven_armed and peak_move_pct
                               >= offset + BREAKEVEN_MIN_GAP_BPS / 1e4)
    breakeven_px = (entry * (1 + _signed(facts.side, offset))
                    if breakeven_armed else None)
    trail = _derive_trail(rule, state, facts, armed=armed, peak_return_pct=peak_return_pct,
                          breakeven_px=breakeven_px)
    hard_tp_px = (entry * (1 + _signed(facts.side, rule.hard_tp_return_pct / lev))
                  if rule.hard_tp_return_pct > 0 else None)
    return {"movePct": move_pct, "returnPct": return_pct, "peakMovePct": peak_move_pct,
            "peakReturnPct": peak_return_pct, "givebackRatio": giveback,
            "unrealizedPnlUsd": facts.qty_coin * _signed(facts.side, facts.mark_px - entry),
            "peakUnrealizedPnlUsd": facts.qty_coin * _signed(facts.side, state.peak_px - entry),
            "armed": armed, "breakevenArmed": breakeven_armed,
            "breakevenPx": breakeven_px, "hardTakeProfitPx": hard_tp_px, **trail}


def _effective_exit(side: str, trail_px: float | None, breakeven_px: float | None,
                    committed_px: float | None) -> tuple[float | None, str | None]:
    """有效退出线 = 三条**下方**保护线中最靠近现价者，返回 `(px, reason_code)`（§2.2.3）。

    硬止盈**刻意不参与**：它是上方目标位（向上触及即止盈），与「向下触及即平仓」的退出线语义相反，
    混进 max/min 会得到一个既非目标也非保护的数（P2-5）。

    候选顺序即同价并列时的**归因优先级**：移动止盈 > 承诺线 > 保本（与既有固定序兼容，见 §2.2.3
    的 G-3 逐字节等价证明）。本函数是「当前由哪条线定住」的**唯一归因真源**（[P1-3]），前端只读不推。
    """
    cands = [(trail_px, REASON_TRAILING), (committed_px, REASON_COMMITTED_EXIT),
             (breakeven_px, REASON_BREAKEVEN)]
    cands = [(px, code) for px, code in cands if px is not None]
    if not cands:
        return None, None
    pick = max if side == "long" else min                 # 最靠近现价的下方线
    # [P2-2] **必须**用 key=按价取极值：Python max/min 在 key 并列时返回**列表首个**达标元素，故
    # tie-break 恰按 cands 的先后=上面的优先级。**严禁**写成 `pick(cands)`（真元组比较）——那会在价
    # 相等时按 reason 字符串字典序 tie-break（breakeven<committed<trailing），空头 min 会错选。
    return pick(cands, key=lambda c: c[0])


def is_commit_armed(status: str | None) -> bool:
    """承诺是否已武装到可采纳（[P1-1] 单一武装谓词，实盘与回测同一份、同阈值）。

    与 `peak_return ≥ breakeven_arm_return_pct`（默认 3%）等价——`ARMED_STATUSES` 即
    `(armed, breakeven_armed)`。未武装（idle / unavailable / flat / None）恒不采纳承诺。
    """
    return status in ARMED_STATUSES


def clamp_commitment(proposed_px, *, side: str, mark_px: float | None,
                     entry_px: float | None, breakeven_px: float | None,
                     atr_px: float | None, atr_floor_mult: float) -> tuple[float | None, str]:
    """把模型的**一次原始退出报价**夹成一条可采纳的保护线，或判定本次不采纳（返回 `(None, reason)`）。

    **纯几何**函数（[P1-1]）：不做武装判定（调用方经 `is_commit_armed` 门控）、不做棘轮
    （`ratchet_commitment`）。判定顺序**不可换**，每步对应一条 v1/v2 已确立的安全不变式（§2.2.1）。

    Args:
        proposed_px: 模型本轮 `holdExitPx` 原始报价。
        side: `long` | `short`。
        mark_px: 判定现价（承诺线相对它论「保护性 / 抗噪距离」）。
        entry_px: 持仓均价（保本线关时的安全底线）。
        breakeven_px: 已武装的保本线（在则为安全底线，夹到它而非入场价）。
        atr_px: 本轮 ATR（价格单位）；`None` / `atr_floor_mult<=0` ⇒ 抗噪下界不生效。
        atr_floor_mult: 抗噪下界倍数（复用 v2 同一参数）。

    Returns:
        `(line, basis)`——采纳时 basis ∈ `COMMIT_BASIS_*`；不采纳时 `(None, reason)`
        （`not_finite` / `not_protective` / `no_floor` / `suppressed_at_floor`）。
    """
    # 0. 输入合法性（武装与否由调用方在此之前门控，clamp 不自带 arm 阈值——[P1-1]）。
    if proposed_px is None or not math.isfinite(proposed_px) or proposed_px <= 0:
        return None, "not_finite"
    line = float(proposed_px)
    # 1. 方向必须是保护性的：多头须在现价下方（止损保护），空头须在上方；否则给的是目标位而非保护位。
    protective = line < mark_px if side == "long" else line > mark_px
    if not protective:
        return None, "not_protective"
    # 2. 抗噪下界（复用 v2 ATR 尺度）：离现价太近的承诺线会被分钟级噪音扫掉；过近则推离至恰好该下界。
    basis = COMMIT_BASIS_ADOPTED
    if atr_px is not None and atr_floor_mult > 0:
        floor_dist = atr_floor_mult * atr_px
        if abs(mark_px - line) < floor_dist:
            line = mark_px - floor_dist if side == "long" else mark_px + floor_dist
            basis = COMMIT_BASIS_ATR_FLOOR
    # 3. 安全底线（复用 v2 入场价夹紧 / 抑制，v1 P0-1 止血范式）：绝不采纳一条会把持仓在入场价之下强平
    #    的「保护」线。[P2-1] fail-open：`floor_line is None`（保本线关且 entry 也缺）⇒ 直接 no_floor，
    #    绝不抛（回测直调 clamp，None 比较会 TypeError）。
    floor_line = breakeven_px if breakeven_px is not None else entry_px
    if floor_line is None:
        return None, "no_floor"
    worse = line < floor_line if side == "long" else line > floor_line
    if worse:
        if breakeven_px is not None:
            return float(breakeven_px), COMMIT_BASIS_CLAMPED_BREAKEVEN
        return None, "suppressed_at_floor"      # 保本线关且报价 < 入场价 ⇒ 抑制不采纳
    return line, basis


def ratchet_commitment(prev_px: float | None, prev_basis: str | None,
                       cand_px: float | None, cand_basis: str | None, *,
                       side: str) -> tuple[float | None, str | None]:
    """单调棘轮（[P1-2] 唯一棘轮真源，实盘与回测同调）：只能向现价收紧、绝不放松。

    `cand_px is None`（本次候选不可采纳）⇒ 前值原样保留；`prev_px is None`（本 epoch 首次承诺）⇒
    直接采纳候选；否则多头取更高 / 空头取更低（更松或相等一律拒绝，前值原样保留）。
    """
    if cand_px is None:
        return prev_px, prev_basis
    if prev_px is None:
        return cand_px, cand_basis
    tighter = cand_px > prev_px if side == "long" else cand_px < prev_px
    return (cand_px, cand_basis) if tighter else (prev_px, prev_basis)


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
        # 新增的 5 个键与三条线同待遇：`unavailable` 时**一律 None**（§2.1.3），绝不下发一个算得
        # 出来却不作数的数字。
        d = {"movePct": None, "returnPct": None, "peakReturnPct": None, "givebackRatio": None,
             "unrealizedPnlUsd": None, "peakUnrealizedPnlUsd": None,
             "trailStopPx": None, "breakevenPx": None, "hardTakeProfitPx": None,
             "givebackPctEffective": None, "trailBasis": None, "trailDistancePx": None,
             "trailClampedAtEntry": None}
        status = STATUS_UNAVAILABLE
        exit_px, exit_src = None, None
    else:
        d = _derive(rule, state, facts)
        status = (STATUS_ARMED if d["armed"]
                  else STATUS_BREAKEVEN_ARMED if d["breakevenArmed"] else STATUS_IDLE)
        exit_px, exit_src = _effective_exit(facts.side, d["trailStopPx"], d["breakevenPx"],
                                            facts.committed_exit_px)
    # 承诺线三键与三条退出线同待遇：unavailable ⇒ **一律 None**（[P1-3]，绝不下发算得出却不作数的价位）。
    committed_px = None if unavailable_reason is not None else facts.committed_exit_px
    committed_basis = None if unavailable_reason is not None else facts.committed_exit_basis
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
        # F1 新增五键（纯加法）：本轮的**有效**回吐阈、被哪条约束定住、回吐距离、是否被入场价夹紧、
        # 本轮 ATR。UI / prompt / explain 三处据同一份数据渲染，不各自重算（R-11 / [R-5] 教训）。
        "givebackPctEffective": _round_pct(d["givebackPctEffective"]),
        "trailBasis": d["trailBasis"],
        "trailDistancePx": _round_px(d["trailDistancePx"]),
        "trailClampedAtEntry": d["trailClampedAtEntry"],
        "atrPx": None if unavailable_reason is not None else _round_px(facts.atr_px),
        # 承诺退出线三键（纯加法，§2.2.3 / §4.3）：已棘轮的承诺线、它被哪条约束定住、以及有效退出线
        # 当前**由哪条线定住**（[P1-3] 后端单点归因真源，前端只读不推）。unavailable ⇒ 三键皆 None。
        "committedExitPx": _round_px(committed_px),
        "committedExitBasis": committed_basis,
        "effectiveExitSource": exit_src,
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


def evaluate_profit_guard(snapshot: dict | None, rule: ProfitGuardRule | None, *,
                          allow: tuple[str, ...] | None = None) -> ProtectVerdict | None:
    """三条规则按固定优先级短路判定（§2.4）；**任一分支不成立一律 `None`（不动作）**。

    优先级：硬止盈 → 移动止盈 → 保本。硬止盈在最前是因为它是「已达目标位」而非「已在回吐」，
    两者同时成立时按目标位记因更贴事实。

    Args:
        snapshot: `build_snapshot` 产物；`None` / `status=="unavailable"` ⇒ 直接不动作（fail-open）。
        rule: 已装配规则；`None` / `enabled=False` ⇒ 恒不动作。
        allow: keyword-only 的**规则码白名单**（F3 §2.3.1 的保守成交序用：先只判两条下方保护线、
            推完峰值再只判硬止盈）。`None`（默认）= 全部规则参与 ⇒ 实盘调用点行为逐字节不变。

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
    allowed = (lambda code: True) if allow is None else (lambda code: code in allow)
    hard_tp_px = snapshot.get("hardTakeProfitPx")
    return_pct = snapshot.get("returnPct")
    if (allowed(REASON_HARD_TAKE_PROFIT) and rule.hard_tp_return_pct > 0
            and return_pct is not None and return_pct >= rule.hard_tp_return_pct):
        return _verdict(REASON_HARD_TAKE_PROFIT, snapshot, hard_tp_px)
    # 三条**下方**保护线（移动止盈 / 承诺线 / 保本）合一：取最靠近现价者**一次**判定，命中即以其归因码
    # 出裁决（§2.2.3）。[P2-2] 不变式：本单次判定等价逐线判定的前提是三码在 `allow` 中**一致放行**——
    # 现有调用方均满足（实盘 allow=None 全放；回测回撤三线全放；回测目标仅硬止盈、下方块 exit_src 必被
    # `allowed` 挡掉）。若未来出现非一致 allow，须改回「按靠近程度遍历、返回首个 allowed 且 touched 者」。
    exit_px, exit_src = _effective_exit(side, snapshot.get("trailStopPx"),
                                        snapshot.get("breakevenPx"),
                                        snapshot.get("committedExitPx"))
    if exit_src is not None and allowed(exit_src) and _touched(side, px, exit_px):
        return _verdict(exit_src, snapshot, exit_px)
    return None
