"""终态对账不得无条件报「已对上」（ai-trade-fill-truth-and-effective-controls 轴 F）。

门禁 `G-F1`（**合入前必须红**）、`G-F2`（闸关逐字回退）、`G-F3`（覆盖率整键省略）、
`G-F4`（三条成因 + 不含字面 `**`）、`G-A17`（`RV-P1-12` 的端到端反向断言：命中结算后差额收敛
到容差以内——缺了它，轴 A 可以全部单测通过而在生产上不改善任何东西）。
"""
from __future__ import annotations

import pytest

from app.ai_auto.reconcile import (
    ReconcileParams,
    build_reconciliation,
    drift_message,
    local_net_view,
    resolve_reconcile_params,
)
from app.config import Settings

_INST = "BTC-USDT-SWAP"


def _row(**over) -> dict:
    base = {"posId": "p1", "instId": _INST, "uTime": 1_788_285_000,
            "realizedPnl": -5.0, "pnl": -4.5, "fee": 0.5,
            "fundingFee": 0.0, "liqPenalty": 0.0, "identityGapUsd": 0.0}
    base.update(over)
    return base


def _build(local_net: float, *, trips: int = 1, final: bool = True, strict: bool = True,
           rows=None, **over):
    params = ReconcileParams(abs_tol_usd=0.5, rel_tol=0.05, strict_final=strict,
                             capital_tol_ratio=over.pop("capital_tol_ratio", 0.0))
    return build_reconciliation(local_net_usd=local_net, local_trips=trips,
                                rows=rows if rows is not None else [_row()],
                                window_start_ts=0, params=params, final=final,
                                inst_id=_INST, **over)


# ---------- G-F1（合入前必须红）：样本对齐却对不上账，就是对不上账 ----------

def test_final_reconciliation_reports_drift_when_the_samples_line_up_but_the_money_does_not():
    """生产实测 6 条记录顶着绿色「已对上」，而同一行正文写着差额，合计 7.41 USDT。

    原先「终态永不判 drift」的理由（刚平的那一笔结算延迟 ⇒ 终态会系统性少一笔）是对的，但
    「少一笔」的观测特征恰恰是 `coverageMismatch`，它已经由 `partial` 分支单独接住——那 6 条
    **全部 `mismatch=False`**。这是一次「为了消除伪阳性把真阳性一起消掉」的过度收敛。
    """
    rec = _build(-3.0)                                   # 交易所可比口径 -5.0，差 2.0 > 容差 0.5
    assert rec["status"] == "drift"
    assert rec["coverageMismatch"] is False
    assert rec["diffUsd"] == pytest.approx(2.0)


def test_a_genuinely_missing_sample_is_still_partial_not_drift():
    """伪阳性由既有的 `partial` 分支接住——本轮一个字节都没动它。"""
    assert _build(-3.0, trips=2)["status"] == "partial"


def test_within_tolerance_is_still_ok():
    assert _build(-5.2)["status"] == "ok"


# ---------- G-F2：闸关 ⇒ 逐字回退 ----------

def test_gate_off_restores_the_unconditional_ok():
    rec = _build(-3.0, strict=False)
    assert rec["status"] == "ok"
    assert _build(-3.0, trips=2, strict=False)["status"] == "partial"


def test_non_final_reconciliation_is_untouched_by_the_gate():
    """盘中对账的判定与今天逐字相同（本轴只改 `final` 那一支）。"""
    for strict in (True, False):
        assert _build(-3.0, final=False, strict=strict)["status"] == "drift"
        assert _build(-5.2, final=False, strict=strict)["status"] == "ok"


def test_default_settings_arm_the_strict_verdict_but_not_the_capital_tolerance():
    params = resolve_reconcile_params(Settings())
    assert params.strict_final is True and params.capital_tol_ratio == 0.0


# ---------- 容差与它的下发 ----------

def test_tolerance_is_published_so_the_ok_wording_can_answer_why():
    """`tolUsd` 是加法键：没有它，前端的 `ok` 措辞只能含糊说「在容差内」而说不出容差是多少。"""
    assert _build(-5.2)["tolUsd"] == pytest.approx(0.5)


