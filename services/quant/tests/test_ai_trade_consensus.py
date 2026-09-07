"""E2 · 策略共识度 + 共识门禁单测（ai-auto-trading-enhancements v4 §2.3 / §7.1）。

`compute_consensus`：计票 / 净立场 / 一致度（全多、多空各半→net=flat、空板→total=0）。
`AiTradeSession._apply_consensus_gate`：min_consensus=0 恒放行；≥阈值放行；<阈值把 open_* 降级 hold；
`close`/`hold` 任何阈值都不被拦（防「该止损时不平仓」）。纯离线（无网络、无 DB）。
"""
from __future__ import annotations

from types import SimpleNamespace

from app.ai_auto.decider import TradeDecision
from app.ai_auto.entry_quota import EntryQuota
from app.ai_auto.session import AiTradeSession
from app.ai_auto.trend_runtime import TrendRuntime
from app.ai_auto.trend_verdict import TrendVerdictRule
from app.ai_auto.open_funnel import OpenFunnelCounters, classify_block
from app.ai_auto.signals import (
    ConsensusRule,
    StrategySignalView,
    apply_consensus_gate,
    compute_consensus,
    consensus_rule_from_settings,
    contrarian_payload,
    resolve_consensus_gate,
)
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


def _gate(need: int, decision: TradeDecision, board_stances: list[str], degrade=None,
          *, contrarian: bool = False, used: int = 0):
    """会话侧薄封装的调用垫片。

    `contrarian=False`（默认）⇒ 逆势通路不装配 ⇒ 行为与本特性合入前**逐字相同**。
    """
    settings = Settings(ai_trade_min_consensus=need,
                        ai_trade_consensus_contrarian_enabled=contrarian)
    host = SimpleNamespace(
        executor=SimpleNamespace(settings=settings),
        decider=SimpleNamespace(position_step=0.0),
        _consensus_rule=consensus_rule_from_settings(settings),
        _contrarian_used=used,
        _entry_quota=EntryQuota("test", 3, legacy_assumed_used=used),
        _round_trace=None,
        # execution-trend-verdict：`_apply_consensus_gate` 现在还会读会话的趋势基调运行时来解
        # 两条顺势放宽通路。替身 self 必须同步补齐——**未装配的 rule ⇒ 两条通路恒返回 0** ⇒
        # 本文件全部断言与本特性合入前逐字相同（这正是「总闸关即逐字节回退」的会话级证明）。
        _trend_rule=TrendVerdictRule(),
        _trend=TrendRuntime(TrendVerdictRule()),
        _discipline=SimpleNamespace(funnel=OpenFunnelCounters()))
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


# ============================ 方向对称的逆势通路（ai-trade-directional-symmetry 轴 A） ============================
#
# 本节的第一条用例是**整轴唯一的对称性铁律守卫**（RK-2）：`resolve_consensus_gate` 的函数体里除了
# `votes` / `opposing` 的取数那一行，不允许出现任何方向字面判断。它必须先写、先红。

_EVIDENCE = ("净立场偏多但 4H 结构已在关键位破位，成交量在反弹段持续萎缩；我推翻策略 15 的因子"
             "得分与策略 28 的趋势带立场——二者的前提在当前体制下都不成立。若价格重新站回中轨"
             "并伴随放量，我立刻认错离场。")


def _rule(**over) -> ConsensusRule:
    base = {"min_consensus": 1, "contrarian_enabled": True, "contrarian_min_confidence": 0.65,
            "contrarian_max_opposing_votes": 1, "contrarian_min_evidence_chars": 60,
            "contrarian_max_per_session": 3, "contrarian_size_mult": 0.5}
    base.update(over)
    return ConsensusRule(**base)


def _resolve(action: str, board: list[str], *, rule=None, used: int = 0, step: float = 0.0,
             confidence: float | None = 0.72, evidence: str | None = _EVIDENCE,
             size: float | None = 0.5):
    decision = TradeDecision(action, size, confidence, "r", contrarian_evidence=evidence)
    consensus = compute_consensus([_view(s) for s in board])
    return resolve_consensus_gate(decision, consensus, rule or _rule(),
                                  position_step=step, used_contrarian=used)


