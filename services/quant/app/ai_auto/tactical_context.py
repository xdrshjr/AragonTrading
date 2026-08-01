"""战术周期证据（ai-trade-wake-aware-context 轴 A，纯计算、严格因果）。

三层时间尺度里缺失的最后一层：

    中线背景（multi_timeframe_context）   1D  12H  4H  1H     ← 严格高于执行周期
    执行周期（market_context/digest）            30m           ← 用户所选 bar
    战术周期（本模块）                       5m   1m           ← 严格低于执行周期，贴唤醒节奏

诊断根因：30m 执行 + 3 分钟唤醒下，一根 K 线期间 AI 被叫醒约 10 次，其中 9 次拿到的信号板 / 体制 /
证据摘要 / 中线背景**逐字节相同**，唯一变化的是 `lastPx` 一个数——模型被要求对同一份证据反复表态，
只能反复输出 hold，而这些 hold 又进摘要被它自己当成「观望已被反复验证」的独立证据。战术周期证据正是
重复轮里唯一真实变化的那部分信息。

指标真源与既有两层**完全同源**（`build_market_context` + `build_market_digest`），因果裁剪逐字复用
`multi_timeframe_context._causal_frame`——同一个量绝不在两处各算一次。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import pandas as pd

from ..config import Settings
from ..okx.types import BAR_SECONDS, BAR_TO_WS_CHANNEL
from .market_context import build_market_context
from .market_digest import build_market_digest
# 因果裁剪的**唯一真源**：复制一份等于两处各算一次，一旦某处口径漂移就会出现「窗口在看 A、
# prompt 却在讲 B」这类无声失真。跨模块引用私有名是刻意选择（同源 > 封装洁癖），且完全不改动
# multi_timeframe_context 自身的行为（G-1 冻结基线不受影响）。
from .multi_timeframe_context import _causal_frame

# 可 REST 取数的战术候选梯子（升序）。含 12H：它在 BAR_SECONDS 里、既有 MTF policy 也在用它、
# REST 也能取；缺它会让 1D 执行周期从 4H 直接跳到 1D。刻意**不含** 1D 及以上——战术层的定义就是
# 「严格短于执行周期」。
_TACTICAL_LADDER = ("1m", "5m", "15m", "30m", "1H", "4H", "12H")

# 升档集合：这些唤醒原因下本轮「有料」，战术段值得展开成每周期两行。
# `signal_flip` 在列：翻转轮是「立场可能正在改变」的时刻，值得看全量战术明细。
# `_EXECUTION_FULL_KINDS` 刻意**不增**——执行周期证据自上一轮起没变，压缩档仍正确。
_FULL_TIER_KINDS = frozenset(
    {"candle_close", "bar_advanced", "signal_flip", "price_breakout", "profit_tier",
     "resting_order"})
# 执行周期段全量披露的唤醒原因：只有「这根 K 线第一次被研判」时才值得重复整段中线背景与证据摘要。
_EXECUTION_FULL_KINDS = frozenset({"candle_close", "bar_advanced"})
_EXTREME_RSI_LABELS = frozenset({"overbought", "oversold"})


@dataclass(frozen=True)
class TacticalInputs:
    """一次决策瞬刻的战术段输入（聚合结构体，守 CLAUDE.md「形参 ≤5」）。

    `as_of_ts` **必须**是会话的 `_decision_as_of_ts` 产物（墙钟口径的决策瞬刻）：K 线轮 = 该根收盘
    时刻、定时轮 = 墙钟当前时刻。误用既有 MTF 的 `ts + BAR_SECONDS[bar]` 会让定时轮的每个战术周期
    都被判成「数据陈旧」，而系统 prompt 决策纪律 #2 明写「陈旧或缺失周期不得补猜」——等于把新取回来
    的证据亲手命令模型忽略。
    """

    execution_bar: str
    tactical_bars: tuple[str, ...]
    as_of_ts: int
    last_px: float
    execution_primary: str | None = None
    # 上一决策轮断面 `{"px","asOfTs","atrPx"}`；None = 首轮无参照。`asOfTs` 与本轮 `as_of_ts`
    # **同一时钟**（不是 time.time()）——混钟会让 K 线轮/定时轮交替时 closedBars 系统性偏一根。
    reference: dict | None = field(default=None)


def tactical_timeframes(execution_bar: str, wake_interval_s: float,
                        max_bars: int = 2) -> tuple[str, ...]:
    """按「最贴近唤醒节奏」选战术周期，升序返回，全部严格短于执行周期。

    判定口径：`coarse` = 第一个**不短于**唤醒周期的候选（用户说的「时间维度最接近」），再向下取
    `max_bars-1` 个更细周期；无候选满足时退到最粗候选。30m 执行 + 3 分钟唤醒 ⇒ `("1m","5m")`，
    与用户原话逐字一致。

    Args:
        execution_bar: 用户所选执行周期（须是合法 OKX 周期）。
        wake_interval_s: 墙钟定时唤醒周期（秒）；`<=0`（关闭定时唤醒）时按执行周期本身取。
        max_bars: 注入的战术周期数上限；`<=0` 等价关闭。

    Returns:
        升序周期元组；执行周期已是最细（1m）时返回 `()`（整段天然关闭）。

    Raises:
        ValueError: `execution_bar` 不是受支持的 OKX 周期。
    """
    if execution_bar not in BAR_TO_WS_CHANNEL:
        raise ValueError(f"unsupported AI execution bar: {execution_bar}")
    if max_bars <= 0:
        return ()
    exec_s = BAR_SECONDS[execution_bar]
    candidates = [b for b in _TACTICAL_LADDER if BAR_SECONDS[b] < exec_s]
    if not candidates:
        return ()
    wake_s = wake_interval_s if wake_interval_s > 0 else exec_s
    coarse = next((i for i, b in enumerate(candidates) if BAR_SECONDS[b] >= wake_s),
                  len(candidates) - 1)
    return tuple(candidates[max(0, coarse - (max_bars - 1)): coarse + 1])


def build_tactical_context(inputs: TacticalInputs, *, frames: dict[str, pd.DataFrame],
                           settings: Settings) -> dict | None:
    """装配本决策瞬刻的战术周期证据；总闸关闭 / 无候选周期 / 全周期不可用 ⇒ None（整段省略）。

    Args:
        inputs: 决策瞬刻输入（执行周期、战术周期集合、`as_of_ts`、最新价、上一轮参照）。
        frames: 各战术周期的原始 OHLCV 窗口拷贝（`MarketWindow.tactical_frames()` 产物）。
        settings: 运行配置（`ai_trade_tactical_context` 总闸 + 各指标层自身的总闸）。

    Returns:
        战术证据 wire（camelCase），`frames` 由粗到细排序，与 prompt / 前端同序；不可用时 None。

    Raises:
        ValueError: `as_of_ts` 非 int 或 `last_px` 非正有限数（与 MTF 同款契约校验）。
    """
    if type(inputs.as_of_ts) is not int:
        raise ValueError("as_of_ts must be an integer timestamp")
    if not math.isfinite(inputs.last_px) or inputs.last_px <= 0:
        raise ValueError("last_px must be positive and finite")
    if not settings.ai_trade_tactical_context or not inputs.tactical_bars:
        return None

    output_frames: list[dict] = []
    missing_bars: list[str] = []
    stale_bars: list[str] = []
    for bar in reversed(inputs.tactical_bars):          # 由粗到细，与中线背景阅读方向一致
        item = _build_frame(bar, frames.get(bar), inputs, settings)
        if item is None:
            missing_bars.append(bar)
            continue
        output_frames.append(item)
        if item["stale"]:
            stale_bars.append(bar)
    if not output_frames:
        return None
    return {
        "executionBar": inputs.execution_bar,
        "wakeIntervalS": None,                          # 由 WakeRuntime 回填（文案的节奏描述）
        "asOfTs": inputs.as_of_ts,
        "executionPrimary": inputs.execution_primary,
        "alignment": _alignment(output_frames, inputs.execution_primary),
        "changeSinceLastRound": _change_since_last_round(inputs),
        "frames": output_frames,
        "missingBars": missing_bars,
        "staleBars": stale_bars,
        # 渲染字符上限随证据一同下发：渲染层（prompts/wake_text）从此只吃 payload，不必拿 Settings，
        # 与本仓库其余 `_format_*` 渲染函数的既有形态一致。
        "maxChars": settings.ai_trade_tactical_max_chars,
    }


def disclosure_tier(*, wake_kind: str, position_side: str, context: dict) -> str:
    """战术段披露档：`"full"`（每周期两行）当且仅当本轮确实「有料」，否则 `"brief"`（每周期一行）。

    四条判据任一成立即升档：① 唤醒原因在升档集合内；② 持仓中（真金白银在场，多看一眼的钱值得花）；
    ③ 战术与执行周期体制背离（最需要展开的一种局面）；④ 任一战术周期出现超买/超卖或异常放量。
    即：只有「空仓 + 心跳 + 同向 + 无极端读数」这种确实没料的轮次才走 `brief`。

    注：判据 ③ 依赖 `regime`，而它受**另一个**总闸 `ai_trade_regime_context` 管制——该闸关闭时
    `alignment` 恒 `"insufficient"`、本条恒假。这是可接受的降级（其余三条仍生效），不是缺陷。
    """
    if wake_kind in _FULL_TIER_KINDS or position_side != "flat":
        return "full"
    if (context or {}).get("alignment") == "diverged":
        return "full"
    return "full" if _has_extreme_reading(context) else "brief"


def execution_disclosure(*, wake_kind: str, enabled: bool) -> str:
    """执行周期段（中线背景 + 证据摘要）的披露档：`"full"` 或 `"repeat"`（压缩形态）。

    语义：执行周期的证据只在「这根 K 线第一次被研判」时全量披露；同一根的后续唤醒一律压缩。
    `enabled=False`（渐进式披露总闸关）⇒ 恒 `"full"`，渲染逐字节回退。
    """
    if not enabled or wake_kind in _EXECUTION_FULL_KINDS:
        return "full"
    return "repeat"


def _build_frame(bar: str, frame: pd.DataFrame | None, inputs: TacticalInputs,
                 settings: Settings) -> dict | None:
    """单个战术周期的因果建帧 + 指标装配；异常 / 空窗一律 None（整周期省略，绝不拖垮决策轮）。"""
    try:
        causal = _causal_frame(frame, bar, inputs.as_of_ts)
        if causal.empty:
            return None
        regime = build_market_context(causal, bar, settings)
        digest = build_market_digest(causal, bar, settings)
        if regime is None and digest is None:
            return None
        close_ts = int(causal.iloc[-1]["ts"]) + BAR_SECONDS[bar]
        age_seconds = max(0, inputs.as_of_ts - close_ts)
        return {
            "bar": bar,
            "sampleCount": len(causal),
            "closeTs": close_ts,
            "ageSeconds": age_seconds,
            "stale": age_seconds >= 2 * BAR_SECONDS[bar],
            "regime": regime,
            "digest": digest,
            "closedBars": _closed_bars(causal, bar, inputs.reference),
        }
    except Exception:  # noqa: BLE001 —— 一个坏周期绝不阻塞决策轮（与 MTF 同姿态）
        return None


def _closed_bars(causal: pd.DataFrame, bar: str, reference: dict | None) -> int | None:
    """上一决策轮之后该级别**收线**的根数；无参照（首轮）⇒ None。

    判据用**收线时刻** `ts + BAR_SECONDS[bar] > reference["asOfTs"]` 而非开盘 `ts >= ...`：后者会漏掉
    「上轮唤醒时正在走、随后立刻收线」的那一根，在 1m 级别恰好是最有信息量的一根。
    """
    if not reference:
        return None
    ref_as_of = reference.get("asOfTs")
    if not isinstance(ref_as_of, (int, float)):
        return None
    return int((causal["ts"] + BAR_SECONDS[bar] > ref_as_of).sum())


def _change_since_last_round(inputs: TacticalInputs) -> dict | None:
    """自上一决策轮的价格变动（**与周期无关**，故挂在顶层，不逐周期重复渲染同一个数）。

    `atrBar` 显式声明这个 ATR 的量纲周期是**执行周期**（`_atr_px` 取自执行周期窗口）——文案必须写成
    「≈0.91×30m ATR」而非裸「≈0.91×ATR」，否则模型会误读为战术周期自身的 ATR。
    """
    reference = inputs.reference
    if not reference:
        return None
    ref_px = reference.get("px")
    if not isinstance(ref_px, (int, float)) or ref_px <= 0:
        return None
    atr_px = reference.get("atrPx")
    atr_mult = None
    if isinstance(atr_px, (int, float)) and atr_px > 0:
        atr_mult = round(abs(inputs.last_px - ref_px) / atr_px, 6)
    return {"pct": round((inputs.last_px / ref_px - 1.0) * 100.0, 6),
            "atrMult": atr_mult, "atrBar": inputs.execution_bar}


def _alignment(frames: list[dict], execution_primary: str | None) -> str:
    """战术周期体制与执行周期体制的关系：aligned / diverged / insufficient。"""
    primaries = [p for p in ((item.get("regime") or {}).get("primary") for item in frames)
                 if p is not None]
    if execution_primary is None or not primaries:
        return "insufficient"
    return "aligned" if all(p == execution_primary for p in primaries) else "diverged"


def _has_extreme_reading(context: dict | None) -> bool:
    """任一战术周期出现 RSI 超买/超卖或异常放量（升档判据 ④）。"""
    for item in (context or {}).get("frames") or []:
        digest = item.get("digest") or {}
        rsi = (digest.get("factors") or {}).get("rsi14") or {}
        if rsi.get("label") in _EXTREME_RSI_LABELS:
            return True
        if (digest.get("volume") or {}).get("spike"):
            return True
    return False
