"""装配层的 fail-closed 判据（discovery-strategy-axes §2.5.4）。

安全不变式必须立在**产出层**，不能只靠生成层的约定（上游评审的方法论结论）：
- **P1-4**：``htf_frames_by_member`` 按**展示 key** 索引；同模板的第二个实例是 ``<key>#2``，
  按 ``template_key`` 组装会让它静默走「无 MTF 数据」路径。
- **P1-6**：``FactorScoreStrategy.prepare`` 把未知 factorKey / 不兼容 bar 吞成安静空仓，
  最终伪装成 S1「样本量不足」——必须在产出层揪出来。
- **P2-8**：边界校验器抛的是 ``HTTPException``，收口成 ``eval_error:HTTPException`` 可读性极差。
- **P1-11**：combo 的 pad 必须 ≥ ``engine.combo._warmup_floor``（漏了 regime 起算根会稀释 S6）。
"""
from __future__ import annotations

from fastapi import HTTPException

from app.config import Settings
from app.discovery.backtest_lane import (
    FactorComboAssembler,
    ParamAssembler,
    RotationAssembler,
    combo_warmup_floor,
    run_split_backtest,
)
from app.discovery.lanes.base import reject_reason_of

from .discovery_synthetic import lane_context, trend_candles

_MEMBERS = [{"templateKey": "momentum_breakout", "params": {"breakout_window": 20}},
            {"templateKey": "momentum_breakout", "params": {"breakout_window": 40}},
            {"templateKey": "ema_cross_scalper", "params": {"fast": 9, "slow": 21}}]
_POLICY = {"rebalanceBars": 24, "lookbackBars": 168, "minDwellBars": 24,
           "switchMarginPct": 0.1}
# 解析得通、但**求值全 NaN**的表达式（节点数低于最小复杂度门槛 ⇒ `evaluate_expression_text`
# 走降级契约返回全 NaN，且**不抛错**）。这正是 P1-6 要防的那条静默路径：边界校验器放行，
# `prepare` 安静空仓，最终伪装成 S1「样本量不足」。
_SILENT_NAN_EXPR = "ts_zscore(volume,30)"


def _rotation(members=None) -> RotationAssembler:
    return RotationAssembler(members or _MEMBERS, _POLICY, Settings())


# ============================ P1-4：同模板重复成员的展示 key ============================
def test_repeated_template_members_get_distinct_display_keys():
    members = _rotation().members()
    keys = [member.key for member in members]
    assert keys == ["momentum_breakout", "momentum_breakout#2", "ema_cross_scalper"]
    assert len(set(keys)) == len(keys)


def test_htf_frames_are_indexed_by_display_key_not_template_key():
    ctx = lane_context(trend_candles(400), htf_frames={"4H": []})
    assembler = _rotation()
    members = assembler.members()
    frames = assembler.htf_frames_by_member(ctx, members)
    assert set(frames) == {member.key for member in members}
    assert "momentum_breakout#2" in frames, "#2 成员会静默走「无 MTF 数据」路径"


def test_missing_frames_for_a_member_are_rejected_fail_closed():
    """成员声明了高周期却没取到帧 ⇒ ``S0:missing_htf:<memberKey>:<tf>``，绝不带病回测。"""
    members = [{"templateKey": "trend_band_swing", "params": {}},
               {"templateKey": "momentum_breakout", "params": {}}]
    ctx = lane_context(trend_candles(400), htf_frames={})
    reason = _rotation(members).preflight(ctx)
    assert reason.startswith("S0:missing_htf:trend_band_swing:")


def test_single_strategy_missing_frames_is_rejected_too():
    ctx = lane_context(trend_candles(400), htf_frames={})
    assert ParamAssembler("trend_band_swing", {}).preflight(ctx).startswith("S0:missing_htf:")
    assert ParamAssembler("momentum_breakout", {}).preflight(ctx) == ""


# ============================ P1-6：安静空仓的产出层判据 ============================
def test_score_unavailable_is_detected_instead_of_masquerading_as_thin_samples():
    """构造一个与当前 bar 不兼容的成员：``prepare`` 不抛错，只是不交易。"""
    members = [{"factorKey": "expr", "params": {"expr": _SILENT_NAN_EXPR},
                "direction": "long", "name": "expr:0000000a", "weight": 1.0}]
    assembler = FactorComboAssembler(members, {})
    strategy = assembler.build()
    strategy.prepare(lane_context(trend_candles(400)).df_full, "1H")
    assert assembler.postflight(strategy) == "S0:score_unavailable"


def test_healthy_combo_passes_postflight():
    members = [{"factorKey": "expr", "params": {"expr": "sub(close,ts_mean(close,20))"},
                "direction": "long", "name": "expr:0000000b", "weight": 1.0}]
    assembler = FactorComboAssembler(members, {})
    strategy = assembler.build()
    strategy.prepare(lane_context(trend_candles(400)).df_full, "1H")
    assert assembler.postflight(strategy) == ""


def test_split_backtest_short_circuits_on_score_unavailable():
    members = [{"factorKey": "expr", "params": {"expr": _SILENT_NAN_EXPR},
                "direction": "long", "name": "expr:0000000a", "weight": 1.0}]
    split = run_split_backtest(FactorComboAssembler(members, {}),
                               lane_context(trend_candles(500)))
    assert split.reject_reason == "S0:score_unavailable"
    assert split.backtests == 1, "已经确定不可用还跑第二段 ⇒ 白付一次回测"


# ============================ P2-8：边界校验器的异常收口 ============================
def test_boundary_validators_map_to_a_readable_reject_code():
    exc = HTTPException(400, detail={"code": "COMBO_MEMBERS", "message": "至少 2 个"})
    assert reject_reason_of(exc) == "S0:assemble_rejected:COMBO_MEMBERS"
    assert reject_reason_of(ValueError("boom")) == "eval_error:ValueError"


def test_strict_member_assembly_raises_instead_of_silently_dropping():
    """``strict=True``：不可用成员**报错而非静默跳过**（默认 best-effort 会把 4 成员变 2 成员）。"""
    bad = [{"templateKey": "no_such_template", "params": {}},
           {"templateKey": "momentum_breakout", "params": {}},
           {"templateKey": "ema_cross_scalper", "params": {}}]
    try:
        _rotation(bad).members()
        raise AssertionError("未知模板竟然被静默跳过了")
    except HTTPException as exc:
        assert reject_reason_of(exc).startswith("S0:assemble_rejected:")


# ============================ P1-11：combo 的 pad 与 _warmup_floor 同源 ============================
def test_combo_pad_is_never_below_the_engine_warmup_floor():
    """``lookbackBars`` 可低至 24；漏了 regime 起算根会让 OOS 头部混进一截恒定权益。"""
    from app.engine.combo import _warmup_floor

    assembler = RotationAssembler(_MEMBERS, {**_POLICY, "lookbackBars": 24}, Settings())
    members, policy = assembler.members(), assembler.policy()
    floor = _warmup_floor(members, policy, None)
    assert combo_warmup_floor(members, policy) == floor
    split = run_split_backtest(assembler, lane_context(trend_candles(900)))
    assert split.pad >= floor
    assert split.clip_mode == "rebased", "轮动轴的 OOS 口径必须如实标注为 rebased（BL-3）"


def test_rotation_backtest_count_scales_with_member_number():
    """评审 P1-3：一个轮动候选背后是 ``2 × rotationSize`` 次底层回测。"""
    split = run_split_backtest(_rotation(), lane_context(trend_candles(900)))
    assert split.backtests == 2 * len(_MEMBERS)
