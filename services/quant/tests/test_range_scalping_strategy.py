from __future__ import annotations

import pandas as pd

from app.engine.simulator import BrokerContext
from app.strategies import STRATEGY_REGISTRY
from app.strategies._range_structure import (
    RangeStructureConfig,
    attach_causal_range,
    resample_closed_buckets,
)
from app.strategies.range_scalping_strategy import RangeScalpingStrategy


def _ctx(side: str = "flat") -> BrokerContext:
    return BrokerContext(
        position_side=side,
        bars_in_position=0,
        entry_price=100.0,
        equity=10_000.0,
    )


def _minute_rows(n: int = 180, start: int = 1_700_000_000) -> pd.DataFrame:
    rows = []
    for i in range(n):
        base = 100.0 + (i % 10) * 0.05
        rows.append({
            "ts": start + i * 60,
            "o": base,
            "h": base + 0.8,
            "l": base - 0.8,
            "c": base + 0.02,
            "vol": 100.0 + i,
        })
    return pd.DataFrame(rows)


def _stable_row(**overrides) -> pd.DataFrame:
    row = {
        "ts": 1_700_000_000,
        "o": 100.0,
        "h": 110.0,
        "l": 90.0,
        "c": 100.0,
        "vol": 100.0,
        "range_high": 110.0,
        "range_low": 90.0,
        "range_mid": 100.0,
        "range_width": 20.0,
        "range_atr": 10.0,
        "range_rsi": 50.0,
        "range_boll_upper": 106.0,
        "range_boll_ma": 100.0,
        "range_boll_lower": 94.0,
        "range_boll_width": 12.0,
        "is_range_stable": True,
        "upper_rejection": False,
        "lower_rejection": False,
    }
    row.update(overrides)
    return pd.DataFrame([row])


def test_resample_closed_buckets_aggregates_ohlc():
    df = pd.DataFrame([
        {"ts": 0, "o": 1.0, "h": 2.0, "l": 0.5, "c": 1.5, "vol": 10.0},
        {"ts": 60, "o": 1.5, "h": 3.0, "l": 1.0, "c": 2.5, "vol": 11.0},
        {"ts": 300, "o": 4.0, "h": 5.0, "l": 3.0, "c": 4.5, "vol": 12.0},
    ])

    buckets = resample_closed_buckets(df, 300)

    assert buckets.to_dict("records") == [
        {"bucket": 0, "o": 1.0, "h": 3.0, "l": 0.5, "c": 2.5, "vol": 21.0},
        {"bucket": 1, "o": 4.0, "h": 5.0, "l": 3.0, "c": 4.5, "vol": 12.0},
    ]


def test_range_mapping_is_causal():
    df = _minute_rows(180, start=0)
    cfg = RangeStructureConfig(
        range_seconds=300,
        range_lookback=4,
        boll_period=4,
        boll_std_mult=2.0,
        boll_width_atr_max=10.0,
        rsi_period=4,
        rsi_mid=50.0,
        rsi_band=50.0,
        atr_period=4,
        width_atr_max=10.0,
    )
    baseline = attach_causal_range(df, 60, cfg)
    mutated = df.copy()
    mutated.loc[45:, ["h", "l", "c"]] = [999.0, 1.0, 500.0]

    changed = attach_causal_range(mutated, 60, cfg)

    cols = ["range_high", "range_low", "range_mid", "range_atr", "range_rsi"]
    pd.testing.assert_frame_equal(baseline.loc[:44, cols], changed.loc[:44, cols])
    current_bucket_rows = baseline[(baseline["ts"] >= df["ts"].iat[45]) & (baseline["ts"] < df["ts"].iat[50])]
    assert current_bucket_rows["range_high"].nunique(dropna=True) <= 1