def test_contrarian_lane_is_byte_identical_for_long_and_short():
    """★ 对称性铁律：同一组入参镜像方向后，lane / reason / 仓位必须逐字相同（RK-2 的唯一守卫）。"""
    for board_long, board_short in ((["long"], ["short"]), (["long", "flat"], ["short", "flat"])):
        # 做空逆势（板子全多）vs 做多逆势（板子全空）——票数分布互为镜像。
        short_out = _resolve("open_short", board_long)
        long_out = _resolve("open_long", board_short)
        assert short_out.lane == long_out.lane
        assert short_out.reason == long_out.reason
        assert short_out.decision.size_fraction == long_out.decision.size_fraction
        assert short_out.contrarian_used == long_out.contrarian_used
        assert short_out.wire["votes"] == long_out.wire["votes"]
        assert short_out.wire["opposingVotes"] == long_out.wire["opposingVotes"]


def test_contrarian_disabled_is_byte_identical_to_legacy():
    """通路关 ⇒ `(decision, reason)` 与旧三参 `apply_consensus_gate` 逐字相等；直通轮返回同一个对象。"""
    rule = _rule(contrarian_enabled=False)
    for action, board in (("open_short", ["long"]), ("open_long", ["short"]),
                          ("open_long", ["long"]), ("close", []), ("hold", [])):
        decision = TradeDecision(action, 0.5 if action.startswith("open_") else None,
                                 0.72, "r", contrarian_evidence=_EVIDENCE)
        consensus = compute_consensus([_view(s) for s in board])
        out = resolve_consensus_gate(decision, consensus, rule,
                                     position_step=0.0, used_contrarian=0)
        legacy_decision, legacy_reason = apply_consensus_gate(decision, consensus, 1)
        assert out.reason == legacy_reason
        assert out.decision.action == legacy_decision.action
        assert out.decision.size_fraction == legacy_decision.size_fraction
        if legacy_reason is None:
            assert out.decision is decision          # 放行轮绝不顺手 replace


def test_contrarian_four_codes_in_declared_order():
    """四条否决码各一例；**顺序即降级码**（先判到哪条就报哪条）。"""
    # a. 反向票数超上限（板子两票多头，上限 1）
    assert _resolve("open_short", ["long", "long"]).reason.startswith("low_consensus_opposed")
    # b. 配额用尽（先于置信度判定 ⇒ 即便置信度不足也报 quota）
    out = _resolve("open_short", ["long"], used=3, confidence=0.1)
    assert out.reason.startswith("low_consensus_quota")
    # c. 置信度不足（先于证据判定 ⇒ 即便证据缺席也报 conf）
    out = _resolve("open_short", ["long"], confidence=0.2, evidence=None)
    assert out.reason.startswith("low_consensus_conf")
    # d. 证据字数不足
    assert _resolve("open_short", ["long"], evidence="太短").reason.startswith(
        "low_consensus_evidence")


def test_contrarian_fails_closed_on_unverifiable_inputs():
    """fail-closed：置信度非有限 / 证据非字符串 / 配额非整数 ⇒ 一律退回否决，绝不放行。"""
    assert _resolve("open_short", ["long"], confidence=None).lane == "veto"
    assert _resolve("open_short", ["long"], confidence=float("nan")).lane == "veto"
    assert _resolve("open_short", ["long"], evidence=12345).lane == "veto"     # type: ignore[arg-type]
    assert _resolve("open_short", ["long"], used=None).lane == "veto"          # type: ignore[arg-type]


def test_contrarian_always_shrinks():
    """放行时仓位必然 <= 原提议 × size_mult（量化后），且被档位地板吃到 0 ⇒ 退回 `low_consensus_size`。"""
    out = _resolve("open_short", ["long"], size=0.5)
    assert out.lane == "contrarian" and out.contrarian_used is True
    assert out.decision.size_fraction == 0.25
    assert out.decision.action == "open_short"          # 方向不变，只缩仓
    # 档位 0.5 时 0.5×0.5=0.25 会被吸附回 0.5（顶格），那等于**放大**，必须退回否决。
    floored = _resolve("open_short", ["long"], size=0.5, step=0.5)
    assert floored.reason.startswith("low_consensus_size")


