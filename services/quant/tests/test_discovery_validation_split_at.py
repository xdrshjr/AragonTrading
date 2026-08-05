"""``evaluate_split`` 的 ``split_at`` / ``frozen_direction`` 等价门禁（discovery-asset-ledger §2.4）。

**两阶段落地**（沿用上游 BL-8）：本文件的 golden 常量是在**改动 ``evaluate_split`` 之前**、
在当时的 HEAD 上实测出来并写死的；改动之后 ``split_at=None && frozen_direction=""`` 必须
逐条相等。常量**绝不**在测试内重新计算——那样等价门禁就只是在和自己比。

六项：``direction`` / ``coverage`` / ``res_is.rankIc`` / ``res_is.ir`` / ``ic_is`` / ``ic_oos``。
"""
from __future__ import annotations

import pytest

from app.discovery.expression import parse_expression
from app.discovery.validation import ValidationContext, evaluate_split, split_index
from app.engine.backtester import candles_to_df

from .discovery_synthetic import trend_candles

GOLDEN_EXPR = "sub(ts_mean(ret,20),ts_mean(ret,60))"
GOLDEN_BARS = 1200
GOLDEN_SEED = 5
GOLDEN_HORIZON = 6

GOLDEN_DIRECTION = "long"
GOLDEN_COVERAGE = 0.95
GOLDEN_IS_RANK_IC = 0.059935
GOLDEN_IS_IR = 1.187469
GOLDEN_IC_IS = [0.025687, 0.096809, 0.104899, 0.002972, 0.057875, 0.115997,
                0.030815, 0.007867, 0.110485, 0.119843, -0.005632, 0.025874]
GOLDEN_IC_OOS = [0.682759, 0.541713, 0.412808, 0.187987, 0.006897, -0.224917,
                 -0.440887, -0.535484, -0.634483, -0.663626, -0.690148, -0.614238]


@pytest.fixture
def golden_ctx() -> ValidationContext:
    df = candles_to_df(trend_candles(GOLDEN_BARS, seed=GOLDEN_SEED))
    return ValidationContext(df_full=df, horizon=GOLDEN_HORIZON)


def test_default_fields_reproduce_golden_constants(golden_ctx: ValidationContext) -> None:
    """两个新字段取默认值时，六项产物与改动前**逐位相等**（R-7 的机器判据）。"""
    result = evaluate_split(parse_expression(GOLDEN_EXPR), golden_ctx)
    assert result.direction == GOLDEN_DIRECTION
    assert round(float(result.coverage), 10) == GOLDEN_COVERAGE
    assert round(float(result.res_is.horizons[0].rankIc), 10) == GOLDEN_IS_RANK_IC
    assert round(float(result.res_is.horizons[0].ir), 10) == GOLDEN_IS_IR
    assert [round(v, 10) for v in result.ic_is] == GOLDEN_IC_IS
    assert [round(v, 10) for v in result.ic_oos] == GOLDEN_IC_OOS


def test_split_at_none_equals_split_index(golden_ctx: ValidationContext) -> None:
    """显式传入 ``split_index(...)`` 的结果与不传逐位相同（默认分支就是那条老路）。"""
    node = parse_expression(GOLDEN_EXPR)
    cut = split_index(len(golden_ctx.df_full), golden_ctx.is_ratio)
    explicit = evaluate_split(node, ValidationContext(
        df_full=golden_ctx.df_full, horizon=GOLDEN_HORIZON, split_at=cut))
    assert explicit.direction == GOLDEN_DIRECTION
    assert [round(v, 10) for v in explicit.ic_is] == GOLDEN_IC_IS
    assert [round(v, 10) for v in explicit.ic_oos] == GOLDEN_IC_OOS


def test_split_at_controls_segment_lengths(golden_ctx: ValidationContext) -> None:
    """``split_at`` 落在指定下标时两段长度正确（体检靠这条把 fresh 段切出来）。"""
    node = parse_expression(GOLDEN_EXPR)
    result = evaluate_split(node, ValidationContext(
        df_full=golden_ctx.df_full, horizon=GOLDEN_HORIZON, split_at=900))
    assert int(result.res_is.bars) == 900
    assert int(result.res_oos.bars) == GOLDEN_BARS - 900


@pytest.mark.parametrize("raw, expected_is_bars", [(-10, 1), (0, 1),
                                                   (GOLDEN_BARS + 50, GOLDEN_BARS - 1)])
def test_split_at_out_of_range_is_clamped(golden_ctx: ValidationContext, raw: int,
                                          expected_is_bars: int) -> None:
    """越界的 ``split_at`` 退化为端点而不是抛错（体检窗口口径由上游保证，这里只兜底）。"""
    result = evaluate_split(parse_expression(GOLDEN_EXPR), ValidationContext(
        df_full=golden_ctx.df_full, horizon=GOLDEN_HORIZON, split_at=raw))
    assert int(result.res_is.bars) == expected_is_bars


def test_frozen_direction_overrides_auto(golden_ctx: ValidationContext) -> None:
    """冻结方向时不再走 ``resolve_direction``；非法取值回落 auto（不静默改语义）。"""
    node = parse_expression(GOLDEN_EXPR)
    frozen = evaluate_split(node, ValidationContext(
        df_full=golden_ctx.df_full, horizon=GOLDEN_HORIZON, frozen_direction="short"))
    assert frozen.direction == "short"
    bogus = evaluate_split(node, ValidationContext(
        df_full=golden_ctx.df_full, horizon=GOLDEN_HORIZON, frozen_direction="sideways"))
    assert bogus.direction == GOLDEN_DIRECTION