def test_current_bucket_does_not_map_its_own_finished_range():
    df = _minute_rows(180, start=0)
    cfg = RangeStructureConfig(
        range_seconds=300,
        range_lookback=4,
        boll_period=4,
        boll_std_mult=2.0,
        boll_width_atr_max=10.0,
        rsi_period=4,
        rsi_mid=50.0,
        rsi_band=50.0,
        atr_period=4,
        width_atr_max=10.0,
    )
    baseline = attach_causal_range(df, 60, cfg)
    mutated = df.copy()
    mutated.loc[45:49, ["h", "l", "c"]] = [999.0, 1.0, 500.0]

    changed = attach_causal_range(mutated, 60, cfg)

    cols = ["range_high", "range_low", "range_mid", "range_atr", "range_rsi"]
    pd.testing.assert_frame_equal(baseline.loc[45:49, cols], changed.loc[45:49, cols])


def test_range_mapping_keeps_incomplete_history_nan():
    df = _minute_rows(30, start=0)
    cfg = RangeStructureConfig(
        range_seconds=300,
        range_lookback=4,
        boll_period=4,
        boll_std_mult=2.0,
        boll_width_atr_max=10.0,
        rsi_period=4,
        rsi_mid=50.0,
        rsi_band=50.0,
        atr_period=4,
        width_atr_max=10.0,
    )

    out = attach_causal_range(df, 60, cfg)

    assert pd.isna(out["range_high"].iat[5])
    assert pd.isna(out["is_range_stable"].iat[5])


def test_strategy_warmup_is_pure():
    base = RangeScalpingStrategy()
    slower = RangeScalpingStrategy({"range_seconds": 900, "boll_period": 30})

    assert base.warmup() > 0
    assert slower.warmup() > base.warmup()


def test_prepare_does_not_mutate_runtime_state():
    strat = RangeScalpingStrategy()
    strat._last_signal_ts = 123

    prepared = strat.prepare(_minute_rows(), "1m")
    strat.prepare(prepared, "1m")

    assert strat._last_signal_ts == 123


def test_short_emits_at_upper_boundary():
    strat = RangeScalpingStrategy()
    df = _stable_row(o=109.5, h=111.0, l=108.8, c=109.0, upper_rejection=True)

    signals = strat.on_candle(df, 0, _ctx())

    assert len(signals) == 1
    assert signals[0].side == "short"
    assert signals[0].reason == "Range upper short"


def test_long_emits_at_lower_boundary():
    strat = RangeScalpingStrategy()
    df = _stable_row(o=90.5, h=91.2, l=89.0, c=91.0, lower_rejection=True)

    signals = strat.on_candle(df, 0, _ctx())

    assert len(signals) == 1
    assert signals[0].side == "long"
    assert signals[0].reason == "Range lower long"


def test_no_signal_when_rsi_or_bollinger_not_stable():
    strat = RangeScalpingStrategy()
    df = _stable_row(
        o=109.5,
        h=111.0,
        l=108.8,
        c=109.0,
        is_range_stable=False,
        upper_rejection=True,
    )

    assert strat.on_candle(df, 0, _ctx()) == []


def test_long_closes_at_mid_or_structural_stop():
    strat = RangeScalpingStrategy()
    mid_hit = _stable_row(h=101.0, c=100.5)
    stop_hit = _stable_row(h=99.0, l=83.5, c=84.0)

    assert strat.on_candle(mid_hit, 0, _ctx("long"))[0].reason == "Target range mid"
    assert strat.on_candle(stop_hit, 0, _ctx("long"))[0].reason == "Range stop lower"


def test_short_closes_at_mid_or_structural_stop():
    strat = RangeScalpingStrategy()
    mid_hit = _stable_row(l=99.0, c=99.5)
    stop_hit = _stable_row(h=116.5, l=101.0, c=116.0)

    assert strat.on_candle(mid_hit, 0, _ctx("short"))[0].reason == "Target range mid"
    assert strat.on_candle(stop_hit, 0, _ctx("short"))[0].reason == "Range stop upper"


def test_default_params_define_all_rule_terms():
    params = RangeScalpingStrategy.meta.default_params

    for key in (
        "touch_atr_mult",
        "wick_ratio",
        "width_atr_max",
        "entry_zone_atr_mult",
        "stop_atr_mult",
        "stop_loss_pct",
        "max_hold_bars",
    ):
        assert key in params


def test_registered_builtin():
    assert "range_scalping_strategy" in STRATEGY_REGISTRY
