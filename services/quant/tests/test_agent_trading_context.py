"""自动交易上下文只允许可靠身份匹配后的白名单。"""

from types import SimpleNamespace


def test_context_identity_is_required_and_private_fields_never_escape():
    from app.agent_trading.auto_context import project_context

    identity = SimpleNamespace(
        account_id=7, environment="demo", credential_fingerprint="fingerprint"
    )
    raw = {"accountId": 7, "state": "paused", "lastDecision": "SECRET"}
    assert (
        project_context(raw, identity)["contextUnavailableReason"]
        == "CONTEXT_IDENTITY_UNAVAILABLE"
    )
    raw.update(
        identity={
            "accountId": 7,
            "environment": "demo",
            "credentialFingerprint": "fingerprint",
        },
        lastDecision={"action": "hold", "reason": "等待", "thinking": "SECRET"},
    )
    result = project_context(raw, identity)
    assert result["lastDecision"]["action"] == "hold"
    assert "SECRET" not in str(result)
    assert "fingerprint" not in str(result)


def test_context_projects_actual_decision_gate_evidence():
    from app.agent_trading.auto_context import project_context

    identity = SimpleNamespace(
        account_id=7, environment="demo", credential_fingerprint="proof"
    )
    raw = {
        "identity": {
            "accountId": 7,
            "environment": "demo",
            "credentialFingerprint": "proof",
        },
        "members": [
            {
                "instId": "BTC-USDT-SWAP",
                "decisions": [
                    {
                        "action": "open_long",
                        "ts": 100,
                        "round": 4,
                        "executed": {
                            "ok": False,
                            "skipped": True,
                            "reason": "consensus",
                        },
                        "degradeReason": "evidence_gate",
                        "thinking": "SECRET",
                    }
                ],
            }
        ],
    }
    decision = project_context(raw, identity)["members"][0]["lastDecision"]
    assert "consensus" in decision["gateReasons"]
    assert "evidence_gate" in decision["gateReasons"]
    assert decision["round"] == 4
    assert "SECRET" not in str(decision)
