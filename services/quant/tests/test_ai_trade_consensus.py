"""E2 · 策略共识度 + 共识门禁单测（ai-auto-trading-enhancements v4 §2.3 / §7.1）。

`compute_consensus`：计票 / 净立场 / 一致度（全多、多空各半→net=flat、空板→total=0）。
`AiTradeSession._apply_consensus_gate`：min_consensus=0 恒放行；≥阈值放行；<阈值把 open_* 降级 hold；
`close`/`hold` 任何阈值都不被拦（防「该止损时不平仓」）。纯离线（无网络、无 DB）。
"""
from __future__ import annotations

from types import SimpleNamespace

from app.ai_auto.decider import TradeDecision
from app.ai_auto.session import AiTradeSession
from app.ai_auto.signals import StrategySignalView, compute_consensus
from app.config import Settings
from app.engine.simulator import RiskParams


def _view(stance: str) -> StrategySignalView:
    return StrategySignalView(strategy_id=1, name="s", stance=stance, latest_signal="none",
                              strength=0.0, reason="", risk_params=RiskParams())


def test_all_long_consensus():
    c = compute_consensus([_view("long"), _view("long"), _view("long")])
    assert c.long_votes == 3 and c.short_votes == 0 and c.flat_votes == 0
    assert c.total == 3 and c.net_stance == "long" and c.agreement == 1.0


def test_even_split_is_flat():
    c = compute_consensus([_view("long"), _view("long"), _view("short"), _view("short")])
    assert c.net_stance == "flat"
    assert c.agreement == 0.5


def test_empty_board():
    c = compute_consensus([])
    assert c.total == 0 and c.agreement == 0.0 and c.net_stance == "flat"


def _gate(need: int, decision: TradeDecision, board_stances: list[str], degrade=None):
    host = SimpleNamespace(executor=SimpleNamespace(
        settings=Settings(ai_trade_min_consensus=need)))
    consensus = compute_consensus([_view(s) for s in board_stances])
    return AiTradeSession._apply_consensus_gate(host, decision, consensus, degrade)


def test_gate_disabled_passes_open_through():
    dec = TradeDecision("open_long", 0.5, 0.7, "r")
    out, degrade = _gate(0, dec, ["long"])
    assert out.action == "open_long" and degrade is None


def test_gate_allows_open_at_threshold():
    dec = TradeDecision("open_long", 0.5, 0.7, "r")
    out, degrade = _gate(2, dec, ["long", "long"])       # long_votes=2 >= 2
    assert out.action == "open_long" and degrade is None


def test_gate_downgrades_open_below_threshold():
    dec = TradeDecision("open_long", 0.5, 0.7, "r")
    out, degrade = _gate(2, dec, ["long", "flat", "flat"])   # long_votes=1 < 2
    assert out.action == "hold"
    assert degrade.startswith("low_consensus")
    assert "1/2" in degrade


def test_gate_uses_short_votes_for_open_short():
    dec = TradeDecision("open_short", 0.5, 0.7, "r")
    out, degrade = _gate(2, dec, ["short", "long", "long"])  # short_votes=1 < 2
    assert out.action == "hold" and "1/2" in degrade


def test_gate_never_blocks_close_or_hold():
    close = TradeDecision("close", None, None, "protect")
    out_close, degrade_close = _gate(9, close, [])           # 零票，阈值极高
    assert out_close.action == "close" and degrade_close is None
    hold = TradeDecision("hold", None, None, "wait")
    out_hold, _ = _gate(9, hold, [])
    assert out_hold.action == "hold"
