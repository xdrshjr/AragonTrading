"""下单前复检按重要性判定（ai-trade-profit-lock-and-entry-unblock 轴 A，spec §7.1）。

A-T1 是本轮缺陷的复现用例：生产最新组 181 次开仓里 172 次被 `pre_submit_context_changed` 丢弃，
根因是资金费率每 ~60s 刷新一次而旧判据逐位比较费率。
"""
from __future__ import annotations

import re
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.ai_auto.cost_runtime import CostRuntime
from app.ai_auto.decider import TradeDecision
from app.ai_auto.funding import FundingSnapshot
from app.ai_auto.open_funnel import EXECUTION_REASONS
from app.ai_auto.pre_submit import (
    REASON_COST,
    REASON_DRIFT,
    PreSubmitRuntime,
    adverse_drift,
    cost_materiality,
    drift_limit_bps,
    identity_readings,
)
from app.ai_auto.session import AiTradeSession
from app.config import Settings
from app.live_auto.fee_ledger import FeeLedger, resolve_fee_params

_REPO = Path(__file__).resolve().parents[3]
_NOW = 1_000.0


def _open(action: str = "open_long", ord_type: str = "market") -> TradeDecision:
    return TradeDecision(action, 0.5, 0.8, "测试", ord_type=ord_type)


def _runtime(monkeypatch, rate: float = 3.47233e-5, **settings) -> CostRuntime:
    monkeypatch.setattr("app.ai_auto.cost_runtime.time.time", lambda: _NOW)
    cfg = Settings(**settings)
    runtime = CostRuntime("SOL-USDT-SWAP", cfg, FeeLedger(ct_val=1, params=resolve_fee_params(cfg)))
    runtime.funding._snapshot = FundingSnapshot(rate, 0, 28_800, 28_800, _NOW - 1, True, "exchange")
    runtime.begin_round_cost()
    return runtime


def test_funding_rate_tick_does_not_veto_open(monkeypatch):
    """A-T1：推理期间资金费率刷新（3.47e-5 → 3.45e-5）⇒ 旧判据拦、新判据放行。"""
    runtime = _runtime(monkeypatch)
    runtime.funding._snapshot = replace(runtime.funding._snapshot, rate=3.45148e-5)
    assert runtime.pre_submit_context_changed() is True          # 旧判据：随机开关
    gate = PreSubmitRuntime(runtime.settings)
    verdict = gate.check(runtime, _open(), bar_seconds=1800, ref_px=100.0, now_px=100.0,
                         atr_px=None)
    assert verdict.code is None
    assert gate.last is None and gate.cost_vetoes == 0


def test_material_cost_rise_still_vetoes(monkeypatch):
    """A-T2：taker 从 5 升到 7 bps（开平两腿 ⇒ +4 bps）⇒ 超出 1 bps 容忍 ⇒ 拦，读数齐全。"""
    runtime = _runtime(monkeypatch, ai_trade_taker_fee_bps=5.0)
    runtime.fee_ledger.retune(maker_bps=2.0, taker_bps=7.0, source="exchange")
    gate = PreSubmitRuntime(runtime.settings)
    verdict = gate.check(runtime, _open(), bar_seconds=1800, ref_px=100.0, now_px=100.0,
                         atr_px=None)
    assert verdict.code == REASON_COST
    readings = gate.last
    assert readings["kind"] == "cost"
    assert readings["deltaBps"] == pytest.approx(4.0, abs=0.05)
    assert {"frozenBps", "currentBps", "toleranceBps"} <= set(readings)
    assert gate.cost_vetoes == 1


def test_cost_drop_never_vetoes():
    """A-T3。"""
    assert cost_materiality(12.0, 3.0, tolerance_bps=0.0).code is None


def test_unknown_cost_fails_open():
    """A-T4：任一侧不可算 ⇒ 放行，读数标 costUnknown。"""
    for frozen, current in ((None, 10.0), (10.0, None), (float("nan"), 10.0)):
        verdict = cost_materiality(frozen, current, tolerance_bps=1.0)
        assert verdict.code is None and verdict.readings["costUnknown"] is True


def test_stale_flip_alone_does_not_veto(monkeypatch):
    """A-T5：费率档位在推理期间过期不单独触发（成本没变就不拦）。"""
    runtime = _runtime(monkeypatch)
    monkeypatch.setattr(runtime.fee_rates, "wire", lambda *_: {"stale": True})
    gate = PreSubmitRuntime(runtime.settings)
    assert gate.check(runtime, _open(), bar_seconds=1800, ref_px=100.0, now_px=100.0,
                      atr_px=None).code is None


def test_market_open_adverse_drift_vetoes():
    """A-T6：long，ref 100 → now 100.3（30 bps > 20）⇒ 放弃追单。"""
    verdict = adverse_drift(side="long", ref_px=100.0, now_px=100.3, limit_bps=20.0)
    assert verdict.code == REASON_DRIFT
    assert verdict.readings["driftBps"] == pytest.approx(30.0)
    assert adverse_drift(side="short", ref_px=100.0, now_px=99.7, limit_bps=20.0).code == REASON_DRIFT


