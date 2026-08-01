"""唤醒 / 战术 / 重复轮压缩文案单测（ai-trade-wake-aware-context §6.1 T-9/T-10/T-11）。

覆盖：八条唤醒文案各含其判定短语、`brief`/`full` 行数、可见截断标记、重复轮压缩、`wake` 缺席 ⇒ 空串，
以及本轮新增的 `signal_flip` 措辞（必含 repaint 警示）与 `_backlog_clause` 的缺席即逐字回退（T-20）。
措辞纪律的三条硬约束也在此钉死：条件化措辞、ATR 倍数必带周期名、阈值取**折算后**的实际门槛。
"""
from __future__ import annotations

import pytest

from app.ai_auto.wake_text import (
    format_digest_repeat,
    format_multi_timeframe_repeat,
    format_tactical_for_prompt,
    format_wake_for_prompt,
)

_BASE_WAKE = {
    "trigger": "interval", "executionBar": "30m", "sinceLastRoundS": 182.0,
    "priceMovePct": 0.42, "priceMoveAtr": 0.91, "moveThresholdAtr": 0.5,
    "moveThresholdPct": 0.0005, "horizonScale": 0.316, "moveThresholdAtrEff": 0.158,
    "skipStreak": 0, "barAdvanced": False, "restingAgeS": None,
    "guardStatus": None, "lastGuardStatus": None, "alsoTrue": [],
}


def _wake(kind: str, **overrides) -> dict:
    return {**_BASE_WAKE, "kind": kind, **overrides}


def _frame(bar: str, **overrides) -> dict:
    return {
        "bar": bar, "sampleCount": 120, "closeTs": 1_700_000_000, "ageSeconds": 42,
        "stale": False, "closedBars": 3,
        "regime": {"primary": "trend_up"},
        "digest": {
            "priceAction": {"trend": "up", "changePct": 0.42, "emaRelation": "fast_above",
                            "emaSlopePct": 0.021, "rangePos": 0.78,
                            "lastBar": {"o": 1.0, "h": 2.0, "l": 0.5, "c": 1.5}},
            "volume": {"ratio": 1.31, "spike": False},
            "factors": {"rsi14": {"value": 58, "label": "neutral"},
                        "macdHist": {"value": 1.42, "label": "bullish"},
                        "bollPctB": {"value": 0.93, "label": "upper_break"},
                        "atrPct": {"value": 0.31, "label": "low"}},
        },
        **overrides,
    }


def _context(**overrides) -> dict:
    return {
        "executionBar": "30m", "wakeIntervalS": 180, "asOfTs": 1_700_000_000,
        "executionPrimary": "trend_up", "alignment": "aligned",
        "changeSinceLastRound": {"pct": 0.42, "atrMult": 0.91, "atrBar": "30m"},
        "frames": [_frame("5m"), _frame("1m")],
        "missingBars": [], "staleBars": [], "maxChars": 700,
        **overrides,
    }


_REPEAT_DIGEST = {
    "priceAction": {"trend": "up"}, "volume": {"ratio": 1.31},
    "factors": {"rsi14": {"value": 58, "label": "neutral"},
                "atrPct": {"value": 0.42, "label": "normal"}},
}


_FLIP = {"netStanceFrom": "flat", "netStanceTo": "long",
         "strategies": [{"strategyId": 7, "name": "EMA 交叉短线", "from": "flat", "to": "long"}],
         "provisional": True, "heldS": 47.3, "samples": 4, "barTs": 1_785_600_000}


# ---------- T-11 / T-20：八条唤醒文案 ----------
@pytest.mark.parametrize(("kind", "must_contain"), [
    ("candle_close", ["新 K 线收盘", "30m 确认根落地", "信号板已按新根重算"]),
    ("bar_advanced", ["补看新 K 线", "首次", "不要把上一轮结论"]),
    ("signal_flip", ["策略立场翻转", "flat→long", "EMA 交叉短线", "0 分 47 秒"]),
    ("resting_order", ["挂单盯守", "未成交限价委托", "cancel_order"]),
    ("profit_tier", ["浮盈跨档", "移动止盈线已激活"]),
    ("price_breakout", ["价格异动", "距上轮 3 分 2 秒", "+0.42%", "0.91×30m ATR"]),
    ("position_watch", ["持仓盯市", "保护", "提前", "5 秒盯市层"]),
    ("heartbeat", ["心跳复盘", "若", "不构成新的独立证据"]),
])
def test_each_wake_kind_carries_its_own_decisive_phrase(kind, must_contain):
    text = format_wake_for_prompt(_wake(kind, restingAgeS=240.0, guardStatus="armed",
                                        lastGuardStatus="idle", flip=_FLIP))
    assert text.startswith("｜唤醒原因=")
    for phrase in must_contain:
        assert phrase in text, f"{kind} 文案缺少关键短语：{phrase}"


