"""轴 C · 交易所对账纯核心（ai-trade-cost-truth-and-net-pnl §3.3）。

覆盖 G-9 ~ G-11、G-15（可比口径的资金费夹具）、G-16（`final=True` 零事件）。

**G-15 的存在理由**：v1 的差额式是 `local_net − exchangeRealizedUsd`，两侧口径按定义差一整个资金费，
故任何跨过结算点的持仓都会让 drift 告警按设计恒响。本文件的 `test_funding_fee_does_not_trigger_drift`
在 v1 公式下必红、在 v2 公式（对 `exchangeComparableUsd` 作差）下必绿——这正是它的验收价值。
"""
from __future__ import annotations

import pytest

from app.ai_auto.reconcile import (ReconcileParams, build_reconciliation, drift_message,
                                   local_net_view, resolve_reconcile_params)
from app.config import Settings
from app.okx.trading import _normalize_position_history

_PARAMS = ReconcileParams(abs_tol_usd=0.5, rel_tol=0.05)
_INST = "BTC-USDT-SWAP"


def _row(*, realized: float, fee: float = 0.0, funding: float = 0.0, liq: float = 0.0,
         u_time: int = 2_000, inst_id: str = _INST) -> dict:
    """归一后的持仓历史行（**支出为正**）；`pnl` 由恒等式反推使自检默认通过。"""
    return {"posId": "p1", "instId": inst_id, "direction": "long", "uTime": u_time,
            "cTime": u_time - 100, "pnl": realized + fee + funding + liq,
            "fee": fee, "fundingFee": funding, "liqPenalty": liq,
            "realizedPnl": realized, "identityGapUsd": 0.0}


# ---------- 用例 1：G-10 无样本 ⇒ unavailable，且不报 0 差额 ----------
def test_zero_samples_reports_unavailable_with_a_null_diff():
    out = build_reconciliation(local_net_usd=-8.42, local_trips=3, rows=[],
                               window_start_ts=1_000, params=_PARAMS)
    assert out["status"] == "unavailable"
    assert out["diffUsd"] is None and out["exchangeComparableUsd"] is None
    assert out["samples"] == 0 and out["localTrips"] == 3


def test_rows_before_the_window_and_other_symbols_are_filtered_out():
    rows = [_row(realized=-5.0, u_time=500),                 # 窗口之前
            _row(realized=-5.0, inst_id="ETH-USDT-SWAP")]    # 别的标的
    out = build_reconciliation(local_net_usd=0.0, local_trips=0, rows=rows,
                               window_start_ts=1_000, params=_PARAMS, inst_id=_INST)
    assert out["status"] == "unavailable"


# ---------- 用例 2：ok / drift ----------
def test_small_difference_is_ok():
    out = build_reconciliation(local_net_usd=-8.42, local_trips=1,
                               rows=[_row(realized=-8.30, fee=21.4)],
                               window_start_ts=1_000, params=_PARAMS)
    assert out["status"] == "ok"
    assert out["diffUsd"] == pytest.approx(-0.12)
    assert out["exchangeFeeUsd"] == pytest.approx(21.4)


def test_large_difference_is_drift():
    out = build_reconciliation(local_net_usd=-8.42, local_trips=1,
                               rows=[_row(realized=-20.0)],
                               window_start_ts=1_000, params=_PARAMS)
    assert out["status"] == "drift"
    assert out["diffUsd"] == pytest.approx(11.58)
    assert "非本会话的手工持仓" in drift_message(out, 3)
    assert "已自动" not in drift_message(out, 3)      # 本层只观测，绝不宣称修正过任何数字


# ---------- 用例 3：G-15 资金费不得触发 drift（v1 公式下本用例必红）----------
def test_funding_fee_does_not_trigger_drift():
    """本地净额按定义**不含**资金费，交易所 realizedPnl **含**——必须对可比口径作差。

    把资金费调到远超 `abs_tol`（0.8 USDT）仍应为 `ok`：v1 的 `local − realized` 会得到 0.8 的差额
    并连续 3 轮误报 `AI_TRADE_PNL_RECONCILE_DRIFT`，而那个告警一旦按设计恒响，就等于没有告警。
    """
    out = build_reconciliation(local_net_usd=-8.30, local_trips=1,
                               rows=[_row(realized=-9.10, funding=0.8)],
                               window_start_ts=1_000, params=_PARAMS)
    assert out["exchangeRealizedUsd"] == pytest.approx(-9.10)   # 原始读数照发
    assert out["exchangeComparableUsd"] == pytest.approx(-8.30)  # 把不记的两项加回去
    assert out["exchangeFundingFeeUsd"] == pytest.approx(0.8)
    assert out["diffUsd"] == pytest.approx(0.0)
    assert out["status"] == "ok"


def test_liquidation_penalty_is_added_back_too():
    out = build_reconciliation(local_net_usd=-8.30, local_trips=1,
                               rows=[_row(realized=-10.30, funding=0.5, liq=1.5)],
                               window_start_ts=1_000, params=_PARAMS)
    assert out["exchangeLiqPenaltyUsd"] == pytest.approx(1.5)
    assert out["status"] == "ok"


