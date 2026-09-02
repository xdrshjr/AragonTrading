"""结算匹配纯核心的七条判据 + wire 省略键（ai-trade-fill-truth-and-effective-controls 轴 A）。

门禁 `G-A1`（七条判据各有一条**会红**的反向用例）、`G-A12`（资金费 / 强平罚金不进本地账本）、
`G-A15`（`RV-P0-4`：`direction` fail-open、`posId` 空串恒不命中）全部落在本文件。
"""
from __future__ import annotations

from app.live_auto.settlement import (
    SettlementQuery,
    coverage_wire,
    match_settlement,
    settlement_meta_wire,
)

_INST = "SOL-USDT-SWAP"
_ENTRY_TS = 1_788_282_000
_DETECT_TS = 1_788_285_000


def _row(**over) -> dict:
    """一条形状真实的 `_normalize_position_history` 归一产物（三项已归一为「正 = 支出」）。"""
    base = {"posId": "p1", "instId": _INST, "direction": "long",
            "openAvgPx": 100.89, "closeAvgPx": 99.88,
            "cTime": _ENTRY_TS, "uTime": _DETECT_TS + 3,
            "pnl": -2.49, "fee": 0.25, "fundingFee": 0.01, "liqPenalty": 0.0,
            "realizedPnl": -2.75, "identityGapUsd": 0.0}
    base.update(over)
    return base


def _query(**over) -> SettlementQuery:
    base = {"inst_id": _INST, "direction": "long",
            "entry_ts": _ENTRY_TS, "detect_ts": _DETECT_TS}
    base.update(over)
    return SettlementQuery(**base)


def _match(rows, *, claimed=None, tol_s=60, query=None):
    return match_settlement(rows, query or _query(), claimed=claimed or set(), tol_s=tol_s)


# ---------- 正路径 ----------

def test_matches_a_well_formed_row():
    fact = _match([_row()])
    assert fact is not None
    assert (fact.pos_id, fact.close_px) == ("p1", 99.88)
    assert (fact.gross_pnl_usd, fact.fee_usd) == (-2.49, 0.25)
    assert (fact.funding_usd, fact.liq_usd) == (0.01, 0.0)


# ---------- 判据 1：instId ----------

def test_rejects_other_instrument():
    assert _match([_row(instId="ETH-USDT-SWAP")]) is None


# ---------- 判据 2：direction 是 fail-open（G-A15 上半） ----------

def test_direction_criterion_is_skipped_when_exchange_gives_no_usable_direction():
    """`RV-P0-4`：净持仓模式账户上 `direction` 可能是空串 / `net`，严格相等会让轴 A 100% 静默空转。

    这条**必须**是反向门禁：不命中的回落与今天逐字相同，没有任何一个读数会变红，只会显示一个
    没人读得懂的 `ratio=0`。仓库里已有反例——`resume` 的离线回读只按 `posId` 匹配、刻意不用
    `direction`。
    """
    for direction in ("", "net", None):
        assert _match([_row(direction=direction)]) is not None, direction


def test_rejects_opposite_direction_when_exchange_does_give_one():
    """给了可用方向就必须一致——fail-open 只针对「交易所没说」，不是「说了但不一样」。"""
    assert _match([_row(direction="short")]) is None


# ---------- 判据 3：posId（G-A15 下半 + 重复认领） ----------

def test_blank_pos_id_never_matches():
    """空串 `posId` 恒不命中：归一化对缺字段回落空串，而空串永远不在 `claimed` 里——

    只判 `not in claimed` 会把一条**没有身份**的结算行当成合法命中并认领掉。
    """
    assert _match([_row(posId="")]) is None
    assert _match([_row(posId=None)]) is None


def test_already_claimed_pos_id_is_rejected():
    assert _match([_row()], claimed={"p1"}) is None


# ---------- 判据 4/5：两个时间窗（时间已由归一层整除 1000 变成秒） ----------

def test_rejects_settlement_earlier_than_detection():
    """结算不得早于侦测——早于即属**上一笔**往返（容差之外）。"""
    assert _match([_row(uTime=_DETECT_TS - 61)]) is None
    assert _match([_row(uTime=_DETECT_TS - 59)]) is not None


def test_rejects_position_opened_after_local_entry():
    assert _match([_row(cTime=_ENTRY_TS + 61)]) is None
    assert _match([_row(cTime=_ENTRY_TS + 59)]) is not None