def test_threshold_text_uses_the_scaled_effective_gate_not_the_raw_config():
    """写「0.5×ATR」而实际按 0.158 判，就是新造一次「说的和做的不一致」。"""
    text = format_wake_for_prompt(_wake("price_breakout"))
    assert "0.16×30m ATR" in text          # moveThresholdAtrEff=0.158 → 两位小数
    assert "0.50×30m ATR 的唤醒阈值" not in text


def test_threshold_text_falls_back_to_pct_when_atr_is_unavailable():
    text = format_wake_for_prompt(_wake("heartbeat", priceMoveAtr=None))
    assert "0.05%" in text
    assert "ATR" not in text


def test_absent_wake_renders_nothing_so_the_legacy_suffix_takes_over():
    assert format_wake_for_prompt(None) == ""
    assert format_wake_for_prompt({}) == ""
    assert format_wake_for_prompt({"kind": "not_a_kind"}) == ""


def test_also_true_conditions_are_surfaced_and_never_silently_dropped():
    text = format_wake_for_prompt(_wake("resting_order", alsoTrue=["price_breakout"]))
    assert "同时成立=价格异动" in text


def test_signal_flip_text_must_carry_all_three_repaint_anchors():
    """T-20：翻转文案必须同时说清「未确认根」「可能 repaint」「以最近确认根为准」。

    少任何一条，模型就可能把一个**预演**结论当成既成事实——而这正是轴 C 唯一可能造成真实亏损的
    误读路径（结构性隔离已保证它进不了门禁，但措辞不能反过来撤销这条保证）。
    """
    text = format_wake_for_prompt(_wake("signal_flip", flip=_FLIP))
    for anchor in ("未确认根", "repaint", "以最近确认根为准", "择时线索而非既成事实"):
        assert anchor in text, f"signal_flip 文案缺少语义锚点：{anchor}"


def test_signal_flip_label_is_available_as_an_also_true_condition():
    """新 kind 必须同时进 `_ALSO_TRUE_LABEL`，否则它作为次要条件时会被静默丢掉。"""
    text = format_wake_for_prompt(_wake("bar_advanced", alsoTrue=["signal_flip"], flip=_FLIP))
    assert "同时成立=立场翻转" in text


# ---------- T-20：补跑子句 ----------
def test_backlog_clause_is_absent_verbatim_when_nothing_was_dropped():
    """`backlog` 缺席 ⇒ 输出与 a7ed70c **逐字节相同**（G-1 冻结门禁的语义前提）。"""
    baseline = format_wake_for_prompt(_wake("heartbeat"))
    assert "补跑" not in baseline
    assert format_wake_for_prompt(_wake("heartbeat", backlog=None)) == baseline
    empty = {"droppedIntervals": 0, "droppedBars": 0, "waitedS": 0.0, "chain": 0}
    assert format_wake_for_prompt(_wake("heartbeat", backlog=empty)) == baseline


def test_backlog_clause_reports_both_drop_kinds_and_the_wait_since_the_first_one():
    """措辞与 `WakeBacklog.wire` 口径逐字对应：等待时间是「最早的一次」，不是上一轮研判耗时。"""
    text = format_wake_for_prompt(_wake("bar_advanced", backlog={
        "droppedIntervals": 3, "droppedBars": 1, "waitedS": 259.0, "chain": 1}))
    assert "1 根 K 线收线未被研判" in text
    assert "3 次定时唤醒被丢弃" in text
    assert "最早的一次已等待 4 分 19 秒" in text
    assert "上一轮研判耗时" not in text


def test_backlog_clause_precedes_the_also_true_clause():
    """子句顺序固定：唤醒原因 → 补跑 → 同时成立（三段各自独立，互不吞并）。"""
    text = format_wake_for_prompt(_wake("bar_advanced", alsoTrue=["price_breakout"],
                                        backlog={"droppedBars": 2, "waitedS": 61.0}))
    assert text.index("补跑") < text.index("同时成立")


