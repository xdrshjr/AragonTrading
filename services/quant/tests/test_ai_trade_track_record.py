"""轴 D：四条装配门禁、prompt 逐字回退、文案禁令（G-14 / G-14c / G-16）。"""
from __future__ import annotations

import re
from types import SimpleNamespace

import pytest

from app.ai_auto.prompts import build_decision_prompt
from app.ai_auto.track_record import (
    TrackRecordRuntime,
    build_track_record,
    format_track_record_text,
)
from app.config import get_settings


def _history(*, trips: int = 193, fee_trips: int = 21, coverage: float = 0.1088,
             code: str = "no_edge_yet") -> dict:
    return {
        "schemaVersion": 2,
        "basis": "trading_net_ex_funding", "asOfTs": 123,
        "snapshotId": "frozen-startup-sample",
        "coverage": {"totalTrips": trips, "netEligibleTrips": trips,
                     "measuredNetTrips": trips, "excludedTrips": 0,
                     "structuralIssueCount": 0, "feeTrips": trips},
        "instId": "BTC-USDT-SWAP", "bar": "15m",
        "pooled": {"roundTrips": trips, "winRate": 0.4041, "payoff": 1.014,
                   "breakevenPayoff": 1.474, "expectancyUsd": -0.0384,
                   "medianMoveBps": 13.9, "medianCostBps": 7.01,
                   "feeTrips": fee_trips, "feeCoverage": coverage,
                   "moveSamples": trips, "feeBpsTrips": fee_trips,
                   "travelComparisonTrips": fee_trips,
                   "travelComparisonCoverage": coverage,
                   "travelMedianFeeBps": 7.01,
                   "expectancyR": -0.011, "rSamples": 191},
        "verdict": {"code": code, "title": "尚无正期望证据", "detail": "…"},
    }


def _settings(**over):
    return get_settings().model_copy(update=over)


def _payload() -> dict:
    return {"round": 1, "barTs": 100, "lastPx": 100.0, "position": None,
            "sessionPnlUsd": 0.0, "budgetUsd": 100.0, "takeProfitUsd": None,
            "stopLossUsd": None, "signalBoard": []}


def test_g14_each_gate_makes_the_whole_key_absent():
    """G-14：四条装配门禁逐条；任一不满足 ⇒ `None` ⇒ payload 无 `trackRecord` 键。"""
    assert build_track_record(_history(), [], _settings(
        ai_trade_track_record_enabled=False)) is None                     # ① 总闸
    assert build_track_record(_history(trips=5), [], _settings(
        ai_trade_track_record_min_trips=20)) is None                      # ② 样本下限
    assert build_track_record(_history(code="insufficient_samples"), [],
                              _settings()) is None                        # ③ 结论码
    assert build_track_record(None, [], _settings()) is None               # ④ 该组合无历史
    assert build_track_record(_history(), [], _settings()) is not None


def test_g14_prompt_is_byte_identical_when_the_key_is_absent():
    """G-14 的另一半：键缺席 ⇒ `build_decision_prompt` 输出与合入前**逐字节相同**。"""
    base = build_decision_prompt(_payload())
    assert "实测战绩" not in base
    with_key = build_decision_prompt({**_payload(),
                                      "trackRecord": build_track_record(_history(), [],
                                                                        _settings())})
    assert "历史交易净额，不含资金费" in with_key
    assert with_key.startswith(base.split("\n")[0])


def test_g14c_cost_sentence_is_omitted_when_fee_coverage_is_low():
    """G-14c（[P0-4] 阻断级）：`feeCoverage=0.11`（生产库实况）⇒ prompt **不含**成本句。

    并断言 prompt 里**永不出现 "0.0 bps"**——一个由「没记费率」算出来的零成本，会把「别做撑不过
    成本的交易」这句纪律翻译成「交易几乎不要钱」的反向暗示。
    """
    wire = build_track_record(_history(coverage=0.1088, fee_trips=21), [], _settings())
    text = format_track_record_text(wire)
    assert "往返成本" not in text
    assert "0.0 bps" not in text


def test_g14c_cost_sentence_appears_when_coverage_is_good():
    """覆盖率达标 ⇒ 成本句出现，且数值取自**带费子样本**并带上那个分母。"""
    wire = build_track_record(_history(coverage=0.8, fee_trips=90), [], _settings())
    text = format_track_record_text(wire)
    assert "往返手续费约 7.0 bps" in text and "90 笔同笔尺度" in text


def test_zero_median_cost_never_produces_a_cost_sentence():
    """全样本腿费恒 0（172/193 笔的实况）⇒ 即使覆盖率被填成 1.0，也不下发一个 0.0 bps 的成本。"""
    history = _history(coverage=1.0, fee_trips=193)
    history["pooled"]["medianCostBps"] = 0.0
    history["pooled"]["travelMedianFeeBps"] = 0.0
    text = format_track_record_text(build_track_record(history, [], _settings()))
    assert "往返成本" not in text and "0.0 bps" not in text


