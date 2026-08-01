"""战术周期证据单测（ai-trade-wake-aware-context §6.1 T-1/T-2/T-8/T-19）。

覆盖：选取表 10 行逐行反算、因果裁剪、**决策瞬刻双钟（T-19，P0-1）**、两个披露分档、安全降级为
None、`changeSinceLastRound` / `closedBars` 反算。全部为纯函数，无 IO、无 DB。
"""
from __future__ import annotations

import pandas as pd
import pytest

from app.ai_auto.tactical_context import (
    TacticalInputs,
    build_tactical_context,
    disclosure_tier,
    execution_disclosure,
    tactical_timeframes,
)
from app.config import Settings
from app.okx.types import BAR_SECONDS

_WALL = 1_700_000_000            # 决策瞬刻（墙钟），同时也是某个 30m 根的收盘时刻
_EXEC_BAR = "30m"


def _frame(bar: str, *, last_close_ts: int, rows: int = 120,
           base: float = 30000.0) -> pd.DataFrame:
    """构造一段升序 OHLCV：末根的**收线时刻**恰为 `last_close_ts`。"""
    secs = BAR_SECONDS[bar]
    last_open = last_close_ts - secs
    data = []
    for index in range(rows):
        ts = last_open - (rows - 1 - index) * secs
        close = base + index * 2.0
        data.append({"ts": ts, "o": close - 1.0, "h": close + 3.0, "l": close - 3.0,
                     "c": close, "vol": 100.0 + index})
    return pd.DataFrame(data)


def _build(as_of_ts: int, *, bars: tuple[str, ...] = ("1m",), last_close_ts: int | None = None,
           reference: dict | None = None, settings: Settings | None = None) -> dict | None:
    frames = {bar: _frame(bar, last_close_ts=last_close_ts or (_WALL - 42)) for bar in bars}
    return build_tactical_context(
        TacticalInputs(execution_bar=_EXEC_BAR, tactical_bars=bars, as_of_ts=as_of_ts,
                       last_px=30200.0, execution_primary="trend_up", reference=reference),
        frames=frames, settings=settings or Settings())


# ---------- T-1：选取表 10 行 ----------
@pytest.mark.parametrize(("execution_bar", "wake_s", "expected"), [
    ("30m", 180, ("1m", "5m")),        # 用户原话用例：30 分钟自动交易 + 3 分钟唤醒 ⇒ 1m/5m
    ("15m", 120, ("1m", "5m")),
    ("1H", 300, ("1m", "5m")),
    ("30m", 1200, ("5m", "15m")),      # 慢唤醒 ⇒ 战术周期同步变粗
    ("30m", 0, ("5m", "15m")),         # 关定时唤醒 ⇒ 退最粗，仍给根内结构
    ("5m", 180, ("1m",)),
    ("1m", 180, ()),                   # 已是最细 ⇒ 天然关闭，整段省略
    ("4H", 120, ("1m", "5m")),         # 4H 是合法可选执行周期（启动弹窗 BARS 含它）
    ("1D", 120, ("1m", "5m")),
    ("1D", 3600, ("30m", "1H")),
])
def test_tactical_timeframes_selection_table(execution_bar, wake_s, expected):
    assert tactical_timeframes(execution_bar, wake_s) == expected


def test_tactical_timeframes_rejects_unknown_bar_and_zero_max_bars():
    with pytest.raises(ValueError):
        tactical_timeframes("7m", 180)
    assert tactical_timeframes("30m", 180, max_bars=0) == ()


# ---------- T-19（P0-1）：决策瞬刻双钟 ----------
def test_decision_instant_is_one_clock_for_both_wake_paths():
    """同一份 1m 窗口（末根 42 秒前收线）走 K 线轮与定时轮：两轮都**不得**被判为数据陈旧。

    K 线轮的 `ts` 是根**开盘**（`_WALL - 1800`），定时轮的 `ts` 是**墙钟**（`_WALL`）——两者语义
    不同，但经 `_decision_as_of_ts` 折算后必须落到同一个决策瞬刻 `_WALL`。
    """
    candle_as_of = (_WALL - BAR_SECONDS[_EXEC_BAR]) + BAR_SECONDS[_EXEC_BAR]   # K 线轮：根开盘 + 一根
    interval_as_of = _WALL                                                     # 定时轮：ts 本身即墙钟
    assert candle_as_of == interval_as_of == _WALL

    for as_of in (candle_as_of, interval_as_of):
        context = _build(as_of)
        assert context is not None
        assert [f["ageSeconds"] for f in context["frames"]] == [42]
        assert all(f["stale"] is False for f in context["frames"])
        assert context["staleBars"] == []


