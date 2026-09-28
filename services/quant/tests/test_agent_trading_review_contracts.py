"""发送边界证据的安全契约。"""
import time
from dataclasses import replace

import pytest

from app.agent_trading.dispatch_guard import DispatchEvidence, DispatchGuard
from app.okx.order_builder import InstrumentMeta


def make_guard():
    payload = {"instId": "BTC-USDT-SWAP", "side": "buy", "sz": "2",
               "tdMode": "cross", "ordType": "market", "clOrdId": "agt1"}
    preview = {"payload": payload, "referencePrice": "100", "posMode": "net_mode"}
    guard = DispatchGuard(preview, time.time() + 60, lambda: True)
    guard.observed_at = time.monotonic()
    return guard, DispatchEvidence(payload, "net_mode", 100, time.monotonic())


def test_actual_payload_cannot_change_after_preview():
    guard, evidence = make_guard()
    with pytest.raises(Exception, match="预览"):
        guard.validate(replace(evidence, payload={**evidence.payload, "sz": "3"}))


def test_oldest_evidence_window_includes_slot_wait():
    guard, evidence = make_guard()
    guard.observed_at -= 6
    with pytest.raises(Exception, match="过期"):
        guard.validate(evidence)


def test_market_drift_and_missing_position_mode_rejected():
    guard, evidence = make_guard()
    with pytest.raises(Exception):
        guard.validate(replace(evidence, ticker=101))
    with pytest.raises(Exception):
        guard.validate(replace(evidence, pos_mode=""))


def test_limit_does_not_use_ticker_drift():
    guard, evidence = make_guard()
    payload = {**evidence.payload, "ordType": "limit", "px": "100"}
    guard.preview["payload"] = payload
    guard.validate(replace(evidence, payload=payload, ticker=200))


def test_changed_contract_value_rejects_even_when_payload_identical():
    guard, evidence = make_guard()
    guard.preview["instrumentConstraints"] = {
        "ctVal": "0.01", "ctValCcy": "BTC", "lotSz": "1",
        "minSz": "1", "tickSz": "0.1"}
    changed = InstrumentMeta(0.02, "BTC", 1, 1, 0.1, 100, "linear", "USDT")
    with pytest.raises(Exception):
        guard.validate(replace(evidence, meta=changed))
