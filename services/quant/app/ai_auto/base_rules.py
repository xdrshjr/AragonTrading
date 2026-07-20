"""【基础策略】铁律层纯核心（ai-trade-base-strategy-ma-band §2.1~§2.4）。

需求把「基础策略」与常规/组合/因子策略并列成「第四种策略类型」，但那三者在本仓库是**策略库里的行**
（`StrategyMeta.kind ∈ {atomic, combo, factor}`），进入 AI 自动交易的唯一通路是
`strategyIds → specs → 影子 broker → signalBoard`，而系统 prompt **宪法级地**写明「信号是参考不是指令，
你有权推翻任何单一信号」——**把「必须强制遵守」的规则做成信号板成员，等于把铁律降级成建议**。故本模块把
基础策略实现为**独立于信号板的硬约束层**：宪章 prompt（软引导，让 Agent 主动遵守）+ 本模块的确定性门禁
（硬保证，让 Agent 无法违反）。

本模块是「算通道」「判合规」「渲染文案」三件事的**唯一真源**（三处各写一份必然出现 UI 显示「合规」而
门禁判「否决」的信任崩塌，[R-5] 教训）；纯函数、无 IO、无 LLM、无 broker，实盘 `session` 与回测
`backtest` 共用同一份核心（§2.8/P1-4），镜像 `signals.apply_consensus_gate` 的既有姿态。

三个不可动摇的不变式（改本文件前先读）：
1. **判定价是委托价**（§2.4/P0-3）：限价总闸默认开，Agent 可在最新价 ±2% 内任选委托价，真实成交价 =
   委托价。门禁若只判 `lastPx`，一张「lastPx 在中轨、委托价挂到带外」的限价单会被完整放行。
2. **fail-closed**（§2.6/P0-4）：通道算不出时开仓一律否决——「无法验证合规」不等于「合规」。
3. **豁免矩阵**（§2.3/P1-7）：`close` / `hold` / `cancel_order` **永不**被拦（镜像共识门禁的同款豁免），
   否则铁律会妨碍离场保护；反向开仓被否 = 两腿都不发生，绝不产生「放行 close 腿」的半反手态。
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass

import pandas as pd

from ..config import Settings
from ..engine.indicators import bollinger
from .decider import TradeDecision

logger = logging.getLogger(__name__)

# 预置规则 id（本期只有这一条；注册表结构留位但不提前造框架，§1.3）。
MA_BAND_RULE_ID = "ma_band_entry"

# 三个锚点的 %B 坐标：下轨 0.0 / 中轨 0.5 / 上轨 1.0（顺序即渲染顺序）。
_ANCHORS: tuple[tuple[str, float], ...] = (("lower", 0.0), ("mid", 0.5), ("upper", 1.0))
# 容差上界（§2.2）：`tolerance = 0.25` 时三锚区首尾相接 ⇒ 规则退化为「只要在带内即可开单」，
# 故入参上界锁死于此，不接受更大值（越界在 schema 层即 422）。
MAX_BAND_TOLERANCE = 0.25
# 受铁律约束的动作（豁免矩阵冻结，§2.3）：只有这两个会被拦。
_GATED_ACTIONS = ("open_long", "open_short")
# 方向性模式（P2-1，默认关）下各动作允许的锚区。
_DIRECTIONAL_ZONES = {"open_long": ("lower", "mid"), "open_short": ("upper", "mid")}
# 边界含（`|%B−anchor| == tolerance` 判为合规）的浮点松弛。
_EDGE_EPS = 1e-9

_ZONE_LABEL = {"lower": "下轨", "mid": "中轨", "upper": "上轨"}


@dataclass(frozen=True)
class MaBandRule:
    """MA 通道开单约束的会话级不可变配置（随 `AiTradeSession` 生命周期，§5.2）。

    Attributes:
        enabled: 是否装配本规则（`False` 等价于不装配）。
        period: 中轨 SMA 周期。
        std_mult: 上下轨 = 中轨 ± `std_mult` × σ。
        tolerance: 「附近」的判据——%B 距最近锚点的容差，∈ (0, `MAX_BAND_TOLERANCE`]。
        directional: `True` 时做多只许 lower/mid、做空只许 upper/mid（P2-1，默认关）。
    """

    enabled: bool = True
    period: int = 20
    std_mult: float = 2.0
    tolerance: float = 0.15
    directional: bool = False


def _fallback(value, default):
    """入参缺省（None）时回落 settings 默认；显式值（含 0/False）原样保留。"""
    return default if value is None else value


def resolve_ma_band_rule(raw: dict | None, settings: Settings) -> MaBandRule | None:
    """把请求里的 `maBandEntry` 子对象解析为规则（**默认开启的唯一解析点**，§2.10/P1-5）。

    三层语义：运维总闸关 ⇒ 恒 `None`（逐字回退到本特性合入前）；字段缺省 ⇒ 按 settings 装配开启态
    （老客户端不发该字段也默认开启）；显式 `enabled=false` ⇒ `None`（该会话不装配）。

    Args:
        raw: 请求中的 `baseRules.maBandEntry`（缺省为 None）；越界入参已在 schema 层 422。
        settings: 运行配置（提供总闸与四个默认值）。

    Returns:
        装配后的 `MaBandRule`；总闸关闭或用户显式关闭时为 `None`（调用方据此整条链路不装配）。
    """
    if not settings.ai_trade_base_rules_enabled:
        return None
    raw = raw or {}
    if raw.get("enabled") is False:
        return None
    return MaBandRule(
        enabled=True,
        period=int(_fallback(raw.get("period"), settings.ai_trade_base_rule_ma_period)),
        std_mult=float(_fallback(raw.get("stdMult"),
                                 settings.ai_trade_base_rule_ma_std_mult)),
        tolerance=float(_fallback(raw.get("tolerance"),
                                  settings.ai_trade_base_rule_band_tolerance)),
        directional=bool(_fallback(raw.get("directional"),
                                   settings.ai_trade_base_rule_directional)))


def rule_wire(rule: MaBandRule | None) -> dict | None:
    """规则 → camelCase 回执镜像（`{"maBandEntry": {...}}`）；未装配 → None。"""
    if rule is None or not rule.enabled:
        return None
    return {"maBandEntry": {"enabled": True, "period": rule.period,
                            "stdMult": rule.std_mult, "tolerance": rule.tolerance,
                            "directional": rule.directional}}


def build_ma_band(df: pd.DataFrame, rule: MaBandRule | None) -> dict | None:
    """按执行周期收盘价算 MA 通道（= 布林通道，`bollinger` 第二返回值即中轨 SMA，A-9）。

    选布林而非「高/低价 MA 包络」的理由：需求的「上下 MA 均线边界 + MA 中线」正是这个形状；仓库已有实现
    且已被 `market_digest.bollPctB` 用于喂 LLM（复用即口径统一、零新依赖）；带宽随波动自适应，使同一条
    规则在 1m 与 1D 上都成立。

    Args:
        df: 执行周期窗口（`candles_to_df` 产物，列 ts/o/h/l/c/vol，升序，**末行=当前确认根**，
            绝不含未来根）。
        rule: 已装配规则；`None`/`enabled=False` → 直接 `None`。

    Returns:
        §2.2 wire 形状的通道快照（camelCase，直接进 payload / 轨迹 / prompt）；数据不足
        （`len(df) < period + 1`）、带宽非正、非有限数或计算异常 → `None`（走 §2.6 fail-closed）。
    """
    if rule is None or not rule.enabled:
        return None
    if df is None or len(df) < rule.period + 1:
        return None
    try:
        upper_s, mid_s, lower_s = bollinger(df["c"], rule.period, rule.std_mult)
        upper, mid = float(upper_s.iloc[-1]), float(mid_s.iloc[-1])
        lower, last = float(lower_s.iloc[-1]), float(df["c"].iloc[-1])
    except Exception as exc:  # noqa: BLE001 —— 通道算不出即 fail-closed，绝不因证据计算崩掉决策轮
        logger.warning("ma band build failed (period=%s): %s", rule.period, exc)
        return None
    span = upper - lower
    if not all(math.isfinite(v) for v in (upper, mid, lower, last, span)):
        return None
    if span <= 0 or mid <= 0:
        return None
    pct_b = (last - lower) / span
    return {"ruleId": MA_BAND_RULE_ID, "period": rule.period, "stdMult": rule.std_mult,
            "tolerance": rule.tolerance,
            "upper": round(upper, 6), "mid": round(mid, 6), "lower": round(lower, 6),
            "bandWidthPct": round(span / mid * 100.0, 4),
            "pctB": round(pct_b, 4), "zone": classify_zone(pct_b, rule.tolerance),
            "directional": rule.directional}


def classify_zone(pct_b: float | None, tolerance: float) -> str | None:
    """把 %B 归类到最近的锚区（`lower`/`mid`/`upper`）；超容差 → `None`（不在任何锚区）。

    判据 `min(|%B−0|, |%B−0.5|, |%B−1|) ≤ tolerance` 是**无量纲**的，同一容差在任何周期 / 波动率下语义
    一致（P1-8）；它天然覆盖「追高杀跌」——跑到带外（%B > 1+tol 或 < −tol）距最近锚点必然超容差。
    边界**含**（`== tolerance` 判为合规）。
    """
    if pct_b is None or not math.isfinite(pct_b) or tolerance <= 0:
        return None
    name, dist = min(((n, abs(pct_b - a)) for n, a in _ANCHORS), key=lambda item: item[1])
    return name if dist <= tolerance + _EDGE_EPS else None


def entry_reference_px(decision: TradeDecision, last_px: float | None) -> float | None:
    """铁律的判定价：限价委托判**委托价**，市价判最新价（§2.4/P0-3，单点收敛，禁止别处各判一次）。

    这是本特性最容易漏掉、且漏掉即**规则整体失效**的一点：限价总闸默认开启，Agent 可在最新价 ±2% 内
    任选委托价并通过既有校验，而真实成交发生在委托价上。
    """
    if decision.ord_type == "limit" and decision.limit_px:
        return float(decision.limit_px)
    return None if last_px is None else float(last_px)


def band_pct_b(band: dict | None, px: float | None) -> float | None:
    """把任意价格归一到通道 %B（`(px − lower) / (upper − lower)`）；通道缺失/带宽非正 → `None`。"""
    if not band or px is None:
        return None
    lower, upper = band.get("lower"), band.get("upper")
    if lower is None or upper is None:
        return None
    span = float(upper) - float(lower)
    if not math.isfinite(span) or span <= 0:
        return None
    return (float(px) - float(lower)) / span


def apply_ma_band_gate(decision: TradeDecision, band: dict | None,
                       rule: MaBandRule | None, *,
                       last_px: float | None = None) -> tuple[TradeDecision, str | None]:
    """MA 通道开单门禁：参考成交价不在任一锚区 ⇒ 本轮开仓硬降级为 hold（独立于 LLM，仅更保守）。

    未装配（`rule is None` / `enabled=False`）或动作在豁免矩阵内时返回**同一个** decision 对象
    （`is` 相等，§六 用例 9），绝不顺手 `replace`。

    Args:
        decision: 决策器本轮产出（已过安全回落与限价价带门禁）。
        band: `build_ma_band` 产物；`None` 表示通道不可用（fail-closed，§2.6）。
        rule: 已装配规则；`None` 即不装配 ⇒ 恒直通。
        last_px: 本轮最新价（keyword-only）；限价委托改判 `decision.limit_px`（§2.4）。

    Returns:
        `(decision, gate_reason)`——`gate_reason` 非 None 表示本轮被基础策略降级为 hold，取值见 §2.3
        降级码表；未降级则 `gate_reason is None`。
    """
    if rule is None or not rule.enabled or decision.action not in _GATED_ACTIONS:
        return decision, None
    pct_b = band_pct_b(band, entry_reference_px(decision, last_px))
    if pct_b is None:                       # 通道不可用 / 无判定价 → fail-closed（§2.6/P0-4）
        return _veto("base_rule_band_unavailable")
    zone = classify_zone(pct_b, rule.tolerance)
    if zone is None:
        return _veto(f"base_rule_ma_band(pctB={pct_b:.2f})")
    if rule.directional and zone not in _DIRECTIONAL_ZONES[decision.action]:
        return _veto(f"base_rule_ma_band_direction(zone={zone})")
    return decision, None


def _veto(reason: str) -> tuple[TradeDecision, str]:
    """被铁律否决：整个决策（含反向开仓的 close+open 两腿）不发生，本轮降级为 hold。"""
    return TradeDecision(action="hold", size_fraction=None, confidence=None,
                         reason=reason), reason


def gate_status(band: dict | None, gate_reason: str | None) -> str:
    """轨迹段状态（§2.9）：通道不可用 → `unavailable`；被否 → `veto`；其余 → `pass`。"""
    if band is None:
        return "unavailable"
    return "veto" if gate_reason else "pass"


def compliant_price_ranges(band: dict | None) -> list[tuple[str, float, float]]:
    """由同一个 band 反解出三段**合规开单价区间**（单一真源；band 缺失 → `[]`）。

    给出可直接使用的价区间而不只是「是 / 否」，是让 Agent 能主动挂出合规限价单的关键——否则它只能靠猜价
    然后被否，白白浪费轮次（§2.7 (b)）。
    """
    if not band:
        return []
    lower, upper = float(band["lower"]), float(band["upper"])
    span = upper - lower
    tol = float(band.get("tolerance") or 0.0)
    if span <= 0 or tol <= 0:
        return []
    return [(name, lower + (anchor - tol) * span, lower + (anchor + tol) * span)
            for name, anchor in _ANCHORS]


def format_band_for_prompt(band: dict | None) -> str:
    """决策 prompt 的每轮只读上下文段（§2.7 (b)）；`band is None` 时如实告知数据不足。

    调用方只在 `payload["baseRules"]` **存在**时才渲染本段——键缺失 ⇒ 整段省略 ⇒ 既有冻结断言与回测
    payload 逐字节不变。
    """
    head = "【基础策略 · MA 通道开单约束（铁律）】"
    if not band:
        return (f"{head}\n"
                "  通道数据暂不可用（K 线不足或带宽为零）：本轮**开仓会被系统直接拒绝并降级为观望**；"
                "平仓 close 与撤单 cancel_order 不受影响。")
    zone = band.get("zone")
    tol = band.get("tolerance")
    pos = (f"{_ZONE_LABEL.get(zone, zone)}锚区内（容差 {tol}）✔ 允许开仓" if zone
           else f"不在任何锚区内（容差 {tol}）✘ 本轮开仓将被系统降级为观望")
    ranges = " / ".join(f"{lo:.2f}~{hi:.2f}（{_ZONE_LABEL[name]}）"
                        for name, lo, hi in compliant_price_ranges(band))
    return (f"{head}\n"
            f"  通道(MA{band.get('period')}±{band.get('stdMult')}σ)："
            f"上轨 {band.get('upper'):.2f} / 中轨 {band.get('mid'):.2f} / "
            f"下轨 {band.get('lower'):.2f}（带宽 {band.get('bandWidthPct')}%）\n"
            f"  当前价位置：%B={band.get('pctB')} → {pos}\n"
            f"  合规开单价区间：{ranges}")


def format_charter(rule: MaBandRule | None) -> str:
    """系统 prompt 的铁律章（§2.7 (a)）；未装配 → **空串垫片**（渲染逐字节不变、零回归，P1-3）。

    软引导层：让 Agent 知道并主动遵守（否则它会反复撞墙、浪费轮次）；硬保证仍是 `apply_ma_band_gate`。
    """
    if rule is None or not rule.enabled:
        return ""
    direction = ("；本会话另启用**方向性**限制：做多只许在下轨 / 中轨锚区，做空只许在上轨 / 中轨锚区"
                 if rule.directional else "")
    return (
        "**最高优先级铁律（不可违反 · 由系统强制执行）**：本会话启用了基础策略「MA 通道开单约束」。"
        f"**开仓（open_long / open_short）只允许在执行周期 MA 通道（MA{rule.period}±{rule.std_mult:g}σ）"
        "的上轨、中轨或下轨附近发生**（容差以带宽归一，见每轮输入的 `基础策略` 段）。不在锚区时提交的"
        "开仓会被系统直接降级为观望——这不是建议，是硬约束。请在研判中主动检查当前位置：不满足就选择 "
        "`hold` 等待价格回到锚区，或改用 `limit` 把委托价挂在锚区内（每轮输入已给出合规价区间）。"
        f"**平仓 `close`、撤单 `cancel_order` 不受本铁律限制**——需要离场时永远可以直接 `close`{direction}。"
    )
