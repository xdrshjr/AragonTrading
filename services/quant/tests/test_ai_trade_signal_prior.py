"""信号先验纯核心单测（§8.1 T-7~T-10 / T-14 / T-22）。

四条被冻结的性质：**加载契约任一不满足即缺席**（C-1~C-4 / C-6）、**体制降级链三级**、
**聚合是加权均值而非未归一化的对数几率和**（后者会让 p 随贡献者数量发散）、**无证据就返回 None**
（绝不因为「有条目」就造一个 p 出来）。另加事件同源（G-11）与覆盖率口径（P0-2 连带）。
"""
from __future__ import annotations

from app.ai_auto.signal_prior import (
    load_snapshot,
    resolve_direction_prior,
    resolve_prior_params,
    strategy_fingerprint,
)
from app.ai_auto.signals import compute_weighted_consensus
from app.ai_auto.signals import StrategySignalView
from app.config import Settings
from app.engine.simulator import RiskParams

_NOW = 1_785_000_000.0
_SETTINGS = Settings()
_PARAMS = resolve_prior_params(_SETTINGS)


def _entry(**over) -> dict:
    base = {"source": "strategy_replay", "event": "stance", "signalKey": None,
            "strategyId": 12, "strategyName": "策略甲", "strategyFingerprint": "fp12",
            "direction": "long", "regime": "all", "winRate": 0.55, "meanRetBps": 5.0,
            "avgWinBps": 40.0, "avgLossBps": 30.0, "sampleN": 100, "lossN": 40,
            "segments": 6, "runN": 20, "ir": 1.0, "tStat": 2.4, "usable": True,
            "unusableReason": None, "costBasis": "gross"}
    base.update(over)
    return base


def _snapshot(entries: list[dict], **over) -> dict:
    base = {"version": 1, "instId": "BTC-USDT-SWAP", "bar": "15m",
            "builtAtTs": int(_NOW) - 3600, "asOfTs": int(_NOW) - 3600, "horizonBars": 8,
            "costBasis": "gross", "entries": entries}
    base.update(over)
    return base


def _view(strategy_id: int, stance: str, latest: str = "none") -> StrategySignalView:
    return StrategySignalView(strategy_id=strategy_id, name=f"S{strategy_id}", stance=stance,
                              latest_signal=latest, strength=0.0, reason="",
                              risk_params=RiskParams())


def _resolve(entries, *, direction="long", board=((12, "long"),), active=None,
             regime=None, params=_PARAMS):
    return resolve_direction_prior(entries, direction=direction, board_view=list(board),
                                   active_signal_keys=active, regime=regime, params=params)


# ============================ T-7：五道加载契约 ============================
def test_missing_snapshot_is_whole_key_absence():
    """C-1 总闸由 `PriorRuntime` 把守；本层对空快照一律整键缺席。"""
    assert load_snapshot(None, _PARAMS, now_wall=_NOW) == ([], "missing")
    assert load_snapshot({}, _PARAMS, now_wall=_NOW) == ([], "missing")


def test_stale_snapshot_is_rejected_whole_key():
    """C-2 新鲜度：超过 `max_age_s` ⇒ 整键缺席 + `stale` 降级原因（供告警分流）。"""
    raw = _snapshot([_entry()], builtAtTs=int(_NOW) - int(_PARAMS.max_age_s) - 10)
    entries, reason = load_snapshot(raw, _PARAMS, now_wall=_NOW)
    assert entries == [] and reason.startswith("stale")


def test_horizon_mismatch_is_rejected_whole_key():
    """C-3 持有期同源：`horizonBars` 与成本模型不等 ⇒ 整键缺席（分子分母必须说同一段持有期）。"""
    entries, reason = load_snapshot(_snapshot([_entry()], horizonBars=12), _PARAMS,
                                    now_wall=_NOW)
    assert entries == [] and reason.startswith("horizon_mismatch")


def test_non_gross_entry_is_dropped_not_whole_key():
    """C-4 成本口径：净额条目**单独剔除**（fail-closed），干净条目照常可用。"""
    raw = _snapshot([_entry(strategyId=1, costBasis="net@14bps"), _entry(strategyId=2)])
    entries, reason = load_snapshot(raw, _PARAMS, now_wall=_NOW)
    assert [e.strategy_id for e in entries] == [2]
    assert "cost_basis" in reason