def test_g16_copy_has_no_markdown_bold_no_promises_and_no_negative_imperatives():
    """G-16：无 `**`（第 4 次复发防线）、无收益承诺词、无否定式祈使句（[P1-9] 约束 ③）。"""
    text = format_track_record_text(build_track_record(_history(coverage=0.8, fee_trips=90),
                                                      [], _settings()))
    assert "**" not in text
    for word in ("已校准", "经回测验证", "预期年化", "稳定盈利", "保证"):
        assert word not in text
    for word in ("不要", "停止", "必须减少"):
        assert word not in text
    assert "不是对未来的承诺" in text        # 免责必须在


def test_every_number_carries_its_own_denominator():
    """约束 ④：历史段 / 会话段 / 成本句各自的分母都必须出现在文本里。"""
    text = format_track_record_text(build_track_record(_history(coverage=0.8, fee_trips=90),
                                                       [], _settings()))
    assert "历史 193 笔往返" in text and "最近 0 笔" in text
    assert "90 笔同笔尺度" in text and "191 笔可算 R" in text


def test_session_segment_is_computed_from_settled_trips_each_round():
    """会话段每轮现算（零 IO）：`TrackRecordRuntime` 持的是取数闭包，不是一份会过期的快照。"""
    trips: list = []
    runtime = TrackRecordRuntime(_history(), _settings(), lambda: list(trips))
    payload = _payload()
    runtime.inject(payload)
    assert payload["trackRecord"]["session"]["roundTrips"] == 0 and runtime.shown is True

    class _Trip:
        move_bps, r_multiple, net_pnl_usd = 13.9, -0.01, -0.03

    trips.append(_Trip())
    second = _payload()
    runtime.inject(second)
    assert second["trackRecord"]["session"]["roundTrips"] == 1
    assert second["trackRecord"]["session"]["medianMoveBps"] == 13.9


@pytest.mark.parametrize("enabled", [True, False])
def test_track_record_shown_is_recorded_for_arm_reconciliation(enabled: bool):
    """[P1-9]：`trackRecordShown` 如实留痕——事后对账必须能按「有没有喂」分臂。"""
    runtime = TrackRecordRuntime(_history(), _settings(ai_trade_track_record_enabled=enabled),
                                 lambda: [])
    payload = _payload()
    runtime.inject(payload)
    assert runtime.shown is enabled
    assert ("trackRecord" in payload) is enabled


def test_prompt_section_never_contains_an_unrendered_placeholder():
    """文案纪律：全部数值由后端格式化好下发，prompt 里不得留下任何未替换的花括号。"""
    text = format_track_record_text(build_track_record(_history(), [], _settings()))
    assert not re.search(r"\{[a-zA-Z]", text)


@pytest.mark.parametrize("patch", [
    {"coverage": None}, {"schemaVersion": 99},
    {"verdict": {"code": "insufficient_evidence"}},
    {"coverage": {"totalTrips": 194, "netEligibleTrips": 193,
                  "measuredNetTrips": 193, "excludedTrips": 1,
                  "structuralIssueCount": 0}},
    {"coverage": {"totalTrips": 193, "netEligibleTrips": 193,
                  "measuredNetTrips": 192, "excludedTrips": 0,
                  "structuralIssueCount": 0}},
])
def test_incomplete_or_unknown_history_cannot_assert_performance(patch):
    assert build_track_record({**_history(), **patch}, [], _settings()) is None


def test_disabled_runtime_removes_stale_payload_key():
    payload = {"trackRecord": {"stale": True}}
    runtime = TrackRecordRuntime(_history(), _settings(
        ai_trade_track_record_enabled=False))
    runtime.inject(payload)
    assert "trackRecord" not in payload


def test_rolling_window_caps_at_200_without_adding_history():
    rows = [SimpleNamespace(net_pnl_usd=i, move_bps=i, r_multiple=i,
                            measured_net_eligible=True) for i in range(201)]
    wire = build_track_record(_history(), rows, _settings())
    session = wire["session"]
    assert session["scope"] == "rolling"
    assert session["capacity"] == session["sampleTrips"] == 200
    assert session["netSamples"] == session["measuredNetSamples"] == 200
    assert session["netPnlUsd"] == sum(range(1, 201))
    assert wire["history"]["snapshotId"] == "frozen-startup-sample"
    text = format_track_record_text(wire)
    assert "本次进程/恢复后可用的最近 200 笔，窗口上限 200" in text
    assert "本会话累计" not in text


@pytest.mark.parametrize("unknown", [None, float("nan"), float("inf"), True])
def test_unknown_rolling_net_is_not_zero(unknown):
    rows = [SimpleNamespace(net_pnl_usd=-2, fee_measurement="estimated"),
            SimpleNamespace(net_pnl_usd=unknown)]
    wire = build_track_record(_history(), rows, _settings())
    session = wire["session"]
    assert session["netPnlUsd"] is None
    assert session["knownNetPnlUsd"] == -2
    assert session["netSamples"] == 1
    assert session["measuredNetSamples"] == 0
    assert session["moveSamples"] == session["rSamples"] == 0
    assert "含估算" in format_track_record_text(wire)


def test_empty_rolling_window_has_no_invented_zero_profit():
    session = build_track_record(_history(), [], _settings())["session"]
    assert session["sampleTrips"] == session["netSamples"] == 0
    assert session["netPnlUsd"] is None
    assert session["knownNetPnlUsd"] is None