# ---------- 用例 4：coverageMismatch 照发差额但打标 ----------
def test_coverage_mismatch_is_flagged_yet_still_reports_the_difference():
    out = build_reconciliation(local_net_usd=-8.30, local_trips=7,
                               rows=[_row(realized=-8.30), _row(realized=0.0)],
                               window_start_ts=1_000, params=_PARAMS)
    assert out["coverageMismatch"] is True and out["samples"] == 2
    assert out["diffUsd"] == pytest.approx(0.0) and out["status"] == "ok"


# ---------- 用例 5：G-16 终态对账永不判 drift ----------
def test_final_reconciliation_downgrades_to_partial_and_never_drifts():
    rows = [_row(realized=-20.0)]
    live = build_reconciliation(local_net_usd=-8.42, local_trips=2, rows=rows,
                                window_start_ts=1_000, params=_PARAMS)
    final = build_reconciliation(local_net_usd=-8.42, local_trips=2, rows=rows,
                                 window_start_ts=1_000, params=_PARAMS, final=True)
    assert live["status"] == "drift"          # 同一组入参，盯市轮判漂移
    assert final["status"] == "partial"       # 终态那一次系统性少最后一笔 ⇒ 绝不呈现为伪 drift
    assert final["final"] is True and final["diffUsd"] == live["diffUsd"]


def test_final_reconciliation_with_full_coverage_is_ok_even_when_far_off():
    out = build_reconciliation(local_net_usd=-8.42, local_trips=1, rows=[_row(realized=-20.0)],
                               window_start_ts=1_000, params=_PARAMS, final=True)
    assert out["status"] == "ok"              # 覆盖齐了就不降级；但仍不判 drift


# ---------- 用例 6：G-11 恒等式运行期自检 ----------
def test_identity_self_check_flags_a_constructed_violation_without_touching_the_diff():
    bad = {**_row(realized=-8.30), "identityGapUsd": 3.0}
    out = build_reconciliation(local_net_usd=-8.30, local_trips=1, rows=[bad],
                               window_start_ts=1_000, params=_PARAMS)
    assert out["identityHolds"] is False and out["identityMaxGapUsd"] == pytest.approx(3.0)
    assert out["diffUsd"] == pytest.approx(0.0)     # 自检不影响差额计算
    assert out["status"] == "ok"


def test_identity_self_check_passes_on_consistent_rows():
    out = build_reconciliation(local_net_usd=0.0, local_trips=1, rows=[_row(realized=-8.30)],
                               window_start_ts=1_000, params=_PARAMS)
    assert out["identityHolds"] is True


# ---------- 用例 7：归一的符号约定（支出为正）----------
def test_normalization_flips_the_expense_signs_and_derives_the_identity_gap():
    row = _normalize_position_history({
        "posId": "p", "instId": _INST, "direction": "long", "cTime": "1754400000000",
        "uTime": "1754431200000", "pnl": "-6.0", "fee": "-2.0", "fundingFee": "-0.3",
        "liqPenalty": "0", "settledPnl": "0", "realizedPnl": "-8.3",
        "openAvgPx": "1", "closeAvgPx": "2"})
    assert row["fee"] == 2.0 and row["fundingFee"] == 0.3      # 负数=支出 ⇒ 归一为正
    assert row["uTime"] == 1754431200                          # 毫秒 ⇒ 秒
    assert row["identityGapUsd"] == pytest.approx(0.0)


# ---------- 用例 8：P0-2 本地视图恒取「已平仓往返净额」 ----------
def test_local_net_view_reconstructs_net_pnl_when_the_switch_is_off():
    trades = [{"side": "long", "fee": 1.0}, {"side": "close", "fee": 1.0, "pnl": 10.0},
              {"side": "short", "fee": 1.0}, {"side": "close", "fee": 1.0, "pnl": -4.0}]
    trades = _with_quantity_and_source(trades)
    # 毛额行按 `pnl − Σ腿费` 还原：(10−2) + (−4−2) = 2.0；净额行直接相加 = 6.0。
    assert local_net_view(trades, net_mode=False) == {"netUsd": 2.0, "trips": 2}
    assert local_net_view(trades, net_mode=True) == {"netUsd": 6.0, "trips": 2}


def test_local_net_view_ignores_a_dangling_open_leg():
    """未平仓的开仓腿绝不进净额：`positions-history` 只覆盖**已结算**持仓，混入即结构性偏差。"""
    trades = [{"side": "long", "fee": 1.0}, {"side": "close", "fee": 1.0, "pnl": 10.0},
              {"side": "long", "fee": 1.0}]
    trades = _with_quantity_and_source(trades)
    assert local_net_view(trades, net_mode=False)["trips"] == 1
    assert local_net_view(trades, net_mode=False)["netUsd"] == pytest.approx(8.0)


def _with_quantity_and_source(trades):
    return [{**trade, "id": index + 1, "qty": 1, "price": 100,
             "feeKnown": True, "feeBasis": "measured"}
            for index, trade in enumerate(trades)]


def test_params_resolve_from_settings():
    params = resolve_reconcile_params(Settings())
    assert (params.abs_tol_usd, params.rel_tol) == (0.5, 0.05)
