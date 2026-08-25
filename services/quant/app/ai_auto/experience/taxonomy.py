"""语境闭合词表（ai-trade-experience-context-graph §4.2，纯常量 + 纯函数、零 IO）。

不变式 I-5 的落点：**语境轴与取值只能来自本模块的常量表**。开放词表会让图不可查询，也会让
LLM 蒸馏自己发明坐标轴——一个模型编出来的 `axis="心情"` 在图上永远匹配不到任何东西，却会
静默占掉一条经验的全部权重。

十一条轴的取值**逐字取自仓库里已经存在且已被验证的枚举**，不新造分类器：

- `regime` / `volatility` / `event` —— `market_context.py` docstring 明列的三组（`classify_regime` 固定产出）。
- `wake` —— `wake_reason.WAKE_KINDS`（**八值**）。注意：`payload["trigger"]` 的 `candle` / `interval`
  是**另一个**二值枚举，不是 `wake_kind`；把它俩混起来会让整条 `wake` 轴永远匹配不上。
- `exit` —— `exit_attribution.EXIT_SOURCES`（九值，直接复用）。
- `horizon` —— `hold_horizon.PROFILES` 的键（`HorizonRule.profile_id`，**不是** wire 键 `profile`）+ `none`。
- `bar` —— `okx.types.BAR_SECONDS` 的键。

两条口径纪律：

1. **未知取值一律折成 `unknown`，绝不丢事件**——丢事件会造成幸存者偏差（被强平的、跨越暂停的
   那些往返恰恰最该被学到）。
2. `symbol` 是**开放轴**（instId 原文）：它的取值域由市场决定，不可能枚举。它不参与 LLM 蒸馏的
   `enum` 约束（蒸馏的标的由调用方按会话实际标的限定）。
"""
from __future__ import annotations

from ..exit_attribution import EXIT_SOURCES
from ..hold_horizon import PROFILES
from ..wake_reason import WAKE_KINDS

UNKNOWN = "unknown"

# 轴 id 一律小写蛇形（`canonical_key` 直接拼），wire 侧的 camelCase 映射见 `AXIS_WIRE`。
AXIS_SYMBOL = "symbol"
AXIS_BAR = "bar"
AXIS_REGIME = "regime"
AXIS_VOLATILITY = "volatility"
AXIS_EVENT = "event"
AXIS_SIDE = "side"
AXIS_WAKE = "wake"
AXIS_EXIT = "exit"
AXIS_HORIZON = "horizon"
AXIS_COST_REGIME = "cost_regime"
AXIS_CONFIDENCE = "confidence"

# 顺序即渲染顺序与桶枚举顺序（决定性排序的一半，见 `retrieval` 的 `(score, key)` 二元键）。
AXES: tuple[str, ...] = (
    AXIS_SYMBOL, AXIS_BAR, AXIS_REGIME, AXIS_VOLATILITY, AXIS_EVENT, AXIS_SIDE,
    AXIS_WAKE, AXIS_EXIT, AXIS_HORIZON, AXIS_COST_REGIME, AXIS_CONFIDENCE,
)

# 开仓瞬刻冻结的九轴（§4.3）：`exit` 在平仓时补齐，`cost_regime` 在聚合期现算（§4.2 评审 P1-9）。
OPEN_AXES: tuple[str, ...] = (
    AXIS_SYMBOL, AXIS_BAR, AXIS_REGIME, AXIS_VOLATILITY, AXIS_EVENT, AXIS_SIDE,
    AXIS_WAKE, AXIS_HORIZON, AXIS_CONFIDENCE,
)

# `symbol` 之外的十条闭合轴的取值域（`symbol` 是开放轴，见模块 docstring）。
AXIS_VALUES: dict[str, tuple[str, ...]] = {
    AXIS_BAR: ("1m", "5m", "15m", "30m", "1H", "4H", "12H", "1D"),
    AXIS_REGIME: ("trend_up", "trend_down", "range", UNKNOWN),
    AXIS_VOLATILITY: ("high_volatility", "low_volatility", "normal", UNKNOWN),
    AXIS_EVENT: ("crash", "breakout", "none", UNKNOWN),
    AXIS_SIDE: ("long", "short", UNKNOWN),
    AXIS_WAKE: tuple(WAKE_KINDS) + (UNKNOWN,),
    AXIS_EXIT: tuple(EXIT_SOURCES),                 # 已含 "unknown"
    AXIS_HORIZON: tuple(PROFILES.keys()) + ("none", UNKNOWN),
    AXIS_COST_REGIME: ("low", "mid", "high", UNKNOWN),
    AXIS_CONFIDENCE: ("low", "mid", "high", UNKNOWN),
}

# 轴 id → wire 键（前端 / 预演入参用 camelCase，与仓库既有 wire 风格一致）。
AXIS_WIRE: dict[str, str] = {axis: axis for axis in AXES}
AXIS_WIRE[AXIS_COST_REGIME] = "costRegime"

