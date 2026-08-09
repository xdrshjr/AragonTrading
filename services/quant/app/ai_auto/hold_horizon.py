"""持仓周期铁律层纯核心（ai-trade-holding-horizon-modes §2.2 / §2.3）。

线上样本给出的结论是「毛利润为正、被换手成本吃成净亏损」：16 次往返里 15 次由 Agent 主动平仓、
14 笔时间戳可比的交易里 11 笔持仓不足 1 小时。根因不在某一条参数，而在**没有任何一层约束 Agent
主动平仓的最短持仓时间**——`profit_guard` 的 `min_hold_s` 只抑制保护层自身，强制时间平仓默认关闭。

本模块把「交易周期模式」实现为**信号板之外的一层确定性门禁**（与 `base_rules`（MA 通道开单铁律）、
`profit_guard`（利润保护）逐字同构）：纯函数核心（无 IO / 无 LLM / 无 broker）+ 会话侧薄封装运行时
（`horizon_runtime.py`）+ 宪章 prompt（`hold_horizon_text.py`）；实盘 `session.py` 与回测
`backtest.py` 调**同一个** `evaluate_horizon_gate`，差别只在「谁提供 `HorizonFacts`」。

四个不可动摇的不变式（改本文件前先读）：
1. **只约束 Agent 主动发起的换手**（§2.3.1 豁免矩阵冻结）：`close` 与反手受约束；`hold` /
   `cancel_order` / 空仓开仓**永不**被拦。止损、交易所 OCO、利润保护层、会话止盈止损、executor 的
   强制时间平仓一律不经本层，故天然不受影响——这是本层唯一不可动摇的安全语义。
2. **fail-open**（§2.3.4，与 `base_rules` 的 fail-closed 相反、与 `profit_guard` 相同）：本层拦的是
   `close`，而 `close` 是风险收敛动作。任何「算不出 ⇒ 不许平仓」的设计都会在断网 / 暖机 / 脏数据的
   瞬间把一笔真实持仓锁死——那比它想解决的问题严重一个数量级。
3. **时钟是墙钟秒，不是根数**（§2.3.2 G1）：执行周期可变而目标持仓是一个物理时长，用根数会在
   15m/4H 之间产生 16 倍的量纲差（`trend_band_swing` 与 `wake-aware-context` 踩过同一个坑）。
4. **锁必须有钥匙**（§2.3.2 G2 / [P0-3]）：连续确认带墙钟上界 `confirm_window_s`，超时即视为确认
   完成。没有它，`trend_hold`（唤醒 0 + 4H + 3 轮）在行情静默期的离场推迟**没有上界**。
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from ..okx.types import BAR_SECONDS

# 三道闸的稳定标识（进快照 `releasedGates`，前后端镜像；顺序即 §2.3.2 的判定顺序）。
GATE_MIN_HOLD = "G1"
GATE_EXIT_CONFIRM = "G2"
GATE_HYSTERESIS = "G3"
_ALL_GATES: tuple[str, ...] = (GATE_MIN_HOLD, GATE_EXIT_CONFIRM, GATE_HYSTERESIS)

# 豁免原因（§2.3.3 冻结矩阵的键）。`epoch_estimated` **只解 G1**——受污染的只有「持仓时长」这一个
# 量，全解等于借一个局部不确定性把整层关掉（[P0-2]）。
RELEASE_LOSS = "loss_release"
RELEASE_STALE_PX = "stale_px"
RELEASE_EPOCH_ESTIMATED = "epoch_estimated"
RELEASE_MAX_HOLD_BARS = "max_hold_bars"
# 逐闸解除矩阵（冻结口径；`test_ai_trade_horizon_exemptions.py` 与 H-11~H-14 逐格对照）。
RELEASE_GATES: dict[str, tuple[str, ...]] = {
    RELEASE_LOSS: _ALL_GATES,
    RELEASE_STALE_PX: _ALL_GATES,
    RELEASE_EPOCH_ESTIMATED: (GATE_MIN_HOLD,),
    RELEASE_MAX_HOLD_BARS: _ALL_GATES,
}

# 降级码前缀（前端 `DEGRADE_REASON_LABELS` 逐条镜像；带参数后缀，与 `low_consensus(k/n)` 同风格）。
REASON_MIN_HOLD = "horizon_min_hold"
REASON_EXIT_UNCONFIRMED = "horizon_exit_unconfirmed"
REASON_HYSTERESIS = "horizon_turnover_hysteresis"

STATUS_FLAT = "flat"
STATUS_HOLDING = "holding"
STATUS_RELEASED = "released"

# 派生量的护栏：`expected_hold_bars` 与 schema 的 `ge=1, le=1000` 同域（越界即被夹到边界，
# 绝不让一个 3 天 / 1m 的组合派生出 4320 根去把资金费期数放大到荒谬）。
_MAX_EXPECTED_HOLD_BARS = 1000


@dataclass(frozen=True)
class HorizonProfile:
    """模式预设表条目（§2.2；模块级常量 `PROFILES` 是唯一真源，前端不复制任何一个数字）。

    `min_hold_s` / `expected_hold_bars` / `confirm_window_s` **刻意不是字段**：它们恒由 §2.2 的三条
    式子派生（把表里的数字手抄第二遍必然漂移，G-10 的正则扫描同时守前端不许硬编码这些数）。

    Attributes:
        id: 稳定标识（wire 值，前后端镜像）。
        title: 中文标题（**由后端下发**，前端不复制）。
        description: 一句话说明（弹窗卡片副标题）。
        target_label: 目标持仓的人话区间；`custom` 为 `None`（[P1-8]）。
        bar: 建议执行周期；`custom` 为 `None` ⇒ 前端不执行任何联动。
        wake_interval_minutes: 建议唤醒周期（分钟；`0` = 仅 K 线 / 信号驱动）。
        target_hold_min_s / target_hold_max_s: 目标持仓区间（秒）。
        exit_confirm_rounds: 离场连续确认轮数。
        hysteresis_k: 换手迟滞倍数（浮盈须覆盖 k × 往返成本）。
        leverage_max_hint: 建议杠杆上限（**只提示不阻断**——杠杆是用户的资金决策）。
    """

    id: str
    title: str
    description: str
    target_label: str | None
    bar: str | None
    wake_interval_minutes: float | None
    target_hold_min_s: float | None
    target_hold_max_s: float | None
    exit_confirm_rounds: int | None
    hysteresis_k: float | None
    leverage_max_hint: float | None


PROFILES: dict[str, HorizonProfile] = {
    "hourly_swing": HorizonProfile(
        id="hourly_swing", title="小时波段",
        description="在分钟级别上捕捉一段快速行情，数十分钟到两小时内完成一次往返。",
        target_label="6 分钟 – 2 小时", bar="5m", wake_interval_minutes=3.0,
        target_hold_min_s=360.0, target_hold_max_s=7200.0,
        exit_confirm_rounds=2, hysteresis_k=1.2, leverage_max_hint=5.0),
    "intraday_swing": HorizonProfile(
        id="intraday_swing", title="日内波段",
        description="在小时级别上捕捉一段趋势，日内完成一到两次往返。",
        target_label="4 – 12 小时", bar="1H", wake_interval_minutes=30.0,
        target_hold_min_s=14400.0, target_hold_max_s=43200.0,
        exit_confirm_rounds=2, hysteresis_k=1.5, leverage_max_hint=3.0),
    "short_swing": HorizonProfile(
        id="short_swing", title="短波段",
        description="跨日持有一段结构性行情，一到两天内完成一次往返。",
        target_label="12 – 48 小时", bar="4H", wake_interval_minutes=120.0,
        target_hold_min_s=43200.0, target_hold_max_s=172800.0,
        exit_confirm_rounds=3, hysteresis_k=2.0, leverage_max_hint=2.0),
    "trend_hold": HorizonProfile(
        id="trend_hold", title="趋势持仓",
        description="跟随一段中线趋势，只在 K 线确认或有效信号事件时重新研判。",
        target_label="1 – 3 天", bar="4H", wake_interval_minutes=0.0,
        target_hold_min_s=86400.0, target_hold_max_s=259200.0,
        exit_confirm_rounds=3, hysteresis_k=2.0, leverage_max_hint=2.0),
    # [P1-8] `custom` 除 id/title/description 外**全部为 None**：这些格子在 §2.2 表里是「不联动 /
    # 用户填」，若给出数字，前端渲染该卡片时会拿到一组无意义的值并据此联动 bar 与唤醒周期。
    "custom": HorizonProfile(
        id="custom", title="自定义",
        description="自行设定目标持仓与三道闸参数；全部留空即不施加任何约束。",
        target_label=None, bar=None, wake_interval_minutes=None,
        target_hold_min_s=None, target_hold_max_s=None,
        exit_confirm_rounds=None, hysteresis_k=None, leverage_max_hint=None),
}


@dataclass(frozen=True)
class HorizonRule:
    """会话级不可变契约（随 `AiTradeSession` 生命周期，§5.2）。

    `custom` 全默认（`min_hold_s=0` / `exit_confirm_rounds=1` / `hysteresis_k=0` /
    `expected_hold_bars=None`）时本规则**逐字等价于未装配**：G1 恒放行、G2 首轮即满足、G3 恒放行、
    成本与先验的前瞻窗口逐字回退全局常数。这是「自定义」的安全退出口（H-2 / H-24 守它）。
    """

    enabled: bool = True
    profile_id: str = "intraday_swing"
    title: str = ""
    target_label: str | None = None
    min_hold_s: float = 0.0
    exit_confirm_rounds: int = 1
    # [P0-3] G2 的墙钟上界；0 = 窗口机制关闭（仅当 `exit_confirm_rounds<=1`、G2 本就空转时成立）。
    confirm_window_s: float = 0.0
    hysteresis_k: float = 0.0
    target_hold_min_s: float = 0.0
    target_hold_max_s: float = 0.0
    # [P1-1] `None` ⇒ 成本模型与信号先验的前瞻窗口逐字回退全局常数（custom 全默认即此值）。
    expected_hold_bars: int | None = None
    gate_reverse: bool = True
    enforce_max_hold: bool = False
    loss_release_frac: float = 0.5
    loss_release_return_pct_fallback: float = 0.10
    # 连续被本层拦截 N 轮后注入自察注记（§2.9(e)）；0 = 关闭 ⇒ `blockedStreakNote` 整键缺席。
    note_after_rounds: int = 0
    # 判定价最大允许年龄（秒）：**复用** `ai_trade_profit_guard_max_px_age_s`——「判定价多老算老」
    # 在一个会话里只能有一个答案（§5.1）。
    max_px_age_s: float = 90.0


@dataclass(frozen=True)
class HorizonState:
    """运行时的 epoch 作用域状态（由 `HorizonRuntime` 持有并整体替换；纯核心只读它）。

    Attributes:
        epoch: `(side, seq)` 身份戳，与利润保护层完全同源（`profit_guard.advance_state`）。
        opened_wall: 本 epoch 的开仓墙钟（`note_open` 锁定；部分成交不重置）。
        opened_wall_estimated: `opened_wall` 是否为估算值（未观察到开仓成交事件）。
        exit_confirm_streak: 本 epoch 内**连续**离场意图轮数（含本轮；第一阶段决策口径）。
        first_exit_intent_wall: 本 epoch 内第一次离场意图的墙钟（streak 归零时一并清空）。
        blocked_streak: 本 epoch 内被本层**连续**拦下的轮数（放行 / 豁免 / epoch 变化即归零）。
    """

    epoch: tuple[str, int] | None = None
    opened_wall: float = 0.0
    opened_wall_estimated: bool = False
    exit_confirm_streak: int = 0
    first_exit_intent_wall: float | None = None
    blocked_streak: int = 0


@dataclass(frozen=True)
class HorizonFacts:
    """一次判定所需的断面（聚合结构体，守 CLAUDE.md「形参 ≤5」）。

    Attributes:
        seq: 会话级持仓序号（`session._position_seq`；回测取 `pos.entry_bar`）。
        side: `long` / `short` / `flat`——实盘取 `executor.projected`（**投影立场**，[P2-2]）。
        entry_px: 开仓均价。
        mark_px: 判定价（实盘恒 `session._current_px()`，回测取当前根收盘价；单源硬约束）。
        leverage: 杠杆（`return_pct` 的保证金收益率口径由它派生）。
        now_wall: 判定时刻（实盘墙钟秒，回测取当前根 ts）。
        px_age_s: 判定价年龄（秒）；`None` ⇒ 不判新鲜度（回测按根推进，无此概念）。
        stale_feed: 会话级行情降级标志。
        bars_in_position: 已持仓根数（`max_hold_bars` 豁免用）。
        max_hold_bars: executor 的强制时间平仓根数（`None`/0 = 未启用）。
        stop_loss_pct: 单仓 OCO 止损%（`loss_release` 阈的派生源）。
        cost_bps: 按**当前持仓方向**估的往返成本（G3 用；`None` ⇒ 本闸 fail-open 放行）。
        exchange_sl_px / exchange_tp_px: 交易所侧兜底触发价（**只读转发**，§2.8，不重算）。
    """

    seq: int
    side: str
    entry_px: float
    mark_px: float
    leverage: float
    now_wall: float
    px_age_s: float | None = None
    stale_feed: bool = False
    bars_in_position: int | None = None
    max_hold_bars: int | None = None
    stop_loss_pct: float | None = None
    cost_bps: float | None = None
    exchange_sl_px: float | None = None
    exchange_tp_px: float | None = None


# ============================ §2.2 三条派生规则（唯一真源，不得手抄第二遍） ============================
def derive_min_hold_s(target_hold_min_s: float | None) -> float:
    """最短主动平仓窗口 = 目标下界的一半（§2.2 / D-5）。

    取一半而非取满：硬地板的职责是掐掉明显的噪音换手，不是替模型做判断；完整目标区间通过 prompt
    软引导传达。取满会让「目标 4–12 小时」变成「4 小时内不许动」，超出「让用户选周期」的授权。
    """
    return max(0.0, 0.5 * float(target_hold_min_s or 0.0))


def derive_expected_hold_bars(target_hold_max_s: float | None, bar: str) -> int | None:
    """成本资金费期数与先验前瞻窗口的根数（§2.2；按**用户实际选择的 bar** 现算）。

    取上界而非中值：唯一消费方是成本模型的资金费期数——成本估算宁可高估不可低估（`cost_model`
    既有极性）。**`target_hold_max_s <= 0` ⇒ `None`**（[P1-1]）：`None` 一路透传到
    `resolve_cost_params` / `backtest_params` / `resolve_prior_params` 的可选形参即逐字回退全局
    常数。若改成 `clamp(ceil(0/bar), 1, 1000)` 会派生出 `1`——资金费期数被压成 1 期、先验快照按
    `horizon_mismatch` 被整份丢弃，而这恰好发生在「自定义 + 什么都不填」这条本应零副作用的路径上。
    """
    seconds = float(target_hold_max_s or 0.0)
    bar_seconds = float(BAR_SECONDS.get(bar) or 0)
    if seconds <= 0 or bar_seconds <= 0:
        return None
    return int(min(_MAX_EXPECTED_HOLD_BARS, max(1, math.ceil(seconds / bar_seconds))))


def derive_confirm_window_s(target_hold_min_s: float | None, bar: str) -> float:
    """G2 的墙钟上界（[P0-3]）= `min(0.25 × 目标下界, 4 × 一根执行周期)`。

    既不超过目标下界的四分之一，也不超过四根执行周期。`target_hold_min_s <= 0` ⇒ `0` ⇒ 窗口机制
    关闭（此时 `exit_confirm_rounds` 也已是 1，G2 本就空转）。
    """
    seconds = float(target_hold_min_s or 0.0)
    if seconds <= 0:
        return 0.0
    bar_seconds = float(BAR_SECONDS.get(bar) or 0)
    quarter = 0.25 * seconds
    return quarter if bar_seconds <= 0 else min(quarter, 4.0 * bar_seconds)


def _resolve_confirm_window_s(target_hold_min_s: float, exit_confirm_rounds: int,
                              bar: str) -> float:
    """会话级窗口：派生式为 0 但 G2 **真的在生效**时，回落到派生式的另一半 `4 × 一根执行周期`。

    派生式把「目标下界未设」读成 0，其隐含前提是「此时 `exit_confirm_rounds` 也是 1、G2 本就空转」
    （§2.2）。但自定义模式允许「只填确认轮数、不填目标区间」——那会得到一道**没有钥匙的锁**：唤醒 0
    的会话里一次离场意图可以被无限期悬置，正是 [P0-3] / R-14 要根除的形态。`min(a, b)` 在 a 未定义
    时理应退化为 b，故此处取四根执行周期，而非把窗口机制整个关掉。
    """
    window = derive_confirm_window_s(target_hold_min_s, bar)
    if window > 0 or exit_confirm_rounds <= 1:
        return window
    return 4.0 * float(BAR_SECONDS.get(bar) or 0)


def _fallback(value, default):
    """入参缺省（None）时回落预设；显式值（含 0/False）原样保留。"""
    return default if value is None else value


def resolve_horizon_rule(raw: dict | None, settings, *, bar: str) -> HorizonRule | None:
    """把请求里的 `tradingHorizon` 解析为契约（**默认开启的唯一解析点**，§4.2 / D-2）。

    四层语义：运维总闸关 ⇒ 恒 `None`；字段缺省 ⇒ 按 settings 装配默认模式（老客户端不发该字段也
    默认开启）；显式 `enabled=false` ⇒ `None`（该会话不装配）；逐项覆盖 ⇒ 覆盖预设。

    Args:
        raw: 请求中的 `tradingHorizon`（缺省为 None）；越界入参已在 schema 层 422。
        settings: 运行配置（提供总闸、默认模式、豁免阈与自察注记阈）。
        bar: 用户**实际选择**的执行周期——两个派生量按它现算，绝不按预设的建议周期算。

    Returns:
        装配后的 `HorizonRule`；总闸关闭或用户显式关闭时为 `None`（调用方据此整条链路不装配）。
    """
    if not settings.ai_trade_horizon_enabled:
        return None
    raw = raw or {}
    if raw.get("enabled") is False:
        return None
    profile = _resolve_profile(raw.get("profile"), settings)
    target_min = float(_fallback(raw.get("targetHoldMinSeconds"),
                                 profile.target_hold_min_s or 0.0))
    target_max = float(_fallback(raw.get("targetHoldMaxSeconds"),
                                 profile.target_hold_max_s or 0.0))
    rounds = int(_fallback(raw.get("exitConfirmRounds"), profile.exit_confirm_rounds or 1))
    return HorizonRule(
        enabled=True, profile_id=profile.id, title=profile.title,
        target_label=profile.target_label,
        min_hold_s=float(_fallback(raw.get("minHoldSeconds"), derive_min_hold_s(target_min))),
        exit_confirm_rounds=rounds,
        confirm_window_s=_resolve_confirm_window_s(target_min, rounds, bar),
        hysteresis_k=float(_fallback(raw.get("turnoverHysteresisK"),
                                     profile.hysteresis_k or 0.0)),
        target_hold_min_s=target_min, target_hold_max_s=target_max,
        expected_hold_bars=derive_expected_hold_bars(target_max, bar),
        gate_reverse=bool(settings.ai_trade_horizon_gate_reverse),
        enforce_max_hold=bool(raw.get("enforceMaxHold") or False),
        loss_release_frac=float(settings.ai_trade_horizon_loss_release_frac),
        loss_release_return_pct_fallback=float(
            settings.ai_trade_horizon_loss_release_return_pct),
        note_after_rounds=int(settings.ai_trade_horizon_note_after_rounds),
        max_px_age_s=float(settings.ai_trade_profit_guard_max_px_age_s))


def _resolve_profile(profile_id, settings) -> HorizonProfile:
    """取预设条目；未知 id 回落 settings 默认（枚举已在 schema 层 422，此处只是防御性兜底）。"""
    fallback = PROFILES[settings.ai_trade_horizon_default_profile]
    return PROFILES.get(str(profile_id or ""), fallback)


def rule_wire(rule: HorizonRule | None) -> dict | None:
    """契约 → camelCase 回执镜像（落 `config_json.tradingHorizon` 与 `group_status`）；未装配 → None。

    **必须同时带上两个派生量** `expectedHoldBars` / `confirmWindowSeconds`（[P1-9]/[P0-3]）：覆盖
    `targetHold*` 之后，它们是唯一能看出「我到底改了什么」的字段，只回显入参等于把派生关系藏起来。
    """
    if rule is None or not rule.enabled:
        return None
    return {"enabled": True, "profile": rule.profile_id, "title": rule.title,
            "targetLabel": rule.target_label,
            "minHoldSeconds": rule.min_hold_s,
            "exitConfirmRounds": rule.exit_confirm_rounds,
            "confirmWindowSeconds": rule.confirm_window_s,
            "turnoverHysteresisK": rule.hysteresis_k,
            "targetHoldMinSeconds": rule.target_hold_min_s,
            "targetHoldMaxSeconds": rule.target_hold_max_s,
            "expectedHoldBars": rule.expected_hold_bars,
            "gateReverse": rule.gate_reverse,
            "enforceMaxHold": rule.enforce_max_hold}


def profile_wire(profile: HorizonProfile) -> dict:
    """预设条目 → `AiTradeHorizonProfileOut` 形状（`custom` 除三个字符串外全为 `null`，[P1-8]）。

    `expectedHoldBars` / `confirmWindowSeconds` 是**按该条目自带 bar 的预览值**——实际生效值按用户
    所选 bar 现算（弹窗切换周期后会变），故前端必须把它们当预览而非承诺。
    """
    bar = profile.bar
    return {"id": profile.id, "title": profile.title, "description": profile.description,
            "targetLabel": profile.target_label, "bar": bar,
            "wakeIntervalMinutes": profile.wake_interval_minutes,
            "minHoldSeconds": (None if profile.target_hold_min_s is None
                               else derive_min_hold_s(profile.target_hold_min_s)),
            "exitConfirmRounds": profile.exit_confirm_rounds,
            "turnoverHysteresisK": profile.hysteresis_k,
            "targetHoldMinSeconds": profile.target_hold_min_s,
            "targetHoldMaxSeconds": profile.target_hold_max_s,
            "expectedHoldBars": (None if bar is None
                                 else derive_expected_hold_bars(profile.target_hold_max_s, bar)),
            "confirmWindowSeconds": (None if bar is None
                                     else derive_confirm_window_s(profile.target_hold_min_s, bar)),
            "leverageMaxHint": profile.leverage_max_hint}


def profiles_wire(settings, *, estimated_cost_bps: float | None = None) -> dict:
    """`GET /api/ai-trade/horizon-profiles` 的完整回执（§4.1）——前端不复制任何一个数字或中文标签。"""
    return {"enabled": bool(settings.ai_trade_horizon_enabled),
            "defaultProfile": str(settings.ai_trade_horizon_default_profile),
            "estimatedCostBps": estimated_cost_bps,
            "lossReleaseFrac": float(settings.ai_trade_horizon_loss_release_frac),
            "lossReleaseReturnPctFallback": float(
                settings.ai_trade_horizon_loss_release_return_pct),
            "profiles": [profile_wire(PROFILES[key]) for key in PROFILES]}


# ============================ §2.3.1 豁免矩阵（冻结） ============================
def gated_kind(action: str, position_side: str, *, gate_reverse: bool) -> str | None:
    """本轮动作是否受本层约束：`'close'` | `'reverse'` | `None`（None ⇒ 恒直通、同一对象返回）。

    **只有** Agent 主动发起的平仓与反手受约束。`hold` / `cancel_order` / 空仓开仓**永不**被拦；
    反手被否 ⇒ 整个决策降级为 `hold`（close 腿与 open 腿**都不发生**），与 `base_rules._veto` 的
    「绝不产生半反手态」语义逐字同构。
    """
    if action == "close" and position_side in ("long", "short"):
        return "close"
    if not gate_reverse:
        return None
    if action == "open_long" and position_side == "short":
        return "reverse"
    if action == "open_short" and position_side == "long":
        return "reverse"
    return None


# ============================ §2.3.3 豁免（release）与派生阈 ============================
def loss_release_return_pct(rule: HorizonRule, *, stop_loss_pct: float | None,
                            leverage: float) -> float:
    """解除阈（**保证金收益率**口径，正数=幅度）——派生量，不是第五个魔数。

    有单仓 OCO 止损 ⇒ 取「止损价格% × 杠杆 × frac」；否则回落配置常数。默认 `frac=0.5`：
    OCO 止损 5% × 5× 杠杆 = 25% 保证金亏损 ⇒ 浮亏达 −12.5% 时本层完全放手，Agent 重新拥有随时止损
    的自由。这一行是用户判断「这把锁安不安全」的唯一依据，故前端必须按同一式子**现算**（[P1-10]）。
    """
    if stop_loss_pct and float(stop_loss_pct) > 0 and float(leverage) > 0:
        return float(stop_loss_pct) * float(leverage) * float(rule.loss_release_frac)
    return float(rule.loss_release_return_pct_fallback)


def _is_bad_px(value) -> bool:
    """判定价 / 开仓价是否不可信（非有限、非正）——「算不出」绝不能变成「不许平」。"""
    try:
        px = float(value)
    except (TypeError, ValueError):
        return True
    return not math.isfinite(px) or px <= 0


def release_reason_for(rule: HorizonRule, state: HorizonState, facts: HorizonFacts,
                       *, return_pct: float | None) -> str | None:
    """本轮命中的豁免原因（§2.3.3 表，**自上而下取第一条**）；未命中 ⇒ `None`。

    优先级 `loss_release > stale_px > epoch_estimated > max_hold_bars` 是冻结口径：亏到解除阈时
    必须允许随时离场，这条结论不该被任何其它豁免的顺序影响。
    """
    threshold = loss_release_return_pct(rule, stop_loss_pct=facts.stop_loss_pct,
                                        leverage=facts.leverage)
    if return_pct is not None and threshold > 0 and return_pct <= -threshold:
        return RELEASE_LOSS
    stale = (facts.stale_feed or _is_bad_px(facts.mark_px) or _is_bad_px(facts.entry_px)
             or (facts.px_age_s is not None and rule.max_px_age_s > 0
                 and float(facts.px_age_s) > rule.max_px_age_s))
    if stale:
        return RELEASE_STALE_PX
    if state.opened_wall_estimated:
        return RELEASE_EPOCH_ESTIMATED
    cap = facts.max_hold_bars or 0
    if cap > 0 and facts.bars_in_position is not None and facts.bars_in_position >= cap:
        return RELEASE_MAX_HOLD_BARS
    return None


# ============================ §2.3.2 三道闸 + 快照 ============================
def _return_pct(facts: HorizonFacts) -> float | None:
    """保证金收益率（与利润保护层、OCO 同口径）；判定价 / 开仓价不可信 ⇒ `None`。"""
    if _is_bad_px(facts.mark_px) or _is_bad_px(facts.entry_px):
        return None
    if facts.side not in ("long", "short"):
        return None
    raw = (float(facts.mark_px) - float(facts.entry_px)) / float(facts.entry_px)
    signed = raw if facts.side == "long" else -raw
    return signed * max(1.0, float(facts.leverage or 1.0))


def _move_bps(facts: HorizonFacts) -> float | None:
    """方向化的**价格口径** bps（G3 与 `cost_bps` 同口径，[R-4] 量纲不变式）。"""
    if _is_bad_px(facts.mark_px) or _is_bad_px(facts.entry_px):
        return None
    if facts.side not in ("long", "short"):
        return None
    raw = (float(facts.mark_px) - float(facts.entry_px)) / float(facts.entry_px) * 1e4
    return raw if facts.side == "long" else -raw


def _fmt_minutes(seconds: float) -> str:
    return f"{seconds / 60.0:.0f}m"


def build_snapshot(rule: HorizonRule, state: HorizonState, facts: HorizonFacts) -> dict:
    """§5.4 形状的 wire 快照（payload / decision / status / trace 共用的**唯一真源**）。

    纯函数：状态推进由 `HorizonRuntime` 负责，本函数只读。文案模块 `hold_horizon_text` 只读本产物、
    **绝不重算任何一个数**（`profit_guard_text` 的 `[R-5]` 教训）。
    """
    hold_s = max(0.0, float(facts.now_wall) - float(state.opened_wall)) if state.epoch else 0.0
    return_pct = _return_pct(facts)
    move_bps = _move_bps(facts)
    reason = (None if facts.side not in ("long", "short")
              else release_reason_for(rule, state, facts, return_pct=return_pct))
    gates = RELEASE_GATES.get(reason or "", ())
    status = (STATUS_FLAT if facts.side not in ("long", "short")
              else STATUS_RELEASED if reason is not None else STATUS_HOLDING)
    required = (None if facts.cost_bps is None or rule.hysteresis_k <= 0
                else round(float(rule.hysteresis_k) * float(facts.cost_bps), 4))
    snapshot = {
        "profileId": rule.profile_id, "title": rule.title, "targetLabel": rule.target_label,
        "status": status, "side": facts.side,
        "holdSeconds": round(hold_s, 3),
        "barsInPosition": facts.bars_in_position,
        "targetHoldMinSeconds": rule.target_hold_min_s,
        "targetHoldMaxSeconds": rule.target_hold_max_s,
        "minHoldSeconds": rule.min_hold_s,
        "minHoldRemainingSeconds": round(max(0.0, rule.min_hold_s - hold_s), 3),
        "exitConfirmRounds": rule.exit_confirm_rounds,
        "exitConfirmStreak": state.exit_confirm_streak,
        "turnoverHysteresisK": rule.hysteresis_k,
        "moveBps": None if move_bps is None else round(move_bps, 4),
        "costBps": facts.cost_bps, "requiredMoveBps": required,
        "returnPct": None if return_pct is None else round(return_pct, 6),
        "lossReleaseReturnPct": round(loss_release_return_pct(
            rule, stop_loss_pct=facts.stop_loss_pct, leverage=facts.leverage), 6),
        "released": reason is not None, "releaseReason": reason,
        "releasedGates": list(gates),
        "blockedStreak": state.blocked_streak,
        "expectedHoldBars": rule.expected_hold_bars,
        "openedWallEstimated": state.opened_wall_estimated,
        "gateReverse": rule.gate_reverse,
        # [P1-7] 本快照的采集时刻：prompt 段带 round-start 的那一份、trace/decision 带 gate 的那一份，
        # 二者 `asOfWall` 必不相等且恒有 prompt < trace（G-4 据此断言，而非断言两份逐字段相等）。
        "asOfWall": float(facts.now_wall),
        "exchangeStopLossPx": facts.exchange_sl_px,
        "exchangeTakeProfitPx": facts.exchange_tp_px,
    }
    snapshot.update(_confirm_window_wire(rule, state, facts))
    if rule.note_after_rounds > 0 and state.blocked_streak >= rule.note_after_rounds:
        # §2.9(e)：**只在武装时才有这个键**（未武装 ⇒ 整键缺席 ⇒ prompt 逐字节回退）。文案里的数
        # 全部取自本快照的 `blockedStreak` / `minHoldRemainingSeconds`，渲染层不重算。
        snapshot["blockedStreakNote"] = True
    return snapshot


def _confirm_window_wire(rule: HorizonRule, state: HorizonState,
                         facts: HorizonFacts) -> dict:
    """G2 墙钟窗口的三个只读键（[P0-3]）；窗口关闭 / 尚无离场意图 ⇒ 剩余为 `None`、未到期。"""
    window = float(rule.confirm_window_s)
    first = state.first_exit_intent_wall
    if window <= 0 or first is None:
        return {"confirmWindowSeconds": window, "confirmWindowRemainingSeconds": None,
                "confirmWindowElapsed": False}
    remaining = window - (float(facts.now_wall) - float(first))
    return {"confirmWindowSeconds": window,
            "confirmWindowRemainingSeconds": round(max(0.0, remaining), 3),
            "confirmWindowElapsed": remaining <= 0}


def evaluate_horizon_gate(decision, snapshot: dict, rule: HorizonRule | None, *,
                          position_side: str):
    """三道闸判定：受约束的主动换手被否 ⇒ 本轮硬降级为 `hold`（独立于 LLM，仅更保守）。

    未装配 / 非受控动作 / 该闸已被豁免解除时返回**同一个** decision 对象（`is` 相等，H-1/H-15），
    绝不顺手 `replace`。

    Args:
        decision: 决策器本轮产出（已过安全回落与既有门禁）。
        snapshot: `build_snapshot` 产物（**判定与下发恒取同一份**，[P1-7]）。
        rule: 已装配契约；`None` 即不装配 ⇒ 恒直通。
        position_side: 当前**投影立场**（实盘 `executor.projected`，回测 `pos.side`，[P2-2]）。

    Returns:
        `(decision, gate_reason)`——`gate_reason` 非 None 表示本轮被本层降级为 hold。
    """
    if rule is None or not rule.enabled or not snapshot:
        return decision, None
    if gated_kind(decision.action, position_side, gate_reverse=rule.gate_reverse) is None:
        return decision, None
    released = set(snapshot.get("releasedGates") or ())
    reason = _first_gate_reason(rule, snapshot, released)
    return (decision, None) if reason is None else (_veto(decision, reason), reason)


def _first_gate_reason(rule: HorizonRule, snapshot: dict, released: set) -> str | None:
    """G1 → G2 → G3 顺序取第一条不满足的闸；被豁免解除的闸直接跳过。"""
    if GATE_MIN_HOLD not in released:
        hold_s = float(snapshot.get("holdSeconds") or 0.0)
        if hold_s < rule.min_hold_s:
            return (f"{REASON_MIN_HOLD}(held={_fmt_minutes(hold_s)},"
                    f"min={_fmt_minutes(rule.min_hold_s)})")
    # `exit_confirm_rounds <= 1` ⇒ 本闸**结构性空转**（首轮即满足）。这条早退不是优化：`streak` 由
    # `note_intent` 在门禁之前推进，而纯核心不该依赖调用顺序才成立——H-2 的「custom 全默认 ⇒ 逐字
    # 等价于未装配」必须是结构性质，否则任何一处忘了调 `note_intent` 就会把 close 锁死。
    if GATE_EXIT_CONFIRM not in released and rule.exit_confirm_rounds > 1:
        # `streak` **含本轮**（`note_intent` 在一切门禁之前推进，§2.5 硬约束 4）：故判据是
        # `streak < rounds` 而不是 spec 伪码里的 `streak + 1 < rounds`（后者假设 streak 不含本轮，
        # 与钉死的调用顺序矛盾，会让 H-5 的「首轮 1/2 被否」变成放行）。详见 §十一 实施发现。
        streak = int(snapshot.get("exitConfirmStreak") or 0)
        if streak < rule.exit_confirm_rounds and not snapshot.get("confirmWindowElapsed"):
            return f"{REASON_EXIT_UNCONFIRMED}({streak}/{rule.exit_confirm_rounds})"
    if GATE_HYSTERESIS not in released:
        move = snapshot.get("moveBps")
        required = snapshot.get("requiredMoveBps")
        # `move <= 0` 一律放行：止损、割肉、认错离场永远不受迟滞约束（D-4）。
        # `required is None` ⇒ 成本不可算或 k=0 ⇒ fail-open 放行。
        if move is not None and required is not None and 0 < float(move) < float(required):
            return f"{REASON_HYSTERESIS}(move={float(move):.1f}bps,req={float(required):.1f}bps)"
    return None


def _veto(decision, reason: str):
    """被本层否决：整个决策（含反手的 close+open 两腿）不发生，本轮降级为 hold。

    延迟导入 `TradeDecision` 打破 `decider → prompts → hold_horizon_text` 的潜在环。
    """
    from .decider import TradeDecision

    return TradeDecision(action="hold", size_fraction=None, confidence=None, reason=reason)


def gate_status(snapshot: dict | None, gate_reason: str | None) -> str:
    """轨迹段状态（§2.11）：空仓 → `idle`；被否 → `veto`；豁免 → `released`；其余 → `pass`。"""
    if not snapshot or snapshot.get("status") == STATUS_FLAT:
        return "idle"
    if gate_reason:
        return "veto"
    return "released" if snapshot.get("released") else "pass"
