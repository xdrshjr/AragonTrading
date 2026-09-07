"""轴 D · Agent 成本意识的文案与视图（ai-trade-cost-truth-and-net-pnl §3.4）。

覆盖：宪章章与每轮段的渲染、缺键整段省略（prompt 逐字节回退）、盈亏平衡价**与杠杆无关**、
预览假设（`previewOrdType` / `previewSide`）如实下发，以及 G-3 的单一真源侧面。
"""
from __future__ import annotations

import pytest

from app.ai_auto.cost_model import CostBreakdown, break_even_px
from app.ai_auto.cost_runtime import CostRuntime, build_charter_text
from app.ai_auto.cost_text import format_cost_charter, format_cost_for_prompt
from app.ai_auto.cost_view import build_cost_view
from app.ai_auto.prompts import build_ai_trade_system_prompt, build_decision_prompt
from app.config import Settings
from app.live_auto.fee_ledger import build_fee_ledger

_INST = "BTC-USDT-SWAP"
_COST = CostBreakdown(fee_open_bps=5.0, fee_close_bps=5.0, half_spread_bps=1.0,
                      impact_bps=0.5, crossing_legs=2, funding_carry_bps=1.0,
                      total_bps=14.0, confidence="ok")


def _position(side):
    return {"side": side, "avgEntry": 30_000, "qtyCoin": 1,
            "openFeeUsd": 18, "fundingAccruedUsd": 0, "closeFeeRate": .0005}


def _view(**over) -> dict:
    base = build_cost_view(_COST, fee_snapshot=None, ord_type="market", side="long",
                           position=None, last_px=30_000.0, atr_px=150.0,
                           session_fee_usd=12.34, net_basis=True)
    base.update(over)
    return base


# ---------- 用例 1：盈亏平衡价（D-2）----------
def test_break_even_price_is_independent_of_leverage():
    """平衡价只由成本 bps 与开仓价决定——杠杆放大的是保证金收益率，不是回本所需的价格距离。"""
    assert break_even_px(30_000.0, side="long", cost_bps=10.0) == pytest.approx(30_030.0)
    assert break_even_px(30_000.0, side="short", cost_bps=10.0) == pytest.approx(29_970.0)


def test_break_even_price_rejects_bad_inputs():
    assert break_even_px(0.0, side="long", cost_bps=10.0) is None
    assert break_even_px(30_000.0, side="flat", cost_bps=10.0) is None
    assert break_even_px(30_000.0, side="long", cost_bps=float("nan")) is None


def test_break_even_gap_is_direction_aware():
    """空头的「还差多少」必须取反，否则文案会把「已覆盖成本」说成「还差」（正负号说反）。"""
    long_view = _view()
    short_view = build_cost_view(_COST, fee_snapshot=None, ord_type="market", side="short",
                                 position=_position("short"),
                                 last_px=30_000.0, atr_px=None, session_fee_usd=None,
                                 net_basis=True)
    assert long_view["breakEvenPx"] is None                 # 空仓 ⇒ 两键为 None
    expected = (30000 - (30000 - 18) / 1.0005) / 30000
    assert short_view["breakEvenGapPct"] == pytest.approx(expected, abs=1e-8)


# ---------- 用例 2：宪章章 ----------
def test_charter_states_cost_price_distance_and_basis():
    text = format_cost_charter(_view())
    assert "【交易成本 · 事前估算】" in text
    assert "14.0 bps" in text and "0.140%" in text
    assert "与杠杆无关" in text
    assert "已扣除" in text                                  # 净额口径必须写明


def test_charter_says_gross_when_the_net_switch_is_off():
    text = format_cost_charter(_view(netBasis=False))
    assert "毛额" in text and "未扣" in text
    assert "已扣除" not in text


def test_charter_is_empty_without_a_view_so_the_system_prompt_falls_back_verbatim():
    assert format_cost_charter(None) == ""
    assert build_ai_trade_system_prompt(_INST, "15m", 1000.0, 3.0, cost_text="") == \
        build_ai_trade_system_prompt(_INST, "15m", 1000.0, 3.0)


