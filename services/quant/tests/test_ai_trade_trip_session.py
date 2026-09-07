"""轴 A 装配链：估计 → 开仓 → 平仓的绑定（G-3 / G-3b 两条阻断级回归）。

不起真实会话——`AttributionLedger` 就是那条链的全部状态所在，会话侧只有五行薄调用。这样既能把
两条回归钉死，又不必在真实资金决策路径上造一个假会话（那会引入比它守住的更多的耦合）。
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.ai_auto.attribution import AttributionLedger
from app.ai_auto.cost_runtime import CostRuntime
from app.ai_auto.decider import TradeDecision
from app.ai_auto.edge_gate import EdgeGateOutcome
from app.ai_auto.session import AiTradeSession
from app.ai_auto.trip_ledger import RoundEstimate
from app.config import get_settings


def _est(edge: float, conf: float) -> RoundEstimate:
    return RoundEstimate(est_edge_bps=edge, est_cost_bps=13.0, edge_source="atr",
                         cost_confidence="ok", decision_confidence=conf, wake_kind="candle")


def _ledger() -> AttributionLedger:
    return AttributionLedger(net_mode=True, ct_val=0.01)


def _open(**over) -> dict:
    row = {"price": 100.0, "qty": 10.0, "fee": 0.02, "ts": 100, "side": "long",
           "stopLossPrice": 95.0, "takeProfitPrice": 110.0,
           "feeKnown": True, "feeBasis": "measured", "positionAfter": 10}
    row.update(over)
    row["id"] = row["ts"]
    return row


_CLOSE = {"id": 200, "price": 101.0, "qty": 10.0, "fee": 0.02, "pnl": 0.1,
          "ts": 200, "side": "close", "positionAfter": 0,
          "feeKnown": True, "feeBasis": "measured"}


def test_g3_a_vetoed_intent_after_the_open_never_rebinds_the_estimate():
    """G-3（D-3 回归，阻断级）：开仓后插一轮被否的反向开仓意图，结算仍绑**开仓那一轮**的估计。

    删掉 `end_round()` 的清零逻辑，本用例必红。
    """
    ledger = _ledger()
    ledger.note_estimate(_est(41.2, 0.62))
    ledger.note_open(_open(), seq=1, atr_px=0.5, now_wall=1.0)
    ledger.end_round()                       # 开仓轮收尾

    ledger.note_estimate(_est(999.0, 0.10))  # 下一轮：一次**被否**的反向开仓意图
    ledger.end_round()                       # 轮末清零 —— 这一行就是 D-3 的修复

    meta = ledger.settle_close(_CLOSE, exit_source="agent")
    assert meta["estEdgeBps"] == 41.2
    assert meta["decisionConfidence"] == 0.62


def test_g3_without_end_round_the_estimate_would_rebind():
    """反面钉子：不清零轮级槽时，估计**会**被下一轮改绑——证明 G-3 守的是一条真实的通路。"""
    ledger = _ledger()
    ledger.note_estimate(_est(41.2, 0.62))
    ledger.note_open(_open(), seq=1, atr_px=0.5, now_wall=1.0)
    ledger.note_estimate(_est(999.0, 0.10))          # 没有 end_round()
    ledger.note_open(_open(ts=101), seq=2, atr_px=0.5, now_wall=2.0)   # 新 epoch 认领到错的那份
    assert ledger.settle_close(_CLOSE, exit_source="agent")["estEdgeBps"] == 999.0


def test_g3b_limit_order_fill_across_rounds_still_binds_the_placing_round():
    """G-3b（[P0-2] 限价回归，阻断级）：跨过 `finally` 的 resting 成交仍绑**下单那一轮**。

    默认订单类型就是限价（`ai_trade_limit_orders_enabled=True`），其采纳成交由盯市 tick 发出，
    那时轮级槽早已被清零。删掉 `claim_estimate` 的按 `clOrdId` 认领，本用例必红。
    """
    ledger = _ledger()
    ledger.note_estimate(_est(55.5, 0.71))
    ledger.claim_estimate("cl-abc")          # 下单那一刻：轮级槽 → 待成交槽
    ledger.end_round()                       # 本轮 finally（限价单仍在挂）

    for _ in range(3):                       # 之后几轮观望：轮级槽恒空
        ledger.note_estimate(None)
        ledger.end_round()

    ledger.note_open(_open(clOrdId="cl-abc"), seq=1, atr_px=0.5, now_wall=9.0)
    meta = ledger.settle_close(_CLOSE, exit_source="agent")
    assert meta["estEdgeBps"] == 55.5 and meta["decisionConfidence"] == 0.71


def test_market_path_falls_back_to_the_round_slot_unchanged():
    """G-3b 的另一半：市价路径（无 `clOrdId`）恒走轮级槽回退，行为与合入前逐字一致。"""
    ledger = _ledger()
    ledger.note_estimate(_est(33.3, 0.5))
    ledger.note_open(_open(), seq=1, atr_px=0.5, now_wall=1.0)
    assert ledger.settle_close(_CLOSE, exit_source="agent")["estEdgeBps"] == 33.3


def test_pending_slot_is_bounded_and_fifo():
    """待成交槽有容量上限且按 FIFO 丢弃（挂单额度只有一个，正常只会有 1 项）。"""
    ledger = _ledger()
    for i in range(12):
        ledger.note_estimate(_est(float(i), 0.5))
        ledger.claim_estimate(f"cl-{i}")
    assert len(ledger._pending_estimates) <= 8
    assert "cl-0" not in ledger._pending_estimates and "cl-11" in ledger._pending_estimates


def test_realized_stats_absent_until_a_trip_settles_then_carries_both_denominators():
    """`aiStats.realized` 零往返 ⇒ 整键缺席；有样本时**必带**两个 coverage 分母。"""
    ledger = _ledger()
    assert ledger.realized_stats() is None
    ledger.note_estimate(_est(41.2, 0.62))
    ledger.note_open(_open(), seq=1, atr_px=0.5, now_wall=1.0)
    ledger.settle_close(_CLOSE, exit_source="agent")
    stats = ledger.realized_stats()
    assert stats["trips"] == 1 and stats["estimateCoverage"] == 1.0
    assert stats["feeCoverage"] == 1.0 and stats["medianMoveBps"] == 100.0


def test_exit_source_is_read_not_judged():
    """[P1-1]：`exitSource` 只从 meta 读；缺席 ⇒ `"unknown"`，本层绝不自己分类。"""
    ledger = _ledger()
    ledger.note_open(_open(), seq=1, atr_px=None, now_wall=1.0)
    trip_meta = ledger.settle_close(_CLOSE)          # 不传 exit_source
    assert trip_meta and ledger.settled_trips()[-1].exit_source == "unknown"


def test_apply_trip_meta_is_wired_after_the_exit_source_stamp():
    """调用真实装配方法，验证结算看到已标出的退出来源且最终持久化该结果。"""
    calls = []
    session = object.__new__(AiTradeSession)
    session.inst_id, session.bar = "BTC-USDT-SWAP", "15m"
    session._wake = SimpleNamespace(last_round_kind=None)
    session._pending_horizon_meta = None
    session.executor = SimpleNamespace(settings=SimpleNamespace(
        ai_trade_exit_attribution_enabled=True), take_settlement_meta=lambda: None)

    def stamp(_explain):
        calls.append("stamp")
        return "agent"

    def enrich(meta, _trade):
        calls.append("settle")
        assert meta["exitSource"] == "agent"
        meta["roundTripFeeUsd"] = .04

    session._consume_exit_source = stamp
    session._cost = SimpleNamespace(apply_explain_meta=lambda *_: None, apply_trip_meta=enrich)
    trade = {**_CLOSE, "explain": {"kind": "atomic"}}
    session._inject_explain_meta(trade)
    assert calls == ["stamp", "settle"]
    assert trade["explain"]["meta"]["roundTripFeeUsd"] == .04


@pytest.mark.parametrize("enabled", [True, False])
def test_cost_runtime_trip_ledger_switch_gates_the_meta_enrichment(enabled: bool):
    """总闸 `ai_trade_trip_ledger_enabled=False` ⇒ `explain.meta` 逐字回退（零富化键）。"""
    settings = get_settings().model_copy(update={"ai_trade_trip_ledger_enabled": enabled})
    runtime = CostRuntime("BTC-USDT-SWAP", settings, None, None)
    runtime.attribution = _ledger()
    runtime.note_estimate(
        EdgeGateOutcome(decision=None, reason=None, status="pass",
                        wire={"edgeSource": "atr", "costConfidence": "ok"},
                        cost_bps=13.0, edge_bps=41.2, gated=True),
        TradeDecision(action="open_long", size_fraction=0.3, confidence=0.62, reason="x"),
        "candle")
    runtime.note_open(_open(), seq=1, now_wall=1.0, atr_px=0.5)
    runtime.record_close(_CLOSE)
    meta: dict = {"exitSource": "agent"}
    runtime.apply_trip_meta(meta, _CLOSE)
    assert ("rMultiple" in meta) is enabled
    assert meta["exitSource"] == "agent"          # 本层只读，绝不改写既有键
    assert runtime.remaining_open_fee_usd == 0
    assert runtime.economics_snapshot()["roundTripCount"] == 1
