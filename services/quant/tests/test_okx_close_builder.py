"""平仓体组装与本地校验纯函数单测（平仓 spec §6 测点 1/2）。

覆盖 ``_close_side`` / ``build_close_order_payload`` 四象限（对冲平多/平空、净多/净空）、对冲单不含
reduceOnly、净单不含 posSide、lotSz/tickSz 取整，以及 ``validate_close`` 的缺价 / minSz（P1-2）/
超持仓 / 取整后归零等**拒单异常路径**。不依赖网络。
"""
from __future__ import annotations

import pytest

from app.okx.order_builder import (
    ClosePositionInput,
    InstrumentMeta,
    build_close_order_payload,
    validate_close,
    _close_side,
)
from app.okx.private_client import OKXPrivateError

_META = InstrumentMeta(ct_val=0.01, ct_val_ccy="BTC", lot_sz=1.0, min_sz=1.0, tick_sz=0.1,
                       max_lever=125.0, ct_type="linear", settle_ccy="USDT")


def _close(**over) -> ClosePositionInput:
    base = dict(inst_id="BTC-USDT-SWAP", pos_side="long", pos=5.0, mgn_mode="cross",
                ord_type="limit", px=64000.0, sz=None)
    base.update(over)
    return ClosePositionInput(**base)


def test_close_side_hedge_and_net():
    assert _close_side("long", 5.0) == "sell"        # 平多 → sell
    assert _close_side("short", 5.0) == "buy"        # 平空 → buy
    assert _close_side("net", 5.0) == "sell"         # 净多（pos>0）→ sell
    assert _close_side("net", -5.0) == "buy"         # 净空（pos<0）→ buy


def test_hedge_long_close_uses_sell_and_posside_no_reduce_only():
    p = build_close_order_payload(_close(pos_side="long"), _META, "long_short_mode", 3.0, "aragonc1")
    assert p["side"] == "sell" and p["posSide"] == "long"
    assert "reduceOnly" not in p                      # 对冲单不带 reduceOnly（posSide 已锁侧）
    assert p["px"] == "64000.0" and p["sz"] == "3" and p["ordType"] == "limit"


def test_hedge_short_close_uses_buy_and_posside():
    p = build_close_order_payload(_close(pos_side="short"), _META, "long_short_mode", 2.0, "aragonc2")
    assert p["side"] == "buy" and p["posSide"] == "short"
    assert "reduceOnly" not in p


def test_net_long_close_uses_sell_and_reduce_only_no_posside():
    p = build_close_order_payload(_close(pos_side="net", pos=5.0), _META, "net_mode", 5.0, "aragonc3")
    assert p["side"] == "sell" and p["reduceOnly"] is True
    assert "posSide" not in p                         # 净单不带 posSide


def test_net_short_close_uses_buy_and_reduce_only():
    p = build_close_order_payload(_close(pos_side="net", pos=-5.0), _META, "net_mode", 4.0, "aragonc4")
    assert p["side"] == "buy" and p["reduceOnly"] is True
    assert "posSide" not in p


def test_build_close_snaps_size_and_price_to_steps():
    meta = InstrumentMeta(0.01, "BTC", 1.0, 1.0, 0.1, 125.0, "linear", "USDT")
    p = build_close_order_payload(_close(px=64000.06), meta, "long_short_mode", 3.7, "aragonc5")
    assert p["sz"] == "3"                              # 向下对齐 lotSz
    assert p["px"] == "64000.1"                        # 对齐 tickSz


def test_validate_close_rejects_limit_without_price():
    with pytest.raises(OKXPrivateError) as exc:
        validate_close(_close(px=None), _META, abs_pos=5.0)
    assert exc.value.code == "LIVE_CLOSE_REJECTED_LOCAL"


def test_validate_close_defaults_to_full_position_when_sz_omitted():
    calc = validate_close(_close(sz=None), _META, abs_pos=5.0)
    assert calc["sz"] == 5.0                           # 缺省 = 全仓 |pos|


def test_validate_close_rejects_below_min_sz():
    """取整后 sz<minSz → LIVE_CLOSE_REJECTED_LOCAL（P1-2，与 validate_order 口径一致）。"""
    meta = InstrumentMeta(0.01, "BTC", 1.0, 5.0, 0.1, 125.0, "linear", "USDT")
    with pytest.raises(OKXPrivateError) as exc:
        validate_close(_close(sz=2.0), meta, abs_pos=10.0)
    assert exc.value.code == "LIVE_CLOSE_REJECTED_LOCAL"


def test_validate_close_dust_position_below_min_sz_rejected():
    """尾仓 |pos|<minSz（dust）时默认全仓亦被 minSz 拦截（UI 应引导市价整仓，§2.6）。"""
    meta = InstrumentMeta(0.01, "BTC", 1.0, 5.0, 0.1, 125.0, "linear", "USDT")
    with pytest.raises(OKXPrivateError) as exc:
        validate_close(_close(sz=None), meta, abs_pos=3.0)
    assert exc.value.code == "LIVE_CLOSE_REJECTED_LOCAL"


def test_validate_close_rejects_size_exceeding_position():
    with pytest.raises(OKXPrivateError) as exc:
        validate_close(_close(sz=8.0), _META, abs_pos=5.0)
    assert exc.value.code == "LIVE_CLOSE_SIZE_EXCEEDS"


def test_validate_close_rejects_when_snapped_to_zero():
    with pytest.raises(OKXPrivateError) as exc:
        validate_close(_close(sz=0.4), _META, abs_pos=5.0)   # lotSz=1 → 取整为 0
    assert exc.value.code == "LIVE_CLOSE_REJECTED_LOCAL"


def test_validate_close_accepts_partial_and_returns_snapped_sz():
    calc = validate_close(_close(sz=3.7), _META, abs_pos=5.0)
    assert calc["sz"] == 3.0
