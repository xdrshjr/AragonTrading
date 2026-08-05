"""可挖模板目录与因子成员目录（discovery-strategy-axes §2.4）。

两条承重：
- **BL-6 / D-16**：``enum`` / ``bool`` 与 ``FROZEN_PARAM_KEYS`` 一律冻结。挖 ``htf_seconds``
  等于让每个候选随机换高周期——取帧集合无界，且非标准秒数会静默落进重采样兜底。
- **P1-6**：成员进目录前必须过 ``is_bar_compatible``，且方向**只在 IS 段**冻结。
"""
from __future__ import annotations

from app.discovery.catalogs import (
    DEFAULT_HTF_WARMUP_BARS,
    FROZEN_PARAM_KEYS,
    REASON_HTF_OVER_CAP,
    REASON_NOT_ATOMIC,
    REASON_TOO_FEW_PARAMS,
    build_ladder,
    compose_members,
    library_members,
    minable_params,
    minable_templates,
    template_family,
)
from app.engine.mtf import bar_to_seconds
from app.strategies import STRATEGY_REGISTRY

from .discovery_synthetic import lane_context, trend_candles


def test_ladders_are_bounded_and_ordered():
    ladder = build_ladder({"type": "number", "min": 0.005, "max": 0.2, "step": 0.005})
    assert ladder[0] == 0.005 and ladder[-1] == 0.2
    assert list(ladder) == sorted(ladder) and len(ladder) <= 64
    assert build_ladder({"type": "enum", "options": ["a", "b"]}) == ()
    assert build_ladder({"type": "bool"}) == ()
    assert build_ladder({"type": "int", "min": 5, "max": 5}) == ()


def test_frozen_param_keys_never_enter_the_search_space():
    """D-16：``htf_seconds`` 类型是 int，但语义是**高周期选择**——必须显式冻结。"""
    for key in ("trend_pullback_long", "trend_pullback_short"):
        schema = STRATEGY_REGISTRY[key].meta.params_schema
        assert schema["htf_seconds"]["type"] == "int"          # 通用规则不会自动拦下它
        keys = {spec.key for spec in minable_params(key, schema)}
        assert "htf_seconds" not in keys
        assert FROZEN_PARAM_KEYS[key] == frozenset({"htf_seconds"})


def test_enum_and_bool_params_are_frozen_by_the_generic_rule():
    schema = STRATEGY_REGISTRY["ema_cross_scalper"].meta.params_schema
    keys = {spec.key for spec in minable_params("ema_cross_scalper", schema)}
    assert "htf_bar" not in keys and "htf_trend_filter" not in keys
    assert {"fast", "slow"} <= keys


def test_unminable_reasons_follow_the_ordered_rule_chain():
    """有序规则链：首个命中即定。不可挖必须**写明原因**，绝不静默隐藏。"""
    catalog = minable_templates("1H", max_htf_frames=3)
    by_key = {spec.key: spec for spec in catalog.templates}
    assert by_key["regime_rotation"].reason == REASON_NOT_ATOMIC
    assert by_key["factor_score"].reason == REASON_NOT_ATOMIC
    assert not by_key["regime_rotation"].minable
    for spec in catalog.templates:
        assert spec.minable or spec.reason, f"{spec.key} 不可挖却没给原因"


def test_htf_frame_cap_evicts_templates_and_says_why():
    """帧数上限为 0 时，所有声明高周期的模板整体退出目录并给出原因。"""
    catalog = minable_templates("1H", max_htf_frames=0)
    assert catalog.htf_bars == ()
    evicted = [spec for spec in catalog.templates if spec.reason == REASON_HTF_OVER_CAP]
    assert evicted, "帧数上限没有把任何模板挤掉？高周期并集可能算错了"
    assert all(spec.htf_bars for spec in evicted)


def test_htf_union_only_keeps_strictly_higher_bars():
    catalog = minable_templates("1H", max_htf_frames=3)
    for tf in catalog.htf_bars:
        assert bar_to_seconds(tf) > bar_to_seconds("1H")


def test_htf_warmup_takes_the_maximum_over_selected_templates():
    """评审 P2-6：取小了会让最靠前的窗口在 ``start_ts`` 处 merge_asof 拿到 NaN。"""
    catalog = minable_templates("1H", max_htf_frames=3)
    assert catalog.htf_warmup_bars >= DEFAULT_HTF_WARMUP_BARS
    for spec in catalog.templates:
        if not spec.minable or not spec.htf_bars:
            continue
        cls = STRATEGY_REGISTRY[spec.key]
        wanted = int(cls(dict(cls.meta.default_params)).htf_warmup_bars())
        assert catalog.htf_warmup_bars >= wanted


def test_template_family_is_a_rule_chain_not_a_guess():
    assert template_family("momentum_breakout") == "breakout"
    assert template_family("trend_band_swing") == "trend"
    assert template_family("regime_rotation") == "rotation"
    assert template_family("not_a_template") == "other"


def test_too_few_params_is_reported_rather_than_hidden():
    catalog = minable_templates("1H", max_htf_frames=3)
    thin = [spec for spec in catalog.templates if spec.reason == REASON_TOO_FEW_PARAMS]
    assert all(len(spec.params) < 2 for spec in thin)


# ============================ 成员目录 ============================
def test_builtin_members_are_bar_compatible_and_direction_frozen():
    """P1-6：不兼容当前 bar 的因子会被 ``prepare`` 吞成安静空仓 ⇒ 伪装成 S1。"""
    from app.engine.factors import is_bar_compatible

    ctx = lane_context(trend_candles(600), config={"horizon": 6})
    members = library_members(ctx, mined_exprs=[], include_library=True)
    assert members, "内置因子一条都没进目录 ⇒ factor_combo 轴恒不 ready"
    for member in members:
        assert member.direction in ("long", "short"), "方向必须冻结，绝不留 auto"
        assert is_bar_compatible(member.factor_key, ctx.bar)


def test_intraday_only_factors_are_excluded_on_daily_bars():
    from app.engine.factors import FACTOR_LIBRARY

    intraday = {key for key, spec in FACTOR_LIBRARY.items() if spec.intraday_only}
    ctx = lane_context(trend_candles(600), bar="1D", config={"horizon": 6})
    members = library_members(ctx, mined_exprs=[], include_library=True)
    assert not ({m.factor_key for m in members} & intraday)


def test_compose_members_dedupes_and_reports_truncation():
    ctx = lane_context(trend_candles(400), config={"horizon": 6})
    library = library_members(ctx, mined_exprs=[], include_library=True)
    expr = "sub(ts_mean(close,20),ts_mean(close,60))"
    catalog = compose_members([(expr, "long"), (expr, "long")], library, 3)
    assert len(catalog.members) == 3
    assert catalog.truncated == len(library) + 1 - 3     # 同一表达式只算一条
    assert catalog.members[0].factor_key == "expr"       # 本 run kept 优先
    assert catalog.content_hash


def test_include_library_off_keeps_only_this_runs_factors():
    ctx = lane_context(trend_candles(400), config={"horizon": 6})
    assert library_members(ctx, mined_exprs=[], include_library=False) == ()
