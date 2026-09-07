"""价格尺度、锚区与文本的确定性回归。"""
import math

import pandas as pd
import pytest

from app.ai_auto.base_rules import (
    MaBandRule, apply_ma_band_gate, band_pct_b, build_ma_band,
    format_band_for_prompt,
)
from app.ai_auto.decider import TradeDecision
from app.ai_auto.market_digest import build_market_digest
from app.config import Settings


def frame(scale):
    closes = [scale * (1 + .02 * math.sin(i / 3)) for i in range(80)]
    return pd.DataFrame({"ts": range(80), "o": closes, "c": closes,
                         "h": [v * 1.001 for v in closes],
                         "l": [v * .999 for v in closes], "vol": [100] * 80})


@pytest.mark.parametrize("scale", [1e-8, 1e-5, .1, 100, 60000])
@pytest.mark.parametrize("action", ["open_long", "open_short"])
def test_scale_preserves_band_gate_and_evidence(scale, action):
    rule = MaBandRule()
    band = build_ma_band(frame(scale), rule)
    reference = build_ma_band(frame(1), rule)
    assert band["upper"] > band["mid"] > band["lower"] > 0
    assert band["pctB"] == pytest.approx(reference["pctB"])
    for anchor in (0, .5, 1):
        for offset, accepted in ((0, True), (.15 - 1e-7, True),
                                  (.15 + 1e-7, False)):
            px = band["lower"] + (anchor + offset) * (
                band["upper"] - band["lower"])
            proposal = TradeDecision(action, .5, .9, "尺度测试", "limit", px)
            final, reason = apply_ma_band_gate(proposal, band, rule)
            assert (reason is None) == accepted
            assert final.action == (action if accepted else "hold")
            assert band_pct_b(band, px) == pytest.approx(anchor + offset)
    digest = build_market_digest(frame(scale), "1m", Settings())
    base = build_market_digest(frame(1), "1m", Settings())
    assert digest["priceAction"]["trend"] == base["priceAction"]["trend"]
    assert digest["priceAction"]["emaFast"] / scale == pytest.approx(
        base["priceAction"]["emaFast"])
    assert digest["factors"]["macdHist"]["value"] / scale == pytest.approx(
        base["factors"]["macdHist"]["value"])


def test_tiny_price_prompt_keeps_nonzero_decimal_and_unit():
    from app.ai_auto.numeric_text import format_price
    assert format_price(1.234e-8) == "0.00000001234"
    assert format_price(-1.2e-8) == "-0.000000012"
    assert format_price(0) == "0"
    for value in (None, "bad", float("nan"), float("inf")):
        assert format_price(value) == "不可得"
    assert "USDT" in format_band_for_prompt(build_ma_band(frame(1e-8), MaBandRule()))


@pytest.mark.parametrize("scale", [1e-8, 1e-5, .1, 100, 60000])
@pytest.mark.parametrize("side", ["long", "short"])
def test_scale_preserves_cost_stop_and_sizing(scale, side):
    from app.ai_auto.cost_model import (
        CostQuery, break_even_px, estimate_round_trip_cost, resolve_cost_params,
    )
    from app.ai_auto.sizing import (
        SizingInputs, SizingRule, StopRule, realized_vol_bar,
        resolve_size_fraction, resolve_stop_distance,
    )
    cost = estimate_round_trip_cost(
        CostQuery("limit", side, 60, funding_rate=0),
        resolve_cost_params(Settings()))
    stop_rule = StopRule(2, 2, .2, .4)
    plan, reason = resolve_stop_distance(atr_px=scale * .01,
        ref_px=scale, rule=stop_rule, cost_bps=cost.total_bps)
    expected, _ = resolve_stop_distance(atr_px=.01, ref_px=1,
        rule=stop_rule, cost_bps=cost.total_bps)
    assert reason is None
    assert plan.stop_bps == expected.stop_bps
    assert plan.take_bps == expected.take_bps
    assert break_even_px(scale, side=side, cost_bps=cost.total_bps) == pytest.approx(
        scale * break_even_px(1, side=side, cost_bps=cost.total_bps))
    rule = SizingRule(.3, 20, .25, 1, 0, 20)
    results = []
    for amount in (scale, 1):
        vol, _ = realized_vol_bar(frame(amount)["c"].tolist(), 20)
        results.append(resolve_size_fraction(SizingInputs(
            action=f"open_{side}", model_fraction=.5, p_win=.6,
            cost_bps=cost.total_bps, stop_bps=plan.stop_bps,
            take_bps=plan.take_bps, realized_vol_bar=vol), rule, 60))
    assert results[0].fraction == pytest.approx(results[1].fraction)