def test_capital_tolerance_is_a_no_op_at_its_default():
    """`capital_tol_ratio = 0` ⇒ 容差公式逐字等于今天（不与 F-1 同时收紧两个判据）。"""
    assert _build(-5.2, capital_tol_ratio=0.0, capital_usd=100.0)["tolUsd"] == pytest.approx(0.5)


def test_capital_tolerance_tightens_the_floor_when_armed():
    """100 USDT 本金上 `abs_tol_usd=0.5` 就是 0.5% 的本金，偏宽。"""
    rec = _build(-5.2, capital_tol_ratio=0.001, capital_usd=100.0)
    assert rec["tolUsd"] == pytest.approx(0.25)          # max(0.05×5.0, min(0.5, 0.1)) = 0.25


# ---------- G-F3：结算覆盖率整键省略 ----------

def test_settlement_coverage_is_absent_unless_it_has_something_to_say():
    assert "settlementCoverage" not in _build(-5.2)
    rec = _build(-5.2, settlement={"exchange": 9, "localMark": 1, "ratio": 0.9})
    assert rec["settlementCoverage"]["ratio"] == 0.9


# ---------- G-F4：三条成因 ----------

def test_drift_message_names_three_causes_in_the_order_this_round_measured():
    rec = _build(-3.0, settlement={"exchange": 1, "localMark": 4, "ratio": 0.2})
    text = drift_message(rec, 3)
    assert "① 外部平仓按本地标记价入账" in text and "本会话已有 4 笔" in text
    assert "② 交易所侧存在未接管持仓" in text
    assert "③ 该标的上存在非本会话的手工持仓" in text
    assert "容差" in text
    assert "**" not in text                              # 后端文案在 JSX 里是纯文本节点


def test_drift_message_survives_a_missing_coverage_block():
    text = drift_message(_build(-3.0), 3)
    assert "① 外部平仓按本地标记价入账" in text and "**" not in text


# ---------- G-A17（RV-P1-12）：端到端反向断言，`AC-2` 的离线代理 ----------

def test_booking_the_exchange_settlement_makes_the_final_difference_converge():
    """轴 A 有效性的**唯一**硬判据：命中结算后差额收敛到容差以内。

    `G-A1..G-A16` 全是单元级的；缺了这一条，轴 A 可以全部单测通过而在生产上不改善任何东西。
    本用例走完整条链：成交流 → `local_net_view` → `build_reconciliation`。
    """
    exchange = _row(realizedPnl=-5.056, pnl=-4.56, fee=0.496)
    # ① 今天：外部平仓按侦测那一刻的 mark_px 合成（生产实测 run 1627 就是这个数）
    today = [{"side": "long", "pnl": 0.0, "fee": 0.10},
             {"side": "close", "pnl": -0.871, "fee": 0.10}]
    view_today = local_net_view(today, net_mode=True)
    rec_today = build_reconciliation(
        local_net_usd=view_today["netUsd"], local_trips=view_today["trips"], rows=[exchange],
        window_start_ts=0, params=ReconcileParams(0.5, 0.05, strict_final=True),
        final=True, inst_id=_INST)
    assert rec_today["status"] == "drift"                # 今天这条链给出的就是 drift

    # ② 本轮：价格取 closeAvgPx、pnl 取交易所毛额减往返总费、fee 取残差
    fixed = [{"side": "long", "pnl": 0.0, "fee": 0.10},
             {"side": "close", "pnl": -4.56 - 0.496, "fee": 0.496 - 0.10}]
    view_fixed = local_net_view(fixed, net_mode=True)
    rec_fixed = build_reconciliation(
        local_net_usd=view_fixed["netUsd"], local_trips=view_fixed["trips"], rows=[exchange],
        window_start_ts=0, params=ReconcileParams(0.5, 0.05, strict_final=True),
        final=True, inst_id=_INST, settlement={"exchange": 1, "localMark": 0, "ratio": 1.0})
    assert abs(rec_fixed["diffUsd"]) <= rec_fixed["tolUsd"]
    assert rec_fixed["status"] == "ok"
    assert rec_fixed["settlementCoverage"]["ratio"] == 1.0