def test_reusing_the_mtf_expression_on_interval_rounds_marks_every_frame_stale():
    """**反例**（把这次的坑变成未来的护栏）：定时轮若误用 `ts + BAR_SECONDS[bar]` 作 as_of，

    `ageSeconds` 会凭空多出一整根执行周期，而 1m 的 stale 阈值只有 120 秒 ⇒ 每个战术周期恒被判
    「数据陈旧」，而系统 prompt 决策纪律 #2 明写「陈旧或缺失周期不得补猜」。
    """
    wrong_as_of = _WALL + BAR_SECONDS[_EXEC_BAR]
    context = _build(wrong_as_of)
    assert context is not None
    assert context["frames"][0]["ageSeconds"] == 42 + BAR_SECONDS[_EXEC_BAR]
    assert context["frames"][0]["stale"] is True
    assert context["staleBars"] == ["1m"]


# ---------- T-2：因果裁剪 ----------
def test_future_bars_never_enter_any_indicator():
    """构造含未来根的帧：`ts + BAR_SECONDS[bar] > as_of_ts` 的根一律被裁掉（严格因果）。"""
    frame = _frame("1m", last_close_ts=_WALL - 42, rows=120)
    future = frame.copy()
    for step in range(1, 6):                      # 追加 5 根「未来」1m 根
        future.loc[len(future)] = {"ts": _WALL - 42 + step * 60, "o": 1.0, "h": 1.0,
                                   "l": 1.0, "c": 1.0, "vol": 1.0}
    context = build_tactical_context(
        TacticalInputs(execution_bar=_EXEC_BAR, tactical_bars=("1m",), as_of_ts=_WALL,
                       last_px=30200.0, execution_primary="trend_up"),
        frames={"1m": future}, settings=Settings())
    assert context is not None
    assert context["frames"][0]["sampleCount"] == 120         # 5 根未来根未入指标
    assert context["frames"][0]["closeTs"] == _WALL - 42


# ---------- changeSinceLastRound / closedBars 反算 ----------
def test_change_since_last_round_is_top_level_and_declares_its_atr_bar():
    reference = {"px": 30000.0, "asOfTs": _WALL - 180, "atrPx": 100.0}
    context = _build(_WALL, reference=reference)
    change = context["changeSinceLastRound"]
    assert change["pct"] == pytest.approx((30200.0 / 30000.0 - 1) * 100)
    assert change["atrMult"] == pytest.approx(200.0 / 100.0)
    assert change["atrBar"] == _EXEC_BAR                       # ATR 的量纲周期是**执行周期**
    assert "pct" not in context["frames"][0]                   # 与周期无关的量绝不逐周期重复


def test_closed_bars_counts_by_close_time_not_open_time():
    """上一轮 as_of 之后**收线**的根数：按收线时刻计，不会漏掉「上轮正在走、随后立刻收线」的那根。"""
    reference = {"px": 30000.0, "asOfTs": _WALL - 180, "atrPx": None}
    context = _build(_WALL, reference=reference)
    assert context["frames"][0]["closedBars"] == 3             # 180 秒内 1m 收了 3 根
    assert context["changeSinceLastRound"]["atrMult"] is None  # ATR 缺失 ⇒ 绝不假数据


def test_no_reference_on_first_round_leaves_change_and_closed_bars_empty():
    context = _build(_WALL)
    assert context["changeSinceLastRound"] is None
    assert context["frames"][0]["closedBars"] is None


