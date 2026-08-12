"""回测侧的纪律预设（R-3 / [P1-4]；判据 G-15）。

两处各解析一次的后果本仓库已有成文前科（`base_rules` P1-4 / `profit_guard` I-3）：用户拿回测调出来
的参数在实盘失真。故回测吃的必须是**同一个** `_resolve_discipline`，载体是那份派生 `settings`。

第二件更危险：回测侧**今天根本没有** `min(value, len(specs))` 这道夹紧。预设一上，单策略回测立刻
退化成「开仓永久不可达」——一份全 hold 的曲线看上去像「策略太保守」，实际是判据恒真。
"""
from __future__ import annotations

from app.ai_auto.backtest_manager import AiBacktestManager, AiBacktestStartRequest
from app.ai_auto.decider import TradeDecision
from app.ai_auto.signals import Consensus, apply_consensus_gate
from app.api.ai_auto import _resolve_discipline
from app.config import Settings
from app.paper.strategy_factory import PaperStrategySpec
from app.schemas import AiTradeDisciplineIn
from app.strategies.base import BaseStrategy, StrategyMeta


class _Stub(BaseStrategy):
    meta = StrategyMeta(key="_stub_bt_discipline", name="Stub", description="t",
                        default_params={}, params_schema={})

    def warmup(self) -> int:
        return 1

    def on_candle(self, df, i, ctx):
        return []


def _specs(n: int) -> list[PaperStrategySpec]:
    return [PaperStrategySpec(i + 1, f"S{i}", "atomic", _Stub({}), {}) for i in range(n)]


def _one_long_vote() -> Consensus:
    """一个策略、一票做多的真实断面（单策略会话最常见的开仓时刻）。"""
    return Consensus(long_votes=1, short_votes=0, flat_votes=0, total=1,
                     net_stance="long", agreement=1.0)


def _spec_for(settings: Settings, specs, *, min_consensus=None):
    req = AiBacktestStartRequest(
        specs=specs, inst_id="BTC-USDT-SWAP", bar="1H", start_ts=1_700_000_000,
        end_ts=1_700_360_000, settings=settings, context_refresh_rounds=10,
        min_consensus=min_consensus)
    return AiBacktestManager()._build_spec(req, provider=object(), planned=10)


def test_backtest_inherits_the_whole_derived_settings_copy():
    """R-3：回测的仓位层 / edge 门禁 / 刹车都读 `spec.settings`，故派生副本一进来整层就活了。"""
    derived, _, _ = _resolve_discipline(None, Settings(_env_file=None), spec_count=3,
                                        priming_bars=0)
    spec = _spec_for(derived, _specs(3))
    assert spec.settings.ai_trade_sizing_enabled is True
    assert spec.settings.ai_trade_edge_gate_k_min == 1.5
    assert spec.settings.ai_trade_stop_atr_mult > 0
    assert spec.settings.ai_trade_brake_halt_dd_pct > 0


def test_explicit_request_field_beats_the_preset():
    """G-15 前半：优先级写死为「显式请求字段 > 预设 > config 默认」，不定优先级就是随机行为。"""
    derived, _, _ = _resolve_discipline(AiTradeDisciplineIn(preset="balanced"),
                                        Settings(_env_file=None), spec_count=3, priming_bars=0)
    assert derived.ai_trade_min_consensus == 1
    assert _spec_for(derived, _specs(3), min_consensus=3).min_consensus == 3
    assert _spec_for(derived, _specs(3)).min_consensus == 1        # 不填 ⇒ 落到预设值


def test_single_strategy_backtest_is_clamped_and_stays_reachable():
    """G-15 后半：单策略 + `steady`（门槛 2）⇒ 夹到 1，且那个 1 **确实过得去**。

    只断言「夹到 1」是不够的——本轮要防的正是「判据恒真 ⇒ 全 hold 假曲线」，故必须再证明夹紧后的
    门槛在真实断面（1 个策略投 1 票）上会放行。
    """
    specs = _specs(1)
    derived, preset, _ = _resolve_discipline(AiTradeDisciplineIn(preset="steady"),
                                             Settings(_env_file=None), spec_count=len(specs),
                                             priming_bars=0)
    spec = _spec_for(derived, specs)
    assert spec.min_consensus == 1
    assert [a["field"] for a in preset.adjustments] == ["minConsensus"]

    decision = TradeDecision(action="open_long", size_fraction=0.5, confidence=0.9, reason="r")
    passed, reason = apply_consensus_gate(
        decision, _one_long_vote(),
        spec.min_consensus)
    assert reason is None and passed.action == "open_long", "夹紧后仍开不出仓 = 恒真判据"


def test_unclamped_preset_would_make_a_single_strategy_backtest_unreachable():
    """反证：不夹紧（照搬 `steady` 的 2）时，同一断面必被拦死——这条就是 [P1-4] 描述的坏结局。"""
    decision = TradeDecision(action="open_long", size_fraction=0.5, confidence=0.9, reason="r")
    gated, reason = apply_consensus_gate(
        decision, _one_long_vote(), 2)
    assert gated.action == "hold" and reason and reason.startswith("low_consensus")


def test_backtest_off_preset_leaves_config_defaults_untouched():
    base = Settings(_env_file=None)
    derived, _, _ = _resolve_discipline(AiTradeDisciplineIn(preset="off"), base,
                                        spec_count=1, priming_bars=0)
    assert derived is base
    assert _spec_for(derived, _specs(1)).settings.ai_trade_sizing_enabled is False
