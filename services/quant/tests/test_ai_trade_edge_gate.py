"""最小 edge 门禁纯核心单测（ai-trade-cost-accounting-and-edge-gate §7.2）。

四个被冻结的不变式各有专项用例把守：
- **豁免矩阵**（用例 1）：close / hold / cancel_order 恒返回**同一个**对象；
- **k_min=0 空转**（用例 2，Z-3）：同一对象（`is` 相等），绝不顺手 replace；
- **fail-open**（用例 3）：ATR 缺失 ⇒ 放行 + `unavailable`——这是与铁律层极性相反的关键一条，
  本仓库有过「反思致永不开单」的实案，一个 fail-closed 的粗估门禁会在暖机断面上把开仓全部否掉；
- **判定价单点收敛**（用例 6）：限价按委托价而非最新价。
"""
from __future__ import annotations

from app.ai_auto.cost_model import CostQuery, estimate_round_trip_cost, resolve_cost_params
from app.ai_auto.decider import TradeDecision
from app.ai_auto.edge_gate import (
    apply_edge_gate,
    edge_wire,
    estimate_gross_edge_bps,
    evaluate_edge_gate,
    gate_status,
)
from app.config import Settings

_SETTINGS = Settings()
_PARAMS = resolve_cost_params(_SETTINGS)
_COST_OK = estimate_round_trip_cost(
    CostQuery(ord_type="market", side="long", bar_seconds=900, funding_rate=0.0), _PARAMS)
_COST_DEGRADED = estimate_round_trip_cost(
    CostQuery(ord_type="market", side="long", bar_seconds=900, funding_rate=None), _PARAMS)


def _open(action: str = "open_long", **over) -> TradeDecision:
    base = {"action": action, "size_fraction": 0.5, "confidence": 0.7, "reason": "test"}
    base.update(over)
    return TradeDecision(**base)


def _edge(target_bps: float):
    """构造一个恰好产出 `target_bps` 的估算（ATR/参考价 反解，避免手写 dataclass 绕过真实路径）。"""
    ref_px = 10_000.0
    return estimate_gross_edge_bps(action="open_long", ref_px=ref_px,
                                   atr_px=ref_px * target_bps / 1e4, take_profit_pct=None,
                                   atr_target_mult=1.0)


def test_exempt_actions_are_never_gated():
    """用例 1：豁免矩阵——close / hold / cancel_order 恒返回同一对象、reason 为 None。"""
    for action in ("close", "hold", "cancel_order"):
        decision = _open(action)
        out, reason = apply_edge_gate(decision, _edge(1.0), _COST_OK, k_min=99.0,
                                      degraded_mult=1.2)
        assert out is decision and reason is None


def test_k_min_zero_is_object_identity_passthrough():
    """用例 2（Z-3）：门禁空转 ⇒ 返回**同一个** decision 对象。"""
    decision = _open()
    out, reason = apply_edge_gate(decision, _edge(1.0), _COST_OK, k_min=0.0, degraded_mult=1.2)
    assert out is decision and reason is None
    assert evaluate_edge_gate(decision, settings=_SETTINGS, last_px=10_000.0, atr_px=50.0,
                              take_profit_pct=None, bar_seconds=900) is None


def test_missing_atr_fails_open_and_counts_unavailable():
    """用例 3（fail-open 钉子）：ATR 不可用 ⇒ 估算 None ⇒ **放行** + status=unavailable。"""
    assert estimate_gross_edge_bps(action="open_long", ref_px=10_000.0, atr_px=None,
                                   take_profit_pct=None, atr_target_mult=1.0) is None
    decision = _open()
    out, reason = apply_edge_gate(decision, None, _COST_OK, k_min=2.0, degraded_mult=1.2)
    assert out is decision and reason is None
    assert gate_status("open_long", None, _COST_OK, reason) == "unavailable"


def test_veto_reason_code_is_byte_exact():
    """用例 4：降级码逐字节 —— 前端 DEGRADE_REASON_LABELS 与运维 grep 都靠这个格式。"""
    out, reason = apply_edge_gate(_open(), _edge(8.1), _COST_OK, k_min=2.0, degraded_mult=1.2)
    assert reason == "edge_below_cost(edge=8.1bps,cost=13.0bps,k=2.0)"
    assert out.action == "hold" and out.reason == reason
    assert out.size_fraction is None and out.confidence is None
    assert gate_status("open_long", _edge(8.1), _COST_OK, reason) == "veto"