def test_stale_strategy_fingerprint_drops_entry_and_reports_id():
    """C-6 策略身份：`params_json` 被改过（指纹失配）⇒ 丢该条目 + 降级原因里带 `strategyId`。"""
    raw = _snapshot([_entry(strategyId=12, strategyFingerprint="old"),
                     _entry(strategyId=31, strategyFingerprint="fp31")])
    entries, reason = load_snapshot(raw, _PARAMS, now_wall=_NOW,
                                    fingerprints={12: "new", 31: "fp31"})
    assert [e.strategy_id for e in entries] == [31]
    assert "strategy_fingerprint(12)" in reason


def test_fingerprints_none_skips_c6():
    """指纹表缺席（回测 / 单测路径）⇒ 跳过 C-6，不做「没有依据的丢弃」。"""
    entries, reason = load_snapshot(_snapshot([_entry()]), _PARAMS, now_wall=_NOW)
    assert len(entries) == 1 and reason is None


def test_strategy_fingerprint_is_stable_and_params_sensitive():
    assert strategy_fingerprint("ema_cross", "{}") == strategy_fingerprint("ema_cross", "{}")
    assert strategy_fingerprint("ema_cross", "{}") != strategy_fingerprint("ema_cross", "{\"a\":1}")
    assert len(strategy_fingerprint("k", "{}")) == 16


# ============================ T-8：体制降级链 ============================
def _bucketed(regime_value: str) -> list:
    raw = _snapshot([_entry(regime="all", winRate=0.51),
                     _entry(regime=regime_value, winRate=0.70)])
    entries, _ = load_snapshot(raw, _PARAMS, now_wall=_NOW)
    return entries


def test_current_regime_bucket_wins_over_all_bucket():
    prior = _resolve(_bucketed("range"), regime="range")
    assert prior is not None and prior.regime_used == "range" and prior.p == 0.7


def test_falls_back_to_all_bucket_when_current_regime_missing():
    """当前体制桶缺失 ⇒ 落 `all` 桶（体制桶天然稀疏，只认当前桶会让趋势段大面积无先验）。"""
    prior = _resolve(_bucketed("range"), regime="trend_up")
    assert prior is not None and prior.regime_used == "all" and prior.p == 0.51


def test_absent_regime_context_uses_all_bucket():
    """在线 regime 缺失（E1 关闭 / 数据不足）⇒ `all` 桶，**不是**整键缺席（P2-3）。"""
    assert _resolve(_bucketed("range"), regime=None).regime_used == "all"


def test_no_bucket_at_all_is_whole_key_absence():
    raw = _snapshot([_entry(regime="range")])
    entries, _ = load_snapshot(raw, _PARAMS, now_wall=_NOW)
    assert _resolve(entries, regime="trend_up") is None


# ============================ T-9/T-10：聚合数学 ============================
def test_three_equal_evidences_aggregate_to_the_same_p():
    """T-9：3 条 `p=0.55, ir=1` 聚合出 **0.55**（加权均值）——未归一化的 logit 和会给出 ≈0.65。"""
    raw = _snapshot([_entry(strategyId=sid) for sid in (1, 2, 3)])
    entries, _ = load_snapshot(raw, _PARAMS, now_wall=_NOW)
    prior = _resolve(entries, board=((1, "long"), (2, "long"), (3, "long")))
    assert prior is not None
    assert abs(prior.p - 0.55) < 1e-6
    assert prior.contributor_n == 3


def test_zero_weight_sum_returns_none():
    """T-10：全部 `ir ≤ 0` ⇒ 不造 p（一个假装有信息的常数比缺席更危险）。"""
    raw = _snapshot([_entry(strategyId=1, ir=0.0), _entry(strategyId=2, ir=-0.4)])
    entries, _ = load_snapshot(raw, _PARAMS, now_wall=_NOW)
    assert _resolve(entries, board=((1, "long"), (2, "long"))) is None


def test_unusable_entries_never_contribute():
    raw = _snapshot([_entry(usable=False, unusableReason="run_n<10")])
    entries, _ = load_snapshot(raw, _PARAMS, now_wall=_NOW)
    assert _resolve(entries) is None


def test_ev_is_probability_weighted_and_may_be_negative():
    """EV 为负是一个**真实结论**，不做截断（由门禁去判）。"""
    raw = _snapshot([_entry(winRate=0.30, avgWinBps=20.0, avgLossBps=40.0)])
    entries, _ = load_snapshot(raw, _PARAMS, now_wall=_NOW)
    prior = _resolve(entries)
    assert prior.ev_bps < 0
    assert abs(prior.ev_bps - (prior.p * 20.0 - (1 - prior.p) * 40.0)) < 1e-6