def test_an_unknown_local_entry_time_skips_the_open_window_instead_of_rejecting_everything():
    """`entry_ts <= 0`（本地不知道这笔仓何时开的）⇒ 判据 5 整条跳过，而不是拒绝一切。

    这是**接管来的孤儿持仓**的常态：`adopt_orphan` 刻意不伪造开仓腿，故台账里没有它，
    `_entry_wall_ts()` 返回 0。照常比对的话 `cTime <= 0 + tol` 对任何真实时间戳都为假 ⇒ 恒不
    命中 ⇒ 轴 A 对这类持仓 100% 静默空转——而它恰恰是最需要真值的那一类（它本来就是一笔来路
    不明的仓）。与判据 2 的 fail-open 是同一条理由：「不知道」不等于「不匹配」。
    """
    assert _match([_row(cTime=_ENTRY_TS + 99_999)], query=_query(entry_ts=0)) is not None
    # 其余判据一条不减：instId / 时间窗右端 / posId / 0 价照常拦。
    assert _match([_row(instId="ETH-USDT-SWAP")], query=_query(entry_ts=0)) is None
    assert _match([_row(uTime=_DETECT_TS - 999)], query=_query(entry_ts=0)) is None
    assert _match([_row(closeAvgPx=0.0)], query=_query(entry_ts=0)) is None


# ---------- 判据 6：绝不用 0 价入账 ----------

def test_zero_close_price_never_matches():
    """`closeAvgPx` 缺字段时归一层回落 0.0——按它入账正是本轮在治的那类假数字。"""
    assert _match([_row(closeAvgPx=0.0)]) is None
    assert _match([_row(closeAvgPx=None)]) is None


# ---------- 判据 7：多行命中取最早 ----------

def test_picks_the_earliest_settlement_when_several_match():
    early = _row(posId="early", uTime=_DETECT_TS + 1, closeAvgPx=99.0)
    late = _row(posId="late", uTime=_DETECT_TS + 40, closeAvgPx=98.0)
    fact = _match([late, early])
    assert fact is not None and fact.pos_id == "early"


# ---------- wire ----------

def test_meta_wire_omits_keys_it_cannot_compute():
    """算不出即**整键省略**，绝不写 `null`（沿用 `trip_ledger.trip_meta_wire` 的成文纪律）。"""
    wire = settlement_meta_wire(None, mark_px=0.0, waited_s=7, booked_open_fee_usd=float("nan"))
    assert wire["settlementSource"] == "local_mark"
    assert wire["settlementWaitedS"] == 7
    for absent in ("markPxAtDetect", "settlementPosId", "settlementPriceGapBps",
                   "exchangeGrossPnlUsd", "bookedOpenFeeUsd"):
        assert absent not in wire


def test_meta_wire_reports_the_price_gap_in_bps():
    """本轮最重要的一个观测量：把「合成价到底差多少」从推断变成读数。"""
    fact = _match([_row(closeAvgPx=99.0)])
    wire = settlement_meta_wire(fact, mark_px=100.0, waited_s=12, booked_open_fee_usd=0.12)
    assert wire["settlementSource"] == "exchange"
    assert wire["settlementPriceGapBps"] == -100.0        # (99 - 100) / 100 * 1e4
    assert wire["bookedOpenFeeUsd"] == 0.12
    assert wire["exchangeFeeUsd"] == 0.25


def test_meta_wire_carries_the_reject_reason_so_it_is_visible_in_the_data():
    """费率残差不自洽 ⇒ 降级为 `local_mark`，但**原因必须留在数据上**（`G-A16`）。"""
    wire = settlement_meta_wire(None, mark_px=100.0, waited_s=3, booked_open_fee_usd=0.9,
                                reject_reason="fee_residual_negative")
    assert wire["settlementSource"] == "local_mark"
    assert wire["settlementRejectReason"] == "fee_residual_negative"


def test_coverage_wire_is_absent_when_nothing_happened_yet():
    """`G-F3`：两者皆 0 ⇒ 整键省略。

    「一次外部平仓都还没发生」与「发生了但一次都没拿到真值」是两件完全不同的事，用同一个 0
    表示会让 `AC-1` 的分母失去意义。
    """
    assert coverage_wire(0, 0) is None
    assert coverage_wire(3, 1) == {"exchange": 3, "localMark": 1, "ratio": 0.75}


def test_match_never_raises_on_garbage_rows():
    """纯核心**绝不抛**：喂进任何形状的垃圾都只会不命中。"""
    garbage = [None, {}, {"instId": _INST}, {"instId": _INST, "posId": object()},
               {"instId": _INST, "posId": "x", "uTime": "not-a-number"}]
    assert _match(garbage) is None
