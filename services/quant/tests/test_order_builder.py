"""下单体组装与本地校验纯函数单测（实盘交易模式 §7.1）。

覆盖 long_short / net 的 side/posSide 映射、limit/market 的 px 取舍、attachAlgoOrds 仅在有止盈止损时
出现、lotSz/tickSz 取整，以及 validate_order 命中名义上限 / minSz 的**拒单异常路径**。不依赖网络。
"""
from __future__ import annotations

import pytest

from app.okx.order_builder import (
    InstrumentMeta,
    OrderInput,
    build_order_payload,
    compute_margin,
    compute_notional,
    ensure_instrument_supported,
    is_instrument_supported,
    snap_price,
    snap_size,
    validate_order,
)
from app.okx.private_client import OKXPrivateError

_META = InstrumentMeta(ct_val=0.01, ct_val_ccy="BTC", lot_sz=1.0, min_sz=1.0, tick_sz=0.1,
                       max_lever=125.0, ct_type="linear", settle_ccy="USDT")


def _order(**over) -> OrderInput:
    base = dict(inst_id="BTC-USDT-SWAP", direction="long", ord_type="limit", td_mode="cross",
                px=64000.0, sz=2.0, lever=5.0, tp_trigger_px=None, sl_trigger_px=None)
    base.update(over)
    return OrderInput(**base)


def test_long_short_mode_maps_side_and_pos_side():
    long_p = build_order_payload(_order(direction="long"), _META, "long_short_mode", "aragon1")
    short_p = build_order_payload(_order(direction="short"), _META, "long_short_mode", "aragon2")
    assert long_p["side"] == "buy" and long_p["posSide"] == "long"
    assert short_p["side"] == "sell" and short_p["posSide"] == "short"


def test_net_mode_omits_pos_side():
    long_p = build_order_payload(_order(direction="long"), _META, "net_mode", "aragon3")
    short_p = build_order_payload(_order(direction="short"), _META, "net_mode", "aragon4")
    assert long_p["side"] == "buy" and "posSide" not in long_p
    assert short_p["side"] == "sell" and "posSide" not in short_p


def test_limit_carries_px_market_omits_it():
    limit_p = build_order_payload(_order(ord_type="limit", px=64000.0), _META, "net_mode", "c1")
    market_p = build_order_payload(_order(ord_type="market", px=None), _META, "net_mode", "c2")
    assert limit_p["px"] == "64000.0" and limit_p["ordType"] == "limit"
    assert "px" not in market_p and market_p["ordType"] == "market"


def test_attach_algo_only_when_tp_or_sl_present():
    plain = build_order_payload(_order(), _META, "net_mode", "c3")
    with_tp = build_order_payload(_order(tp_trigger_px=66000.0), _META, "net_mode", "c4")
    with_sl = build_order_payload(_order(sl_trigger_px=62000.0), _META, "net_mode", "c5")
    assert "attachAlgoOrds" not in plain
    assert with_tp["attachAlgoOrds"][0]["tpTriggerPx"] == "66000.0"
    assert with_tp["attachAlgoOrds"][0]["tpOrdPx"] == "-1"
    assert "slTriggerPx" not in with_tp["attachAlgoOrds"][0]
    assert with_sl["attachAlgoOrds"][0]["slTriggerPx"] == "62000.0"
    assert "tpTriggerPx" not in with_sl["attachAlgoOrds"][0]


def test_snap_size_and_price_respect_steps():
    assert snap_size(2.7, 1.0) == 2.0           # 向下取整到 lotSz 整数倍
    assert snap_size(0.45, 0.1) == pytest.approx(0.4)
    assert snap_price(64000.06, 0.1) == pytest.approx(64000.1)


def test_compute_notional_and_margin():
    notional = compute_notional(2.0, 0.01, 64000.0)
    assert notional == pytest.approx(1280.0)
    assert compute_margin(notional, 5.0) == pytest.approx(256.0)
    assert compute_margin(notional, 0) == pytest.approx(notional)  # lever<=0 回落不除零


def test_instrument_support_predicate():
    assert is_instrument_supported(_META) is True
    spot = InstrumentMeta(0.0, "BTC", 0.0001, 0.0001, 0.01, 10.0, "", "BTC")
    assert is_instrument_supported(spot) is False
    with pytest.raises(OKXPrivateError) as exc:
        ensure_instrument_supported(spot)
    assert exc.value.code == "LIVE_INSTRUMENT_UNSUPPORTED"


def test_validate_order_rejects_when_notional_exceeds_cap():
    """名义超本地上限 → LIVE_ORDER_REJECTED_LOCAL（拒单异常路径，不发往 OKX）。"""
    with pytest.raises(OKXPrivateError) as exc:
        validate_order(_order(sz=100.0), _META, max_notional_usd=100.0, ref_px=64000.0)
    assert exc.value.code == "LIVE_ORDER_REJECTED_LOCAL"


def test_validate_order_rejects_below_min_sz():
    meta = InstrumentMeta(0.01, "BTC", 1.0, 5.0, 0.1, 125.0, "linear", "USDT")
    with pytest.raises(OKXPrivateError) as exc:
        validate_order(_order(sz=2.0), meta, max_notional_usd=1e9, ref_px=64000.0)
    assert exc.value.code == "LIVE_ORDER_REJECTED_LOCAL"


def test_validate_order_rejects_limit_without_price():
    with pytest.raises(OKXPrivateError) as exc:
        validate_order(_order(ord_type="limit", px=None), _META, max_notional_usd=1e9, ref_px=0.0)
    assert exc.value.code == "LIVE_ORDER_REJECTED_LOCAL"


def test_validate_order_accepts_valid_and_returns_calc():
    calc = validate_order(_order(sz=2.0), _META, max_notional_usd=1e9, ref_px=64000.0)
    assert calc["sz"] == 2.0
    assert calc["notional"] == pytest.approx(1280.0)
    assert calc["margin"] == pytest.approx(256.0)