def test_contrarian_never_rescues_a_passing_decision():
    """票数已达门槛 ⇒ 逆势通路完全不参与（同一个对象直通，配额不动）。"""
    decision = TradeDecision("open_long", 0.5, 0.72, "r", contrarian_evidence=_EVIDENCE)
    out = resolve_consensus_gate(decision, compute_consensus([_view("long")]), _rule(),
                                 position_step=0.0, used_contrarian=0)
    assert out.lane == "consensus" and out.decision is decision
    assert out.contrarian_used is False


def test_all_new_codes_classify_to_consensus():
    """五个细分码经 `open_funnel.classify_block` 全部归到 `consensus` 那一格（漏斗零改动即闭合）。"""
    for code in ("low_consensus_opposed(0/1)", "low_consensus_quota(0/1)",
                 "low_consensus_conf(0/1)", "low_consensus_evidence(0/1)",
                 "low_consensus_size(0/1)", "low_consensus(0/1)"):
        assert classify_block(code) == "consensus"


def test_contrarian_payload_absent_when_lane_unavailable():
    """A-5：通路不可用 ⇒ 整键缺席 ⇒ prompt 逐字节回退。"""
    assert contrarian_payload(_rule(contrarian_enabled=False), 0) is None
    assert contrarian_payload(_rule(min_consensus=0), 0) is None
    wire = contrarian_payload(_rule(), 1)
    assert wire["available"] is True and wire["remaining"] == 2


def test_session_gate_counts_contrarian_and_never_calls_it_a_block():
    """逆势门禁放行仅计漏斗；收到真实委托接收回执前不消耗额度。"""
    decision = TradeDecision("open_short", 0.5, 0.72, "r", contrarian_evidence=_EVIDENCE)
    settings = Settings(ai_trade_min_consensus=1, ai_trade_consensus_contrarian_enabled=True)
    funnel = OpenFunnelCounters()
    host = SimpleNamespace(
        executor=SimpleNamespace(settings=settings),
        decider=SimpleNamespace(position_step=0.0),
        _consensus_rule=consensus_rule_from_settings(settings),
        _contrarian_used=0,
        _entry_quota=EntryQuota("test", 3),
        _round_trace={},
        # 同上：替身 self 必须带上会话的趋势基调运行时（未装配 ⇒ 两条通路恒返回 0 ⇒
        # 本用例的断言与本特性合入前逐字相同）。
        _trend_rule=TrendVerdictRule(),
        _trend=TrendRuntime(TrendVerdictRule()),
        _discipline=SimpleNamespace(funnel=funnel))
    out, degrade = AiTradeSession._apply_consensus_gate(
        host, decision, compute_consensus([_view("long")]), None)
    assert out.action == "open_short" and out.size_fraction == 0.25 and degrade is None
    assert host._contrarian_used == 0
    assert host._entry_quota.used == host._entry_quota.reserved == 0
    assert host._round_contrarian is True
    assert funnel.contrarian_passed == 1
    assert funnel.blocked == {}                       # 它不是降级，绝不进 blocked
    assert host._round_trace["consensusGate"]["lane"] == "contrarian"


def test_short_is_not_structurally_impossible_in_a_long_only_board():
    """★ 回归复现用例：信号板 0 票做空时，旧代码恒 hold，新代码在证据充分时放行。

    这是本次 bug 的稳定复现器（项目规范第七条：修 bug 必须先写能复现的用例）。
    """
    board = ["long", "long"]      # 信号板一边倒：short_votes == 0，恒不满足 `votes >= 1`
    consensus = compute_consensus([_view(s) for s in board])
    decision = TradeDecision("open_short", 0.5, 0.9, "r", contrarian_evidence=_EVIDENCE)
    # 合入前的行为（也是总闸关 / 通路关时的行为）：恒降级为 hold。
    legacy, legacy_reason = apply_consensus_gate(decision, consensus, 1)
    assert legacy.action == "hold" and legacy_reason.startswith("low_consensus")
    # 修复后：把反向票上限放到板子的实际票数，证据充分即放行（仓位强制减半）。
    out = resolve_consensus_gate(decision, consensus,
                                 _rule(contrarian_max_opposing_votes=2),
                                 position_step=0.0, used_contrarian=0)
    assert out.lane == "contrarian"
    assert out.decision.action == "open_short" and out.decision.size_fraction == 0.25