def test_degraded_cost_tightens_the_threshold():
    """用例 5：构造一个「ok 放行、degraded 拦下」的临界样本（阈值 ×1.2）。"""
    edge = _edge(13.5)          # 13.0 < 13.5 < 13.0×1.2 = 15.6
    passed, pass_reason = apply_edge_gate(_open(), edge, _COST_OK, k_min=1.0, degraded_mult=1.2)
    vetoed, veto_reason = apply_edge_gate(_open(), edge, _COST_DEGRADED, k_min=1.0,
                                          degraded_mult=1.2)
    assert pass_reason is None and passed.action == "open_long"
    assert veto_reason is not None and vetoed.action == "hold"


def test_limit_decision_is_judged_on_the_order_price():
    """用例 6：限价按**委托价**判定（复用 base_rules.entry_reference_px，单点收敛）。"""
    market = evaluate_edge_gate(_open(), settings=_gate_on(), last_px=10_000.0, atr_px=50.0,
                                take_profit_pct=None, bar_seconds=900)
    limit = evaluate_edge_gate(_open(ord_type="limit", limit_px=5_000.0), settings=_gate_on(),
                               last_px=10_000.0, atr_px=50.0, take_profit_pct=None,
                               bar_seconds=900)
    # 同一 ATR 下判定价减半 ⇒ ATR 折合 bps 翻倍 ⇒ 两侧 edge 不同（证明取的是委托价）。
    assert limit.wire["edgeBps"] == market.wire["edgeBps"] * 2


def test_take_profit_cap_narrows_the_edge():
    """用例 7：单仓 OCO 止盈距离小于 ATR 预算 ⇒ source 切到 take_profit（上界只收窄）。"""
    est = estimate_gross_edge_bps(action="open_long", ref_px=10_000.0, atr_px=100.0,
                                  take_profit_pct=0.005, atr_target_mult=1.0)
    assert est.source == "take_profit"
    assert est.target_bps == 50.0 and est.atr_bps == 100.0 and est.take_profit_bps == 50.0


def test_none_take_profit_means_no_cap_not_zero_cap():
    """用例 8（RV-7）：`take_profit_pct is None` ⇒ 上界**整体不生效**，与传入极大值等价。"""
    without = estimate_gross_edge_bps(action="open_long", ref_px=10_000.0, atr_px=100.0,
                                      take_profit_pct=None, atr_target_mult=1.0)
    huge = estimate_gross_edge_bps(action="open_long", ref_px=10_000.0, atr_px=100.0,
                                   take_profit_pct=9.99, atr_target_mult=1.0)
    assert without.take_profit_bps is None
    assert (without.target_bps, without.source) == (100.0, "atr")
    assert (huge.target_bps, huge.source) == (without.target_bps, without.source)


def test_gate_status_reports_pass_for_non_gated_actions():
    """非受控动作（hold 轮）**不算** unavailable——否则一个持续观望的会话会误触发不可用告警。"""
    assert gate_status("hold", None, _COST_OK, None) == "pass"
    assert gate_status("close", None, None, None) == "pass"
    assert gate_status("open_long", None, _COST_OK, None) == "unavailable"


def test_non_gated_rounds_are_marked_not_gated_so_they_stay_out_of_the_counters():
    """观望 / 平仓轮 `status` 也是 pass，但 `gated=False` ⇒ 不进 `edgeGate` 计数分母。

    AI 会话的观望轮占比很高；若它们被记成「放行」，`vetoRate` 会被稀释到 §7.5 的 L2 放行区间
    （20%~70%）以下，运维据此会误判「门禁未生效」并去调一个其实正常的 `k_min`。
    """
    for action in ("hold", "close", "cancel_order"):
        out = evaluate_edge_gate(_open(action), settings=_gate_on(), last_px=10_000.0,
                                 atr_px=50.0, take_profit_pct=None, bar_seconds=900)
        assert out.gated is False and out.status == "pass"
    opened = evaluate_edge_gate(_open(), settings=_gate_on(), last_px=10_000.0, atr_px=50.0,
                                take_profit_pct=None, bar_seconds=900)
    assert opened.gated is True


def test_evaluate_wire_carries_threshold_and_confidence():
    out = evaluate_edge_gate(_open(), settings=_gate_on(), last_px=10_000.0, atr_px=8.1,
                             take_profit_pct=None, bar_seconds=900, funding_enabled=False)
    assert out.wire["status"] == "veto"
    assert out.wire["kMin"] == 2.0 and out.wire["requiredBps"] == 26.0
    assert out.wire["costConfidence"] == "ok" and out.wire["edgeSource"] == "atr"
    assert out.cost_bps == 13.0 and out.edge_bps == 8.1


def test_edge_wire_is_none_safe():
    assert edge_wire(None) is None
    assert edge_wire(_edge(20.0))["source"] == "atr"


def _gate_on() -> Settings:
    """`k_min=2.0` 的运行配置（默认 0 = 空转，多数用例需要显式打开门禁）。"""
    return Settings(ai_trade_edge_gate_k_min=2.0, ai_trade_funding_enabled=False)
