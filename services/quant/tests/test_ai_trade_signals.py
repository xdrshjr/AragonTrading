"""信号板影子回放单测（ai-auto-trading §2.2 / P0-1）：立场为**持久**（影子 SimBroker position_side），
latestSignal 仅在边沿根非 none；状态化 close 立场经暖机可达；多策略影子互不污染。

合成行情、桩策略，纯离线（无网络、无 DB）。
"""
from __future__ import annotations

from app.ai_auto.decider import TradeDecision
from app.ai_auto.signals import (
    Consensus,
    apply_consensus_gate,
    decision_to_signals,
    evaluate_signal_board,
    make_shadow_broker,
)
from app.engine.backtester import candles_to_df
from app.paper.strategy_factory import PaperStrategySpec
from app.strategies.base import BaseStrategy, Signal, StrategyMeta

_T = 1_700_000_000
_S = 3600


class _StubStrategy(BaseStrategy):
    meta = StrategyMeta(key="_stub_sig", name="Stub", description="t",
                        default_params={}, params_schema={})

    def __init__(self, signals_by_bar=None):
        super().__init__({})
        self._sig = signals_by_bar or {}

    def warmup(self) -> int:
        return 1

    def on_candle(self, df, i, ctx):
        return self._sig.get(i, [])


def _candles(n: int = 8):
    return [{"ts": _T + i * _S, "o": 100.0 + i, "h": 105.0 + i, "l": 95.0 + i,
             "c": 100.0 + i, "vol": 1.0} for i in range(n)]


def _spec(strategy_id: int, strategy) -> PaperStrategySpec:
    return PaperStrategySpec(strategy_id, f"S{strategy_id}", "atomic", strategy, {})


def test_stance_is_persistent_and_latest_signal_is_edge_only():
    candles = _candles(8)
    df = candles_to_df(candles)
    strat = _StubStrategy({2: [Signal(side="long")], 5: [Signal(side="close")]})
    spec = _spec(1, strat)
    shadow = make_shadow_broker()
    views = [evaluate_signal_board([spec], [shadow], [df], i)[0] for i in range(len(candles))]

    # 边沿根：on_candle 返回 long → latest=open_long，但持仓次根开盘才成交，本根 stance 仍 flat
    assert views[2].latest_signal == "open_long"
    assert views[2].stance == "flat"
    # 非边沿根：latest=none，但影子已在次根开盘成交 → stance 持久为 long（证「持久立场」非边沿返回值）
    assert views[3].latest_signal == "none"
    assert views[3].stance == "long"
    assert views[4].stance == "long"
    # close 边沿根：latest=close，stance 仍 long（次根成交平仓）
    assert views[5].latest_signal == "close"
    assert views[5].stance == "long"
    # 平仓成交后：stance 归 flat（证状态化 close 立场可达）
    assert views[6].stance == "flat"


def test_multi_strategy_shadows_do_not_pollute_each_other():
    candles = _candles(8)
    df = candles_to_df(candles)
    strat_a = _StubStrategy({2: [Signal(side="long")]})   # 开多后持有
    strat_b = _StubStrategy({})                            # 从不交易
    specs = [_spec(1, strat_a), _spec(2, strat_b)]
    shadows = [make_shadow_broker(), make_shadow_broker()]
    dfs = [df, df]
    last = None
    for i in range(len(candles)):
        last = evaluate_signal_board(specs, shadows, dfs, i)
    assert last[0].stance == "long"    # A 的影子持多仓
    assert last[1].stance == "flat"    # B 的影子不受 A 影响，恒 flat


def test_short_stance_reachable_and_strength_reason_passthrough():
    candles = _candles(6)
    df = candles_to_df(candles)
    strat = _StubStrategy({1: [Signal(side="short", strength=0.4, reason="edge")]})
    spec = _spec(9, strat)
    shadow = make_shadow_broker()
    views = [evaluate_signal_board([spec], [shadow], [df], i)[0] for i in range(len(candles))]
    assert views[1].latest_signal == "open_short"
    assert abs(views[1].strength - 0.4) < 1e-9
    assert views[1].reason == "edge"
    assert views[2].stance == "short"   # 空头持久立场可达


# ============================ 共识门禁 + 决策→信号（§2.8/R1 上移的纯函数）============================
def _consensus(long_v: int, short_v: int, flat_v: int = 0) -> Consensus:
    total = long_v + short_v + flat_v
    net = "long" if long_v > short_v else "short" if short_v > long_v else "flat"
    agreement = max(long_v, short_v) / total if total else 0.0
    return Consensus(long_v, short_v, flat_v, total, net, round(agreement, 4))


def test_apply_consensus_gate_disabled_when_min_zero():
    """min_consensus=0（默认）→ 门禁关闭：开仓原样通过，gate_reason 为 None。"""
    dec = TradeDecision("open_long", 0.5, 0.8, "r")
    out, reason = apply_consensus_gate(dec, _consensus(1, 0), 0)
    assert out is dec and reason is None


def test_apply_consensus_gate_downgrades_open_when_votes_below_min():
    """同向共识不足 → 开仓硬降级为 hold，gate_reason 带 low_consensus(k/n) 计数。"""
    dec = TradeDecision("open_long", 0.5, 0.8, "r")
    out, reason = apply_consensus_gate(dec, _consensus(1, 2), 2)
    assert out.action == "hold"
    assert reason == "low_consensus(1/2)"


def test_apply_consensus_gate_passes_when_votes_meet_min():
    """同向共识达标 → 开仓通过，gate_reason None。"""
    dec = TradeDecision("open_short", 0.5, 0.8, "r")
    out, reason = apply_consensus_gate(dec, _consensus(0, 3), 2)
    assert out is dec and reason is None


def test_apply_consensus_gate_never_blocks_close_or_hold():
    """close/hold 从不被门禁拦（共识不足也永远能平仓保护）。"""
    for action in ("close", "hold"):
        dec = TradeDecision(action, None, None, "r")
        out, reason = apply_consensus_gate(dec, _consensus(0, 0, 5), 3)
        assert out is dec and reason is None


def test_decision_to_signals_maps_pure_core_without_explain():
    """纯核心映射：open→带 strength/reason，close→带 reason，hold→[]，且不设 explain（富化在 session 层）。"""
    assert decision_to_signals(TradeDecision("hold", None, None, "r")) == []
    longs = decision_to_signals(TradeDecision("open_long", 0.4, 0.9, "up"))
    assert longs[0].side == "long" and abs(longs[0].strength - 0.4) < 1e-9
    assert longs[0].explain is None
    shorts = decision_to_signals(TradeDecision("open_short", 0.3, 0.9, "dn"))
    assert shorts[0].side == "short" and abs(shorts[0].strength - 0.3) < 1e-9
    closes = decision_to_signals(TradeDecision("close", None, None, "exit"))
    assert closes[0].side == "close" and closes[0].reason == "exit"