def test_contributors_capped_at_top_n_but_counts_are_not():
    params = resolve_prior_params(Settings(ai_trade_prior_top_n=2))
    # IR 刻意取在 `ir_clip=2.0` 之下：夹顶后权重并列，排序退化为稳定序，测不出「按权重降序」。
    raw = _snapshot([_entry(strategyId=sid, ir=0.4 * sid) for sid in (1, 2, 3, 4)])
    entries, _ = load_snapshot(raw, params, now_wall=_NOW)
    prior = _resolve(entries, board=tuple((sid, "long") for sid in (1, 2, 3, 4)),
                     params=params)
    assert len(prior.contributors) == 2 and prior.contributor_n == 4
    assert [c["strategyId"] for c in prior.contributors] == [4, 3]   # 权重降序


# ============================ G-11：事件同源 ============================
def test_strategy_entry_needs_matching_stance_not_entry_edge():
    """G-11：离线按**立场根**度量 ⇒ 在线只认 `stance == direction`。

    板上策略处于 `flat` 但本根刚发出 `open_long` 边沿——**不得**因此取用先验：那一支度量的是
    边沿分布、用的是立场分布，`p / W / L` 会失去定义。
    """
    entries, _ = load_snapshot(_snapshot([_entry()]), _PARAMS, now_wall=_NOW)
    assert _resolve(entries, board=((12, "flat"),)) is None
    assert _resolve(entries, board=((12, "short"),)) is None
    assert _resolve(entries, board=((12, "long"),)) is not None


def test_signal_entry_needs_active_key_and_none_means_unknown():
    """`signal_edge` 侧：`active_signal_keys is None` = 本轮**不知道** ⇒ 整体不参与（不是空集）。"""
    raw = _snapshot([_entry(source="signal_edge", event="trigger", signalKey="rsi_oversold",
                            strategyId=None, strategyFingerprint=None)])
    entries, _ = load_snapshot(raw, _PARAMS, now_wall=_NOW)
    assert _resolve(entries, board=(), active=None) is None
    assert _resolve(entries, board=(), active=set()) is None
    assert _resolve(entries, board=(), active={"rsi_oversold"}) is not None


# ============================ T-22 / T-14：加权共识与覆盖率口径 ============================
def test_weighted_consensus_is_none_without_priors():
    """T-14：先验缺席 ⇒ `None` ⇒ payload 整键缺席 ⇒ prompt 逐字回退。"""
    assert compute_weighted_consensus([_view(1, "long")], None) is None
    assert compute_weighted_consensus([_view(1, "long")], {"long": None, "short": None}) is None


def test_coverage_denominator_only_counts_speaking_strategies():
    """T-22：两策略有立场、其一有先验 ⇒ `0.5`；`flat` 的策略**不进分母**。"""
    entries, _ = load_snapshot(_snapshot([_entry(strategyId=12)]), _PARAMS, now_wall=_NOW)
    prior = _resolve(entries, board=((12, "long"), (31, "long")))
    board = [_view(12, "long"), _view(31, "long"), _view(99, "flat")]
    weighted = compute_weighted_consensus(board, {"long": prior, "short": None})
    assert weighted.coverage == 0.5


def test_coverage_is_none_when_whole_board_is_flat():
    """全板空仓 ⇒ `None` 而**不是** 0.0：没有分母时报 0% 是在制造一个假的故障信号。"""
    entries, _ = load_snapshot(_snapshot([_entry()]), _PARAMS, now_wall=_NOW)
    prior = _resolve(entries)
    weighted = compute_weighted_consensus([_view(12, "flat")], {"long": prior, "short": None})
    assert weighted.coverage is None


def test_weighted_net_stance_uses_tie_eps_deadzone():
    entries, _ = load_snapshot(_snapshot([_entry(strategyId=1, winRate=0.80),
                                          _entry(strategyId=2, direction="short",
                                                 winRate=0.51)]),
                               _PARAMS, now_wall=_NOW)
    board = [_view(1, "long"), _view(2, "short")]
    priors = {"long": _resolve(entries, board=((1, "long"), (2, "short"))),
              "short": _resolve(entries, direction="short",
                                board=((1, "long"), (2, "short")))}
    weighted = compute_weighted_consensus(board, priors)
    assert weighted.net_stance == "long"
    assert weighted.p_long == 0.8 and weighted.p_short == 0.51