# ---------- 降级 ----------
def test_switch_off_or_no_candidates_or_all_unavailable_returns_none():
    assert _build(_WALL, settings=Settings(ai_trade_tactical_context=False)) is None
    assert build_tactical_context(
        TacticalInputs(execution_bar=_EXEC_BAR, tactical_bars=(), as_of_ts=_WALL, last_px=1.0),
        frames={}, settings=Settings()) is None
    assert build_tactical_context(          # 窗口全空 ⇒ 全周期不可用 ⇒ 整段省略
        TacticalInputs(execution_bar=_EXEC_BAR, tactical_bars=("1m",), as_of_ts=_WALL,
                       last_px=30200.0),
        frames={}, settings=Settings()) is None


def test_contract_violations_raise_like_the_multi_timeframe_layer():
    with pytest.raises(ValueError):
        _build(float(_WALL))               # type: ignore[arg-type]
    with pytest.raises(ValueError):
        build_tactical_context(
            TacticalInputs(execution_bar=_EXEC_BAR, tactical_bars=("1m",), as_of_ts=_WALL,
                           last_px=0.0), frames={}, settings=Settings())


# ---------- 顺序 / 体制关系 ----------
def test_frames_are_ordered_coarse_to_fine_and_alignment_reads_execution_primary():
    context = _build(_WALL, bars=("1m", "5m"))
    assert [f["bar"] for f in context["frames"]] == ["5m", "1m"]     # 由粗到细，与 prompt 同序
    assert context["executionPrimary"] == "trend_up"
    assert context["alignment"] in {"aligned", "diverged", "insufficient"}


def test_regime_switch_off_degrades_alignment_to_insufficient_not_a_bug():
    """`ai_trade_regime_context=False` ⇒ 战术帧无 regime ⇒ alignment 恒 insufficient（可接受降级）。"""
    context = _build(_WALL, settings=Settings(ai_trade_regime_context=False))
    assert context is not None
    assert context["frames"][0]["regime"] is None
    assert context["alignment"] == "insufficient"
    assert context["frames"][0]["digest"] is not None                # 其余证据仍全量生效


# ---------- T-8：披露分档 ----------
_QUIET = {"alignment": "aligned", "frames": [{"bar": "1m", "digest": {
    "factors": {"rsi14": {"label": "neutral"}}, "volume": {"spike": False}}}]}


def test_disclosure_tier_is_brief_only_when_the_round_really_has_nothing():
    assert disclosure_tier(wake_kind="heartbeat", position_side="flat", context=_QUIET) == "brief"


@pytest.mark.parametrize(("kind", "side", "context"), [
    ("price_breakout", "flat", _QUIET),                                   # ① 唤醒原因在升档集合
    ("signal_flip", "flat", _QUIET),                                      # ①b 立场翻转轮同样升档
    ("heartbeat", "long", _QUIET),                                        # ② 持仓中
    ("heartbeat", "flat", {**_QUIET, "alignment": "diverged"}),           # ③ 体制背离
    ("heartbeat", "flat", {"alignment": "aligned", "frames": [{"digest": {
        "factors": {"rsi14": {"label": "overbought"}}}}]}),               # ④ 极端读数
])
def test_disclosure_tier_upgrades_on_any_single_condition(kind, side, context):
    assert disclosure_tier(wake_kind=kind, position_side=side, context=context) == "full"


def test_execution_disclosure_compresses_every_repeat_round_of_the_same_bar():
    assert execution_disclosure(wake_kind="candle_close", enabled=True) == "full"
    assert execution_disclosure(wake_kind="bar_advanced", enabled=True) == "full"
    assert execution_disclosure(wake_kind="heartbeat", enabled=True) == "repeat"
    assert execution_disclosure(wake_kind="price_breakout", enabled=True) == "repeat"
    # `signal_flip` 刻意**不进**执行周期全量披露集合：翻转是未确认根上的预演结论，执行周期的
    # 证据自上一根确认根起没变，压缩档仍正确（本轮最新变化由战术周期承载）。
    assert execution_disclosure(wake_kind="signal_flip", enabled=True) == "repeat"
    # 总闸关闭 ⇒ 恒 full（渲染逐字节回退）
    assert execution_disclosure(wake_kind="heartbeat", enabled=False) == "full"
