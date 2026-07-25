"""滚动往返归因与统计合并单测（ai-trade-cost-accounting-and-edge-gate §7.2）。

两条口径钉子：
- 用例 2：`net_mode=True` 时 `trade["pnl"]` **已经是净额**，毛额须由 `pnl + fee` 反推——搞反会让
  归因表在切换口径的那一刻整体偏移一个往返成本；
- 用例 3（RV-13）：`closeFeeCoverage` 的分母**只数平仓样本**，开仓腿的零费率不该拉低它。
"""
from __future__ import annotations

import pytest

from app.ai_auto.attribution import AttributionLedger
from app.ai_auto.stats_ext import build_cost_stats, cost_snapshot_wire, merge_cost_stats


def _close(pnl: float, fee: float, ts: int = 1) -> dict:
    return {"ts": ts, "side": "close", "price": 100.0, "qty": 1.0, "fee": fee, "pnl": pnl,
            "ordType": "market"}


def test_gross_mode_derives_net_by_subtracting_fee():
    """用例 1：三笔往返（+10 / −5 / +2 毛，各 1 费）⇒ 净 4.0、期望 1.333、成本占比 3/17。"""
    ledger = AttributionLedger()
    for pnl in (10.0, -5.0, 2.0):
        ledger.record_close(_close(pnl, 1.0))
    cost = ledger.stats()["cost"]
    assert cost["roundTrips"] == 3
    assert cost["grossPnlUsd"] == pytest.approx(7.0)
    assert cost["netPnlUsd"] == pytest.approx(4.0)
    assert cost["netExpectancyUsd"] == pytest.approx(4.0 / 3)
    assert cost["costRatio"] == pytest.approx(round(3 / 17, 4))


def test_net_mode_derives_gross_by_adding_fee_back():
    """用例 2：A0-2 开启后 `pnl` 已是净额 ⇒ 毛额 = pnl + fee（绝不重复扣费）。"""
    ledger = AttributionLedger(net_mode=True)
    ledger.record_close(_close(9.0, 1.0))
    cost = ledger.stats()["cost"]
    assert cost["netPnlUsd"] == pytest.approx(9.0)
    assert cost["grossPnlUsd"] == pytest.approx(10.0)


def test_close_fee_coverage_counts_close_samples_only():
    """用例 3（RV-13）：零费率的**平仓**样本拉低覆盖率；零费率的**开仓**样本完全不进分母。"""
    ledger = AttributionLedger()
    ledger.record_close(_close(1.0, 0.5))
    assert ledger.stats()["cost"]["closeFeeCoverage"] == 1.0
    ledger.record_close(_close(1.0, 0.0))
    assert ledger.stats()["cost"]["closeFeeCoverage"] == 0.5
    # 开仓腿根本不走 record_close（`session._dispatch` 只在 side=="close" 时挂点）⇒ 指标不变。
    assert ledger.stats()["cost"]["roundTrips"] == 2


def test_t_stat_needs_at_least_two_samples_and_nonzero_variance():
    """用例 4：n<2 ⇒ None（不造假显著性）；零方差同样 None。"""
    ledger = AttributionLedger()
    ledger.record_close(_close(1.0, 0.1))
    assert ledger.stats()["cost"]["tStat"] is None
    ledger.record_close(_close(1.0, 0.1))
    assert ledger.stats()["cost"]["tStat"] is None      # 两笔完全相同 ⇒ 方差 0
    ledger.record_close(_close(5.0, 0.1))
    assert ledger.stats()["cost"]["tStat"] is not None


def test_ring_buffer_evicts_the_oldest_round_trip():
    """用例 5：环形容量 200 ⇒ 第 201 笔挤掉第 1 笔。"""
    ledger = AttributionLedger(capacity=200)
    for i in range(201):
        ledger.record_close(_close(1.0, 0.1, ts=i))
    assert ledger.stats()["cost"]["roundTrips"] == 200