AXIS_LABEL_ZH: dict[str, str] = {
    AXIS_SYMBOL: "标的",
    AXIS_BAR: "周期",
    AXIS_REGIME: "体制",
    AXIS_VOLATILITY: "波动",
    AXIS_EVENT: "事件",
    AXIS_SIDE: "方向",
    AXIS_WAKE: "唤醒",
    AXIS_EXIT: "退出",
    AXIS_HORIZON: "持仓周期",
    AXIS_COST_REGIME: "成本档",
    AXIS_CONFIDENCE: "置信度",
}

VALUE_LABEL_ZH: dict[str, str] = {
    # regime
    "trend_up": "上升趋势", "trend_down": "下降趋势", "range": "震荡",
    # volatility
    "high_volatility": "高波动", "low_volatility": "低波动", "normal": "常波动",
    # event
    "crash": "急跌", "breakout": "突破", "none": "无事件",
    # side
    "long": "做多", "short": "做空",
    # wake（八值，逐字对齐 WAKE_KINDS）
    "candle_close": "新K线收线", "bar_advanced": "补跑上根", "signal_flip": "信号翻转",
    "resting_order": "挂单待成", "profit_tier": "利润档位", "price_breakout": "价格异动",
    "position_watch": "持仓盯market", "heartbeat": "心跳",
    # exit（九值，逐字对齐 EXIT_SOURCES）
    "agent": "Agent主动平", "agentReverse": "Agent反手", "profitGuard": "利润保护",
    "exchangeOco": "交易所OCO", "timeExit": "到期离场", "sessionStop": "会话止盈损",
    "windowEnd": "区间结束", "external": "外部干预",
    # horizon
    "hourly_swing": "小时波段", "intraday_swing": "日内波段", "short_swing": "短波段",
    "trend_hold": "趋势持仓", "custom": "自定义",
    # cost_regime / confidence
    "low": "低", "mid": "中", "high": "高",
    UNKNOWN: "未知",
}
# 「持仓盯market」是一处笔误的物理防线：常量表在这里被逐条读过一遍。
VALUE_LABEL_ZH["position_watch"] = "持仓盯市"


def canonical_key(axis: str, value: str) -> str:
    """语境节点的稳定 key：`axis:value`（轴名小写、取值原样）。

    Args:
        axis: `AXES` 中的轴 id。
        value: 该轴的取值（未归一化亦可，本函数不做折叠）。

    Returns:
        `"regime:range"` 这样的 key；节点表的 `key` 列与检索种子共用它。
    """
    return f"{str(axis).strip().lower()}:{str(value).strip()}"


def fold(axis: str, value) -> str:
    """把任意取值折进闭合词表；越界 / 空 ⇒ `unknown`（**绝不丢事件**）。

    `symbol` 轴是开放轴：非空即原样（去空白），空 ⇒ `unknown`。

    Args:
        axis: 轴 id。
        value: 原始取值（可能是 None / 非字符串 / 越界枚举）。

    Returns:
        闭合词表内的取值字符串。
    """
    text = "" if value is None else str(value).strip()
    if not text:
        return UNKNOWN
    if axis == AXIS_SYMBOL:
        return text
    allowed = AXIS_VALUES.get(axis)
    if allowed is None:
        return UNKNOWN
    return text if text in allowed else UNKNOWN


def is_valid(axis: str, value: str) -> bool:
    """该 `(axis, value)` 是否在闭合词表内（蒸馏 / 导入的越界判据，越界即整条丢弃）。"""
    if axis == AXIS_SYMBOL:
        return bool(str(value or "").strip())
    return str(value) in (AXIS_VALUES.get(axis) or ())


def confidence_band(value) -> str:
    """决策置信度 → 三档（`<0.5` low / `[0.5,0.75)` mid / `≥0.75` high）；缺席 ⇒ `unknown`。"""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return UNKNOWN
    if number != number:            # NaN
        return UNKNOWN
    if number < 0.5:
        return "low"
    return "mid" if number < 0.75 else "high"


def label_of(axis: str, value: str) -> str:
    """语境的中文展示名（`高波动`）；未登记的取值原样回显（标的名走这条路）。"""
    return VALUE_LABEL_ZH.get(str(value), str(value))


def describe(signature: dict) -> str:
    """把一个语境签名压成一句人读的短语（`高波动+突破 做多`），供模板与 UI 共用。

    刻意**不含标的与周期**：它们由模板的前缀单独渲染（`{标的} {周期} · {语境}`），
    在这里重复一次会让 120 字上限被两遍标的名吃掉。
    """
    parts = [label_of(axis, signature[axis]) for axis in AXES
             if axis in signature and axis not in (AXIS_SYMBOL, AXIS_BAR)
             and signature[axis] != UNKNOWN]
    return "+".join(parts) if parts else "全局"


def signature_key(signature: dict) -> str:
    """语境签名的**决定性**串（按 `AXES` 顺序拼），用作桶身份与 `lesson:` 哈希的输入。"""
    return "|".join(f"{axis}={signature[axis]}" for axis in AXES if axis in signature)