def test_charter_discloses_the_fee_source():
    """数从哪来决定它可不可信：官方档位与本地常数必须能一眼分辨，绝不含糊。"""
    exchange = format_cost_charter(_view(source="exchange", level="Lv2"))
    config = format_cost_charter(_view(source="config", level=""))
    assert "交易所官方接口" in exchange and "Lv2" in exchange
    assert "本地保守常数" in config


def test_charter_says_funding_is_missing_instead_of_counting_it_as_zero():
    text = format_cost_charter(_view(fundingCarryBps=None))
    assert "资金费率暂未取到" in text and "实际支出或收入暂不可得" in text


# ---------- 用例 3：每轮段 ----------
def test_round_section_shows_cost_preview_assumptions_and_session_fee():
    line = format_cost_for_prompt(_view(previewOrdType="limit", previewSide="short"))
    assert "本轮往返成本≈14.0 bps" in line
    assert "按限价腿、空头方向估算" in line                 # 预览假设必须如实说明
    assert "本会话累计已付手续费 12.3400 USDT" in line
    assert "当前空仓" in line


def test_round_section_shows_break_even_and_gap_when_holding():
    view = build_cost_view(_COST, fee_snapshot=None, ord_type="market", side="long",
                           position=_position("long"),
                           last_px=30_000.0, atr_px=150.0, session_fee_usd=1.0,
                           net_basis=True)
    line = format_cost_for_prompt(view)
    assert view["breakEvenPx"] == pytest.approx((30000 + 18) / .9995)
    assert f"盈亏平衡价≈{view['breakEvenPx']}" in line
    assert "距平衡价还差 0.110%" in line


def test_round_section_omits_the_atr_clause_instead_of_writing_unknown():
    assert "ATR" not in format_cost_for_prompt(_view(atrShare=None))
    assert "占当根 ATR 的 28%" in format_cost_for_prompt(_view())


def test_missing_key_makes_the_decision_prompt_fall_back_byte_for_byte():
    payload = {"round": 1, "barTs": 1, "lastPx": 30_000.0, "position": None,
               "sessionPnlUsd": 0.0, "budgetUsd": 1000.0, "takeProfitUsd": None,
               "stopLossUsd": None, "signalBoard": []}
    assert format_cost_for_prompt(None) == ""
    assert build_decision_prompt(payload) == build_decision_prompt({**payload})
    assert "【交易成本】" not in build_decision_prompt(payload)
    assert "【交易成本】" in build_decision_prompt({**payload, "costAwareness": _view()})


# ---------- 用例 4：G-3 单一真源（预览与门禁读同一份 CostParams）----------
def test_preview_cost_reads_the_same_cost_params_as_the_gate():
    settings = Settings()
    ledger = build_fee_ledger(settings, ct_val=0.01)
    runtime = CostRuntime(_INST, settings, ledger, allow_limit=True)
    preview = runtime.estimate_preview_cost(ord_type="limit", side="long", bar_seconds=900)
    params = runtime._cost_params()
    assert preview.fee_open_bps == params.taker_fee_bps
    assert preview.fee_close_bps == params.taker_fee_bps     # 平仓腿恒 taker
    view = runtime.cost_view(bar_seconds=900)
    assert view["previewOrdType"] == "limit"                 # 复用会话装配的 allow_limit
    assert view["roundTripBps"] == preview.total_bps


def test_cost_view_is_absent_when_the_awareness_switch_is_off():
    settings = Settings(ai_trade_cost_awareness_enabled=False)
    runtime = CostRuntime(_INST, settings, build_fee_ledger(settings, ct_val=0.01))
    payload: dict = {}
    runtime.inject(payload, last_px=30_000.0, atr_px=None, bar_seconds=900)
    assert "costAwareness" not in payload                    # **整键缺席**，不是 None
    assert runtime.charter_text(900) == ""