def test_favorable_drift_passes():
    """A-T7。"""
    assert adverse_drift(side="long", ref_px=100.0, now_px=99.0, limit_bps=20.0).code is None
    assert adverse_drift(side="short", ref_px=100.0, now_px=101.0, limit_bps=20.0).code is None
    assert adverse_drift(side="long", ref_px=100.0, now_px=None, limit_bps=20.0).code is None


def test_limit_open_skips_drift(monkeypatch):
    """A-T8：限价单的价格边界本身就是保护，不做漂移复检。"""
    runtime = _runtime(monkeypatch)
    gate = PreSubmitRuntime(runtime.settings)
    limit = gate.check(runtime, _open(ord_type="limit"), bar_seconds=1800, ref_px=100.0,
                       now_px=105.0, atr_px=None)
    market = gate.check(runtime, _open(), bar_seconds=1800, ref_px=100.0, now_px=105.0,
                        atr_px=None)
    assert limit.code is None and market.code == REASON_DRIFT
    assert gate.drift_vetoes == 1


def test_drift_limit_uses_atr_when_larger():
    """A-T9：阈值 = max(min_bps, atr_mult × ATR bps)。"""
    assert drift_limit_bps(min_bps=20, atr_mult=0.5, atr_bps=60) == pytest.approx(30)
    assert drift_limit_bps(min_bps=20, atr_mult=0.5, atr_bps=10) == 20
    assert drift_limit_bps(min_bps=20, atr_mult=0.5, atr_bps=None) == 20


def _fake_session(cost, *, enabled: bool, identity=(1, "flat", 0, None), frozen=None):
    gate = PreSubmitRuntime(Settings(ai_trade_pre_submit_materiality_enabled=enabled))
    return SimpleNamespace(
        _pre_submit=gate, _round_facts={}, _round_execution_identity=frozen,
        _execution_identity=lambda: identity, _trade_persist_failed=False,
        _trade_backlog_persist_failed=False, _execution_state_persist_failed=False,
        _cost=cost, bar="30m", _current_px=lambda: 100.0, _atr_px=None,
        executor=SimpleNamespace())


_SCENARIOS = [
    {"rate": r} for r in (3.47233e-5, 3.45148e-5, 1e-4, -2e-5, 0.0)
] + [
    {"interval_s": s} for s in (3600, 14_400, 28_800)
] + [
    {"next_funding_ts": t} for t in (7200, 28_800, 57_600)
] + [
    {"interval_known": k} for k in (True, False)
] + [
    {"funding_ts": t} for t in (0, 100)
] + [
    {"fetched_wall": w} for w in (_NOW - 1, _NOW - 5, _NOW - 30)
] + [{"rate": 2e-4, "interval_s": 3600}, {"rate": 3.47233e-5, "fetched_wall": _NOW}]


@pytest.mark.parametrize("change", _SCENARIOS)
def test_materiality_switch_off_is_byte_identical(monkeypatch, change):
    """A-T10：总闸关 ⇒ 与旧判据逐次同判（20 组断面）。"""
    runtime = _runtime(monkeypatch)
    runtime.funding._snapshot = replace(runtime.funding._snapshot, **change)
    fake = _fake_session(runtime, enabled=False)
    expected = "pre_submit_context_changed" if runtime.pre_submit_context_changed() else None
    assert AiTradeSession._prepare_open_context(fake, _open(), 100.0) == expected


def test_identity_change_still_vetoes(monkeypatch):
    """A-T11：持仓序变化 ⇒ 仍返回旧码，且读数说清是「身份」变了。"""
    runtime = _runtime(monkeypatch)
    fake = _fake_session(runtime, enabled=True, identity=(2, "flat", 0, None),
                         frozen=(1, "flat", 0, None))
    assert AiTradeSession._prepare_open_context(fake, _open(), 100.0) == REASON_COST
    assert fake._pre_submit.last == {"kind": "identity", "changed": ["positionSeq"]}
    assert fake._pre_submit.identity_vetoes == 1


def test_identity_readings_lists_every_changed_slot():
    assert identity_readings((1, "long", 2, "a"), (1, "short", 2, "b"))["changed"] == [
        "side", "restingClOrdId"]


def test_funnel_registers_new_reason():
    """A-T12：开仓漏斗、后端标签表、前端标签表三处都登记了 pre_submit_price_drift。"""
    from app.ai_auto.integrity_view import _EXECUTION_MESSAGES

    assert REASON_DRIFT in EXECUTION_REASONS
    assert REASON_DRIFT in _EXECUTION_MESSAGES
    front = (_REPO / "apps/web/lib/aiTradeIntegrity.ts").read_text(encoding="utf-8")
    assert re.search(r"\bpre_submit_price_drift:\s*\"", front)
