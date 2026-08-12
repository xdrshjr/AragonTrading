"""交易纪律预设的纯核心与解析层（ai-trade-discipline-preset-and-integrity 轴 A）。

覆盖判据：G-3（R-1 共识夹紧）/ G-4（R-2 现算 primingBars 抬升）/ G-10（`armed=false` 必带 note）/
G-11（每个预设过真实校验器）/ G-18（运维总闸一票关闭）/ [P1-5]（关闭 + 覆盖字段 ⇒ 422）。

零回归支点：`off` / `enabled=false` / 总闸关三条路径都必须返回**同一个** settings 对象（`is` 相等）。
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.ai_auto.discipline import (
    OVERRIDE_TO_SETTING,
    PRESET_IDS,
    preset_rows,
    preset_table,
    resolve_updates,
)
from app.api.ai_auto import _resolve_discipline
from app.config import Settings
from app.schemas import AiTradeDisciplineIn


def _settings(**over) -> Settings:
    # [P2-4] `_env_file=None`：`Settings(**...)` 在 `BaseSettings` 下会再读一次 env/.env，不隔离
    # 的话本机 `.env` 会让本文件的门禁时绿时红。
    return Settings(_env_file=None, **over)


def _in(**over) -> AiTradeDisciplineIn:
    return AiTradeDisciplineIn(**over)


# ============================ 预设表本身 ============================
def test_named_presets_arm_every_dark_switch():
    """三个具名预设都必须把「默认是暗的」那几条闸真的点亮——这是本轮的价值主张本身。"""
    for preset_id in ("steady", "balanced", "aggressive"):
        updates = preset_table(preset_id)
        assert updates["ai_trade_sizing_enabled"] is True
        assert updates["ai_trade_edge_gate_k_min"] > 0
        assert updates["ai_trade_stop_atr_mult"] > 0
        assert updates["ai_trade_brake_slow_dd_pct"] > 0
        assert updates["ai_trade_brake_halt_dd_pct"] > updates["ai_trade_brake_slow_dd_pct"]
        assert updates["ai_trade_brake_loss_streak"] > 0
        assert updates["ai_trade_effective_exposure_cap_mult"] >= 1.0   # 值域 {0} ∪ [1,10]


def test_presets_are_monotonic_from_steady_to_aggressive():
    """稳健 → 均衡 → 激进：门槛只放松、仓位只放大。表里手滑写反一个数，这条会红。"""
    steady, balanced, aggressive = (preset_table(p) for p in
                                    ("steady", "balanced", "aggressive"))
    for key in ("ai_trade_edge_gate_k_min", "ai_trade_min_consensus", "ai_trade_min_confidence"):
        assert steady[key] >= balanced[key] >= aggressive[key], key
    for key in ("ai_trade_vol_target_annual", "ai_trade_kelly_lambda",
                "ai_trade_effective_exposure_cap_mult", "ai_trade_brake_slow_dd_pct",
                "ai_trade_brake_halt_dd_pct", "ai_trade_brake_loss_streak"):
        assert steady[key] <= balanced[key] <= aggressive[key], key


def test_off_yields_empty_updates_and_is_not_armed():
    preset = resolve_updates("off", None, spec_count=3)
    assert preset.updates == {} and preset.armed is False and preset.f_floor == 1.0


def test_unknown_preset_degrades_to_off_rather_than_guessing():
    """未知 id 一律按 `off`：宁可不装配，也不猜一组阈值去开真实仓位。"""
    assert resolve_updates("moon", None, spec_count=3).preset_id == "off"


@pytest.mark.parametrize("preset_id", [p for p in PRESET_IDS if p != "off"])
def test_every_preset_survives_the_real_validator(preset_id):
    """G-11：`model_copy(update=)` 绕过 pydantic 校验，故这里让**真实校验器**跑一遍每个预设。"""
    base = _settings()
    merged = Settings(_env_file=None, **{**base.model_dump(), **preset_table(preset_id)})
    for key, value in preset_table(preset_id).items():
        assert getattr(merged, key) == value


# ============================ R-1 共识夹紧（G-3）============================
def test_min_consensus_is_clamped_to_the_real_strategy_count():
    """单策略会话选 `steady`（门槛 2）⇒ 夹到 1，且**如实回执**（绝不静默夹紧）。"""
    preset = resolve_updates("steady", None, spec_count=1)
    assert preset.updates["ai_trade_min_consensus"] == 1
    adj = [a for a in preset.adjustments if a["field"] == "minConsensus"]
    assert len(adj) == 1 and adj[0]["from"] == 2 and adj[0]["to"] == 1
    assert "夹紧" in adj[0]["reason"]


def test_no_clamp_note_when_the_strategy_count_is_sufficient():
    preset = resolve_updates("steady", None, spec_count=5)
    assert preset.updates["ai_trade_min_consensus"] == 2 and preset.adjustments == []


def test_override_min_consensus_is_clamped_too():
    """覆盖字段同样受 R-1 约束——否则用户填 8、只有 2 个策略时开仓永久不可达。"""
    preset = resolve_updates("balanced", {"minConsensus": 8}, spec_count=2)
    assert preset.updates["ai_trade_min_consensus"] == 2 and preset.adjustments


# ============================ 覆盖字段 ============================
def test_overrides_replace_preset_values_and_keep_int_typing():
    preset = resolve_updates("balanced", {"edgeGateK": 3.5, "brakeLossStreak": 9}, spec_count=3)
    assert preset.updates["ai_trade_edge_gate_k_min"] == 3.5
    assert preset.updates["ai_trade_brake_loss_streak"] == 9
    assert isinstance(preset.updates["ai_trade_brake_loss_streak"], int)


def test_override_map_matches_the_schema_fields_exactly():
    """映射表与入参 schema 必须一一对应：任一侧加字段、另一侧忘了加 ⇒ 那个字段静默无效。"""
    schema_fields = set(AiTradeDisciplineIn.model_fields) - {"enabled", "preset"}
    assert schema_fields == set(OVERRIDE_TO_SETTING)


def test_zero_is_an_explicit_off_switch_not_a_missing_value():
    """`0` 表示「显式关闭该闸」，绝不能被当成「没填」而回落预设值。"""
    preset = resolve_updates("balanced", {"stopAtrMult": 0.0, "minConfidence": 0.0}, spec_count=3)
    assert preset.updates["ai_trade_stop_atr_mult"] == 0.0
    assert preset.updates["ai_trade_min_confidence"] == 0.0


# ============================ API 层解析（零回归 / 总闸 / 冲突 / R-2）============================
def test_default_request_arms_the_server_default_preset():
    base = _settings()
    derived, preset, bars = _resolve_discipline(None, base, spec_count=3, priming_bars=300)
    assert preset.preset_id == "balanced"
    assert derived is not base and derived.ai_trade_sizing_enabled is True
    assert derived.ai_trade_edge_gate_k_min == 1.5
    assert base.ai_trade_sizing_enabled is False          # 绝不改原对象
    assert bars == 300


@pytest.mark.parametrize("body", [
    _in(enabled=False),
    _in(preset="off"),
])
def test_user_off_returns_the_very_same_settings_object(body):
    """§7.1 零回归支点：`is` 相等——「关闭」这条路径与本特性合入前逐字节相同。"""
    base = _settings()
    derived, preset, bars = _resolve_discipline(body, base, spec_count=3, priming_bars=300)
    assert derived is base and preset.preset_id == "off" and bars == 300


def test_ops_master_switch_beats_the_request_body():
    """G-18：`ai_trade_discipline_enabled=False` ⇒ 无论请求体填什么都不装配（一键回滚）。"""
    base = _settings(ai_trade_discipline_enabled=False)
    derived, preset, _ = _resolve_discipline(_in(preset="steady", edgeGateK=2.0), base,
                                             spec_count=3, priming_bars=300)
    assert derived is base and preset.preset_id == "off"


@pytest.mark.parametrize("body", [
    _in(enabled=False, edgeGateK=2.0),
    _in(preset="off", minConsensus=2),
])
def test_closing_the_layer_while_giving_overrides_is_rejected(body):
    """[P1-5]：矛盾组合一律 422 并点名字段——静默丢弃用户显式输入是本仓库明令禁止的形状。"""
    with pytest.raises(HTTPException) as exc:
        _resolve_discipline(body, _settings(), spec_count=3, priming_bars=300)
    assert exc.value.status_code == 422
    assert exc.value.detail["code"] == "AI_TRADE_DISCIPLINE_CONFLICT"
    assert "edgeGateK" in exc.value.detail["message"] or \
           "minConsensus" in exc.value.detail["message"]


def test_priming_bars_are_raised_from_the_derived_settings_not_a_hardcoded_97():
    """G-4 / [P1-3]：抬升值必须**现算**——运维调大相关性样本数，抬升值须随之变。"""
    _, preset, bars = _resolve_discipline(None, _settings(), spec_count=3, priming_bars=60)
    assert bars == 97                      # max(vol_window=96, corr_min_samples=96) + 1
    adj = [a for a in preset.adjustments if a["field"] == "primingBars"]
    assert len(adj) == 1 and adj[0]["from"] == 60 and adj[0]["to"] == 97

    base = _settings(ai_trade_correlation_min_samples=200)
    _, preset2, bars2 = _resolve_discipline(None, base, spec_count=3, priming_bars=60)
    assert bars2 == 201, "写死 97 的实现会在这里红——这正是这条用例存在的理由"
    assert preset2.adjustments[0]["to"] == 201


def test_default_priming_bars_do_not_trigger_the_raise():
    """澄清：默认 `primingBars=300` 时这条抬升**不触发**（G-4 必须显式构造 60 才验得到）。"""
    _, preset, bars = _resolve_discipline(None, _settings(), spec_count=3, priming_bars=300)
    assert bars == 300
    assert not [a for a in preset.adjustments if a["field"] == "primingBars"]


def test_backtest_caller_passes_zero_priming_bars_and_never_gets_a_raise():
    """回测无暖机根数概念 ⇒ 传 0 ⇒ 恒不触发抬升（否则每次回测都会多一条假调整项）。"""
    _, preset, bars = _resolve_discipline(None, _settings(), spec_count=1, priming_bars=0)
    assert bars == 0
    assert not [a for a in preset.adjustments if a["field"] == "primingBars"]


def test_profit_guard_threshold_lands_in_the_derived_copy():
    """G-13 的**前半段**（后半段是会话级顺序断言）：预设确实改得动利润保护的硬止盈。"""
    derived, _, _ = _resolve_discipline(_in(preset="steady"), _settings(),
                                        spec_count=3, priming_bars=300)
    assert derived.ai_trade_profit_guard_hard_tp_return_pct == 0.30


# ============================ 只读阈值表端点（弹窗数据源）============================
def test_preset_rows_render_zero_as_an_explicit_off_switch():
    """`0` 是「这条闸在本预设下显式关闭」，不是「一个很小的数」——UI 据 `off` 显示「关闭」。"""
    rows = {row["key"]: row for row in preset_rows("aggressive")}
    hard_tp = rows["ai_trade_profit_guard_hard_tp_return_pct"]
    assert hard_tp["off"] is True and hard_tp["value"] == "0%"
    assert rows["ai_trade_edge_gate_k_min"]["off"] is False


def test_preset_rows_are_empty_for_off_and_labelled_in_chinese_for_the_rest():
    assert preset_rows("off") == []
    for preset_id in ("steady", "balanced", "aggressive"):
        rows = preset_rows(preset_id)
        assert rows, preset_id
        assert all(row["label"] and row["value"] for row in rows), preset_id
        # 每一个下发的键都必须真的在冻结表里（否则 UI 会显示一个不生效的数）。
        table = preset_table(preset_id)
        assert all(row["key"] in table for row in rows), preset_id


def test_every_threshold_in_the_table_has_a_chinese_label():
    """漏标签的阈值会被 `preset_rows` 静默跳过 ⇒ 用户在弹窗里看不到它，却会在实盘吃到它。"""
    labelled = {row["key"] for row in preset_rows("balanced")}
    numeric = {key for key, value in preset_table("balanced").items()
               if not isinstance(value, bool)}
    assert numeric - labelled == set(), f"这些阈值没有中文标签，弹窗里看不见：{numeric - labelled}"