def test_flat_session_previews_the_unfavourable_funding_side():
    """空仓时方向假设取资金费的不利侧——绝不因方向假设而让成本看起来更便宜。"""
    settings = Settings(ai_trade_funding_enabled=True)
    runtime = CostRuntime(_INST, settings, build_fee_ledger(settings, ct_val=0.01))
    view = runtime.cost_view(bar_seconds=900)
    long_cost = runtime.estimate_preview_cost(ord_type="market", side="long", bar_seconds=900)
    short_cost = runtime.estimate_preview_cost(ord_type="market", side="short", bar_seconds=900)
    assert view["roundTripBps"] == max(long_cost.total_bps, short_cost.total_bps)


def test_holding_session_previews_the_position_side():
    settings = Settings()
    runtime = CostRuntime(_INST, settings, build_fee_ledger(settings, ct_val=0.01))
    payload = {"position": {"side": "short", "avgEntry": 30_000.0}}
    runtime.inject(payload, last_px=30_000.0, atr_px=None, bar_seconds=900)
    assert payload["costAwareness"]["previewSide"] == "short"
    assert payload["costAwareness"]["breakEvenPx"] is None
    assert "覆盖不足" in format_cost_for_prompt(payload["costAwareness"])


def test_startup_charter_uses_the_same_assembly_path_as_the_round_section():
    settings = Settings()
    wire = {"makerBps": 1.5, "takerBps": 4.0, "level": "Lv1", "ruleType": "normal",
            "source": "exchange", "rebate": False, "makerBpsRaw": 1.5, "takerBpsRaw": 4.0}
    text = build_charter_text(settings, _INST, fee_rates_wire=wire, bar_seconds=900,
                              allow_limit=True)
    assert "Lv1" in text and "交易所官方接口" in text
    assert "开仓腿 4.0" in text  # 普通限价采用官方 taker 档位。


@pytest.mark.parametrize("source,known,interval,phrase,periods", [
    ("historical_observed", False, 3600, "按最近历史间隔估计 1h", 24),
    ("unknown", False, -1, "周期未知、按模型回落间隔估计 8h", 3),
    ("exchange_schedule", True, 3600, "当前安排 1h", 24),
    ("unknown", True, 3600, "周期值可用、来源未记录（1h）", 24),
])
def test_funding_interval_text_matches_actual_calculation(
        source, known, interval, phrase, periods):
    from app.ai_auto.cost_model import (
        CostParams, CostQuery, cost_wire, estimate_round_trip_cost,
    )
    cost = estimate_round_trip_cost(CostQuery(
        "market", "long", 3600, funding_rate=0.0001,
        funding_interval_s=interval, funding_interval_known=known,
        funding_interval_source=source), CostParams(2, 5, 1, 0, 24))
    view = build_cost_view(
        cost, fee_snapshot=None, ord_type="market", side="long",
        position=None, last_px=100, atr_px=None, session_fee_usd=0,
        net_basis=True)
    assert cost.funding_periods == periods
    assert cost.funding_carry_bps == periods
    assert phrase in format_cost_charter(view)
    for key in ("fundingIntervalSource", "fundingAssumedIntervalS"):
        assert view[key] == cost_wire(cost)[key]


def test_disabled_funding_discloses_no_fictional_period():
    from app.ai_auto.cost_model import (
        CostParams, CostQuery, estimate_round_trip_cost,
    )
    from app.ai_auto.cost_text import _funding_periods_clause
    cost = estimate_round_trip_cost(CostQuery(
        "market", "long", 3600, funding_rate=1, funding_enabled=False),
        CostParams(2, 5, 1, 0, 24))
    assert cost.funding_carry_bps == 0
    assert _funding_periods_clause({"fundingPeriods": cost.funding_periods}) == ""


@pytest.mark.parametrize("carry", [0, None])
def test_zero_or_missing_rate_still_discloses_unknown_interval(carry):
    view = _view(fundingCarryBps=carry, fundingPeriods=24,
                 fundingIntervalS=None,
                 fundingIntervalSource="historical_observed",
                 fundingAssumedIntervalS=3600)
    assert "按最近历史间隔估计 1h" in format_cost_charter(view)