# ---------- 战术段：brief / full / 截断 ----------
def test_brief_tier_renders_exactly_one_line_per_timeframe():
    lines = format_tactical_for_prompt(_context(), "brief", max_chars=0)
    frame_lines = [line for line in lines if line.startswith("- ")]
    assert len(frame_lines) == 2
    assert not any(line.startswith("  └") for line in lines)
    assert "自上轮收 3 根" in frame_lines[0]                 # 真正逐周期不同的量
    assert lines[0].startswith("战术周期（贴合 3 分钟唤醒节奏；")
    assert "不得单独反转中线判断" in lines[0]


def test_full_tier_adds_exactly_one_detail_line_per_timeframe():
    lines = format_tactical_for_prompt(_context(), "full", max_chars=0)
    assert len([line for line in lines if line.startswith("  └")]) == 2
    detail = next(line for line in lines if line.startswith("  └"))
    assert "MACD柱=1.42(bullish)" in detail
    assert "ATR%=0.31(low)" in detail
    # A-4b 收紧：布林%B 与区间位置高度重合、末根 O/H/L/C 四个数字符成本最高而信息量最低，皆已剔除
    assert "布林%B" not in detail
    assert "末根" not in detail


def test_change_since_last_round_is_one_shared_line_with_its_atr_bar_named():
    lines = format_tactical_for_prompt(_context(), "brief", max_chars=0)
    assert "自上一决策轮：价 +0.42%（≈0.91×30m ATR）" in lines
    assert sum("×30m ATR" in line for line in lines) == 1     # 绝不逐周期重复同一个数


def test_divergence_and_missing_bars_are_stated_verbatim_never_guessed():
    lines = format_tactical_for_prompt(
        _context(alignment="diverged", missingBars=["1m"],
                 frames=[_frame("5m", regime={"primary": "range"})]),
        "full", max_chars=0)
    body = "\n".join(lines)
    assert "战术与执行周期体制背离（30m=trend_up，5m=range）" in body
    assert "优先以执行周期与中线背景为准" in body
    assert "缺失周期=1m；不得补猜。" in body


def test_truncation_is_always_visible_never_silent():
    lines = format_tactical_for_prompt(_context(), "full", max_chars=200)
    assert lines[-1].endswith("…（战术周期证据已按 200 字上限截断）")
    assert lines[0].startswith("战术周期（")                  # 抬头永远保留
    assert sum(len(line) + 1 for line in lines[:-1]) <= 200


def test_zero_max_chars_means_no_truncation_at_all():
    lines = format_tactical_for_prompt(_context(), "full", max_chars=0)
    assert not any("截断" in line for line in lines)


def test_absent_tactical_context_renders_nothing():
    assert format_tactical_for_prompt(None, "full", max_chars=700) == []


# ---------- T-9：重复轮压缩 ----------
_MTF = {
    "frames": [{"bar": "1H"}, {"bar": "4H"}],
    "trendAlignment": {"status": "bullish"},
    "nearestSupport": {"price": 29000.0, "distancePct": 1.2, "bars": ["4H"]},
    "nearestResistance": None,
}


def test_repeat_round_multi_timeframe_keeps_only_the_header_plus_one_note():
    lines = format_multi_timeframe_repeat(_MTF)
    assert len(lines) == 2
    assert lines[0].startswith("中线背景：整体=bullish；候选支撑=29000.0")
    assert lines[1] == "（各周期明细与本根首次研判相同，未重复列出）"


def test_repeat_round_defers_to_the_existing_renderer_on_degraded_or_absent_context():
    """`frames` 为空时**原样复用**既有降级行——压成表头等于输出三个 None 的噪音行。"""
    assert format_multi_timeframe_repeat(None) is None
    assert format_multi_timeframe_repeat({"frames": []}) is None


def test_repeat_round_digest_is_one_line_and_points_at_the_tactical_section():
    line = format_digest_repeat(_REPEAT_DIGEST, "30m", has_tactical=True)
    assert line == ("执行周期（30m）证据：与本根首次研判相同（趋势=up，RSI14=58(neutral)，"
                    "量比=1.31，ATR%=0.42）；本轮最新变化见战术周期")
    assert "\n" not in line
    assert format_digest_repeat(None, "30m", has_tactical=True) == ""


def test_repeat_round_digest_never_points_at_an_absent_tactical_section():
    """压缩开 + 战术关（1m 执行 / 总闸关 / 取数全失败）时不得指向不存在的证据段。"""
    line = format_digest_repeat(_REPEAT_DIGEST, "30m", has_tactical=False)
    assert line.endswith("ATR%=0.42）")
    assert "战术周期" not in line