def test_gate_counters_and_conversion_rate():
    ledger = AttributionLedger()
    ledger.note_gate("pass", cost_bps=13.0, edge_bps=20.0)
    ledger.note_gate("veto", cost_bps=13.0, edge_bps=1.0)
    ledger.note_gate("unavailable")
    for executed in (True, False, False, False):
        ledger.note_round(executed)
    stats = ledger.stats()
    assert stats["edgeGate"] == {"pass": 1, "veto": 1, "unavailable": 1, "vetoRate": 0.3333}
    assert stats["decisionConversionRate"] == 0.25
    assert stats["cost"]["modelCostBps"] is None        # 最近一次是 unavailable ⇒ 无读数


def test_empty_ledger_reports_none_not_fake_zeros():
    stats = AttributionLedger().stats()
    assert stats["cost"]["closeFeeCoverage"] is None
    assert stats["cost"]["netExpectancyUsd"] is None
    assert stats["edgeGate"]["vetoRate"] is None
    assert stats["decisionConversionRate"] is None


# ---------- 用例 6（RV-1）：与既有 aiStats 的加法合并 ----------
_EXISTING_AI_STATS = {
    "decisionRounds": 12, "actionCounts": {"hold": 9}, "holdRate": 0.75,
    "avgConfidence": 0.62, "degradedRounds": 2, "reflectionRuns": 3,
    "reflectionConfirms": 2, "reflectionAborts": 1, "reflectionFailClosed": 0,
    "wakeSkips": 4, "maxHoldStreak": 6, "profitGuardExits": 1, "profitGuardArmedRounds": 3,
}


def test_merge_keeps_every_existing_key_bit_identical():
    ledger = AttributionLedger()
    ledger.record_close(_close(1.0, 0.1))
    merged = merge_cost_stats(_EXISTING_AI_STATS, build_cost_stats(ledger.stats(), None))
    for key, value in _EXISTING_AI_STATS.items():
        assert merged[key] == value
    assert set(merged) - set(_EXISTING_AI_STATS) == {"cost", "edgeGate",
                                                     "decisionConversionRate"}


def test_merge_raises_on_key_clash_instead_of_silently_overwriting():
    with pytest.raises(ValueError, match="holdRate"):
        merge_cost_stats(_EXISTING_AI_STATS, {"holdRate": 0.0})


def test_build_cost_stats_folds_in_fee_ledger_audit_readings():
    class _Ledger:
        @staticmethod
        def audit_stats():
            return {"measuredFeeBps": 5.4, "feeDeviationBps": 0.4, "slipBps": 1.9,
                    "feeAuditSamples": 24, "feeAuditSkipped": 0, "feeRebateSamples": 0}

    stats = build_cost_stats(AttributionLedger().stats(), _Ledger())
    assert stats["cost"]["measuredFeeBps"] == 5.4
    assert stats["cost"]["feeAuditSamples"] == 24
    # 账本缺席（未装配的会话）⇒ 实测键一律不出现，其余读数照常。
    assert "measuredFeeBps" not in build_cost_stats(AttributionLedger().stats(), None)["cost"]


def test_cost_snapshot_wire_throttles_to_every_tenth_round():
    ledger = AttributionLedger()
    assert cost_snapshot_wire(1, ledger.stats(), None) is None
    assert cost_snapshot_wire(9, ledger.stats(), None) is None
    assert cost_snapshot_wire(10, ledger.stats(), None) is not None
    assert cost_snapshot_wire(20, ledger.stats(), None) is not None
    assert cost_snapshot_wire(0, ledger.stats(), None) is None


def test_round_trip_notional_scales_by_the_contract_multiplier():
    """名义额 = 价 × 张数 × `ctVal`。

    漏掉合约乘数不会让任何**当前**读数出错（`stats()` 尚未消费该字段），但会在账本里存下一个差
    一个数量级的「USD」——BTC-USDT-SWAP 的 `ctVal=0.01` 即高估 100 倍。可证伪写法：断言它**不是**
    `price × qty`。
    """
    ledger = AttributionLedger(ct_val=0.01)
    ledger.record_close(_close(1.0, 0.1))
    trip = ledger._trips[-1]
    assert trip.notional_usd == pytest.approx(100.0 * 1.0 * 0.01)
    assert trip.notional_usd != pytest.approx(100.0 * 1.0)
