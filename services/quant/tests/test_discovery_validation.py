"""验证层测试（strategy-factor-discovery §7.1）—— 含四条**承重**用例。

承重用例（缺一即放行条件 2 不满足）：
① **F6 非空转**（评审 P0-1）：IS 强、OOS 反号 ⇒ 必须拒。实现若误把 ``direction="auto"``
   传给 OOS，两段 rankIc 都会被构造成非负，本条必红——这是唯一能证明 P0-1 没复活的机器判据。
② **F8 量纲**（评审 P0-2）：生产默认成本下门槛落在**千分位**（0.0028），而非两位数。
③ **F9 ``turnover_proxy``**（评审 P1-2）：逐根翻档 → 趋近 1；恒定档位 → 趋近 0；样本 <2 → None ⇒ 拒。
④ **IS/OOS 同源求值**（评审 P1-6）：OOS 段前 ``max_window`` 根**不是** NaN，
   即证明用的是全窗求值后切片，而非切片后重算。
"""
from __future__ import annotations

import math
from dataclasses import replace

import numpy as np
import pytest

from app.discovery.expression import WINDOW_LADDER, parse_expression
from app.discovery.validation import (
    DEFAULT_THRESHOLDS,
    KeptEntry,
    KeptRow,
    ValidationContext,
    _check_cost_gate,
    _check_oos_gate,
    evaluate_split,
    normalize_thresholds,
    rebuild_kept_pool,
    required_ir,
    round_trip_cost,
    segment_ic_series,
    sign_stability,
    split_index,
    turnover_proxy,
    validate_candidate,
)
from app.engine.backtester import candles_to_df

# 3 节点（过 MIN_NODES 门槛，与生产里「必先过审查层」的候选同形）；
# ``neg`` 只翻符号，方向由 IS 段定号吸收，指标绝对值与裸 zscore 一致。
_DRIVER = "neg(ts_zscore(volume,20))"


def _driven_candles(n: int, flip_at: int) -> list[dict]:
    """构造「前瞻收益由成交量信号驱动」的确定性 K 线；``flip_at`` 之后驱动方向翻转。

    这样 ``ts_zscore(volume,20)`` 在 ``flip_at`` 之前有强正 RankIC、之后强负 RankIC，
    可以精确控制 IS / OOS 的符号关系（F6 双向用例的地基）。
    """
    rows: list[dict] = []
    price, ts = 100.0, 1_700_000_000
    for i in range(n):
        sig = math.sin(i / 5.0)
        k = 0.012 if i < flip_at else -0.012
        open_, close = price, price * (1.0 + k * sig)
        rows.append({"ts": ts + i * 3600, "o": round(open_, 4),
                     "h": round(max(open_, close) * 1.001, 4),
                     "l": round(min(open_, close) * 0.999, 4),
                     "c": round(close, 4), "vol": 1000.0 * (1.0 + 0.6 * sig)})
        price = close
    return rows


def _ctx(flip: bool, n: int = 1200, **overrides) -> ValidationContext:
    flip_at = split_index(n, 0.7) if flip else n + 1
    df = candles_to_df(_driven_candles(n, flip_at))
    base = ValidationContext(df_full=df, horizon=1, is_ratio=0.7,
                             thresholds=dict(DEFAULT_THRESHOLDS))
    return replace(base, **overrides) if overrides else base


# ============================ ① F6 非空转（P0-1 承重） ============================
def test_f6_rejects_out_of_sample_sign_flip():
    """IS 强、OOS 反号 ⇒ F6 必须拒。误传 ``auto`` 给 OOS 时本条必红。"""
    ctx = _ctx(flip=True)
    split = evaluate_split(parse_expression(_DRIVER), ctx)
    ic_is = split.res_is.horizons[0].rankIc
    ic_oos = split.res_oos.horizons[0].rankIc
    assert ic_is > 0.3, "夹具应在样本内给出强 RankIC"
    assert ic_oos < -0.3, "OOS 必须在**冻结方向**下算出负值（auto 会把它构造成非负）"
    assert _check_oos_gate(split, ctx.thresholds) == "F6"
    outcome = validate_candidate(parse_expression(_DRIVER), ctx, pool=[], trials=10)
    assert outcome.verdict == "rejected" and outcome.reject_reason == "F6"


def test_f6_passes_when_out_of_sample_keeps_sign_and_strength():
    ctx = _ctx(flip=False)
    split = evaluate_split(parse_expression(_DRIVER), ctx)
    assert split.res_is.horizons[0].rankIc > 0.3
    assert split.res_oos.horizons[0].rankIc > 0.3
    assert _check_oos_gate(split, ctx.thresholds) == ""


def test_f6_rejects_when_out_of_sample_strength_decays_too_much():
    """同号但强度衰减超过 ``oosRetain`` 也要拒（保留 50% 强度是 F6 的另一半）。"""
    ctx = _ctx(flip=False)
    split = evaluate_split(parse_expression(_DRIVER), ctx)
    strict = {**ctx.thresholds, "oosRetain": 1.5}
    assert _check_oos_gate(split, strict) == "F6"


# ============================ ② F8 量纲（P0-2 承重） ============================
def test_round_trip_cost_is_a_decimal_not_bps():
    """生产默认（taker 5 bps + 滑点 2 bps）往返成本 = 0.0014，**不是** 14 或 28。"""
    assert round_trip_cost(5.0, 2.0) == pytest.approx(0.0014)
    assert round_trip_cost(0.0, 0.0) == 0.0


class _FakeStat:
    def __init__(self, spread):
        self.topBottomSpread = spread
        self.rankIc = self.ic = self.ir = self.tStat = self.monotonicity = None


class _FakeResult:
    def __init__(self, spread):
        self.horizons = [_FakeStat(spread)]


class _FakeSplit:
    def __init__(self, spread):
        self.res_is = _FakeResult(spread)


@pytest.mark.parametrize("spread,expected", [(0.003, ""), (0.001, "F8")])
def test_f8_threshold_lands_in_the_per_mille_range(spread, expected):
    """门槛 = costMult(2.0) × 0.0014 = 0.0028：0.003 过、0.001 不过。

    数值门槛落在千分位而非两位数，即证明没有再犯 ``bps`` 与小数的 1e4 错配（评审 P0-2）。
    """
    ctx = ValidationContext(df_full=candles_to_df(_driven_candles(50, 999)), horizon=1,
                            fee_bps=5.0, slip_bps=2.0,
                            thresholds=dict(DEFAULT_THRESHOLDS))
    assert _check_cost_gate(_FakeSplit(spread), ctx) == expected
    gate = float(ctx.thresholds["costMult"]) * round_trip_cost(ctx.fee_bps, ctx.slip_bps)
    assert 0.001 < gate < 0.01


def test_f8_rejects_missing_spread():
    ctx = ValidationContext(df_full=candles_to_df(_driven_candles(50, 999)), horizon=1,
                            thresholds=dict(DEFAULT_THRESHOLDS))
    assert _check_cost_gate(_FakeSplit(None), ctx) == "F8"


# ============================ ③ F9 换手代理（P1-2 承重） ============================
def test_turnover_proxy_extremes():
    alternating = np.array([float(i % 5) for i in range(500)])
    assert turnover_proxy(alternating) == pytest.approx(1.0, abs=0.02)
    monotone = np.arange(500, dtype=float)   # 分档随时间单调推进 ⇒ 极少切换
    assert turnover_proxy(monotone) < 0.02


def test_turnover_proxy_returns_none_and_gate_fails_closed():
    """有限样本 < 2 / 分档退化 → None ⇒ F9 拒（验证层 fail-closed，不可判定即不入库）。"""
    assert turnover_proxy(np.array([np.nan, np.nan, 1.0])) is None
    assert turnover_proxy(np.full(200, 3.0)) is None
    ctx = _ctx(flip=False)
    constant = parse_expression("mul(0,ts_mean(volume,20))")
    outcome = validate_candidate(constant, ctx, pool=[], trials=10)
    assert outcome.verdict == "rejected"
    assert outcome.metrics["turnoverProxy"] is None


def test_f9_rejects_high_turnover():
    ctx = _ctx(flip=False, thresholds={**DEFAULT_THRESHOLDS, "maxTurnover": 0.0001})
    outcome = validate_candidate(parse_expression(_DRIVER), ctx, pool=[], trials=10)
    assert outcome.verdict == "rejected" and outcome.reject_reason == "F9"


# ============================ ④ IS/OOS 同源求值（P1-6 承重） ============================
def test_out_of_sample_head_is_not_nan_because_evaluation_is_full_window():
    """OOS 段**前 240 根**（窗口梯子顶格）不是 NaN。

    若实现改成「先切片再各自 compute」，OOS 段前 ``max_window`` 根必然全 NaN，本条即红——
    这是 P1-6 的机器判据（默认 30% 尾段约 360 根，白丢 240 根即 2/3）。
    """
    ctx = _ctx(flip=False)
    max_window = WINDOW_LADDER[-1]
    node = parse_expression(f"ts_zscore(volume,{max_window})")
    split = evaluate_split(node, ctx)
    cut = split_index(len(ctx.df_full), ctx.is_ratio)
    head = split.values[cut:cut + max_window]
    assert len(head) == max_window
    assert np.isfinite(head).all(), "OOS 段头部出现 NaN ⇒ 暖机被重置，两段不是同一条序列"


def test_split_is_strictly_chronological():
    ctx = _ctx(flip=False)
    cut = split_index(len(ctx.df_full), ctx.is_ratio)
    df = ctx.df_full.reset_index(drop=True)
    assert df["ts"].iloc[cut - 1] < df["ts"].iloc[cut]
    assert cut == int(len(df) * 0.7)


# ============================ F1–F5 / F7 / F10 / F11 ============================
def test_f1_rejects_short_sample():
    ctx = _ctx(flip=False, thresholds={**DEFAULT_THRESHOLDS, "minSamples": 100000})
    outcome = validate_candidate(parse_expression(_DRIVER), ctx, pool=[], trials=10)
    assert outcome.verdict == "rejected" and outcome.reject_reason == "F1"


def test_f2_rejects_weak_rank_ic():
    ctx = _ctx(flip=False, thresholds={**DEFAULT_THRESHOLDS, "minAbsIc": 0.99})
    outcome = validate_candidate(parse_expression(_DRIVER), ctx, pool=[], trials=10)
    assert outcome.verdict == "rejected" and outcome.reject_reason == "F2"


def test_f7_rejects_non_monotonic():
    ctx = _ctx(flip=False, thresholds={**DEFAULT_THRESHOLDS, "minMonotonicity": 1.1})
    outcome = validate_candidate(parse_expression(_DRIVER), ctx, pool=[], trials=10)
    assert outcome.verdict == "rejected" and outcome.reject_reason == "F7"


def test_f11_penalty_raises_the_bar_with_trials():
    """门槛随 ``log10(trials)`` 单调抬高——这是 R-1 多重检验风险最直接的解药。"""
    thresholds = dict(DEFAULT_THRESHOLDS)
    assert required_ir(10, thresholds) == pytest.approx(thresholds["minAbsIr"])
    assert required_ir(100, thresholds) > required_ir(10, thresholds)
    assert required_ir(10000, thresholds) > required_ir(100, thresholds)
    assert required_ir(1, thresholds) == pytest.approx(required_ir(10, thresholds))
    no_penalty = {**thresholds, "trialPenaltyK": 0.0}
    assert required_ir(10 ** 6, no_penalty) == pytest.approx(thresholds["minAbsIr"])


def test_f11_reason_is_reported_when_penalty_is_the_binding_constraint():
    ctx = _ctx(flip=False, thresholds={**DEFAULT_THRESHOLDS, "minAbsIr": 500.0})
    low = validate_candidate(parse_expression(_DRIVER), ctx, pool=[], trials=10)
    assert low.reject_reason == "F3"           # 惩罚未生效时报基础闸
    high = validate_candidate(parse_expression(_DRIVER), ctx, pool=[], trials=10 ** 6)
    assert high.reject_reason == "F11"         # 惩罚抬高后报多重检验闸
    assert high.metrics["requiredIr"] > low.metrics["requiredIr"]


def test_f10_dedupes_against_kept_pool_on_both_channels():
    """与已入库因子在**值序列**或**分段 IC 序列**任一通道过相关即拒（双通道，§2.7 F10）。"""
    ctx = _ctx(flip=False)
    first = validate_candidate(parse_expression(_DRIVER), ctx, pool=[], trials=10)
    assert first.verdict == "kept", first.reject_reason
    pool = [KeptEntry(candidate_id=1, expr_text=_DRIVER, direction=first.direction,
                      values=first.values, ic_series=first.ic_series, score=first.score)]
    twin = validate_candidate(parse_expression(_DRIVER), ctx, pool=pool,
                              trials=11)
    assert twin.verdict == "rejected" and twin.reject_reason == "F10"
    assert twin.metrics["maxValueCorr"] > 0.85


def test_a_real_candidate_can_pass_all_eleven_gates():
    """DoD #4：至少一个候选能通过 11 条闸入库，且指标里带 turnoverProxy / trials。"""
    ctx = _ctx(flip=False)
    outcome = validate_candidate(parse_expression(_DRIVER), ctx, pool=[], trials=42)
    assert outcome.verdict == "kept", outcome.reject_reason
    assert outcome.values is not None and len(outcome.ic_series) >= 6
    assert outcome.metrics["turnoverProxy"] is not None
    assert outcome.metrics["trials"] == 42
    assert outcome.metrics["is"]["rankIc"] is not None
    assert outcome.metrics["oos"]["rankIc"] is not None
    assert len(outcome.series["isIc"]) <= 24 and len(outcome.series["quantiles"]) <= 10


# ============================ 辅助纯函数 ============================
def test_sign_stability():
    assert sign_stability([0.1, 0.2, 0.3]) == 1.0
    assert sign_stability([0.1, -0.2, 0.3, -0.4]) == 0.5
    assert sign_stability([]) is None


def test_segment_ic_series_is_bounded_and_directional():
    ctx = _ctx(flip=False)
    df = ctx.df_full.reset_index(drop=True)
    split = evaluate_split(parse_expression(_DRIVER), ctx)
    long_side = segment_ic_series(split.values, df["c"].to_numpy(float), 1, dir_mult=1.0)
    short_side = segment_ic_series(split.values, df["c"].to_numpy(float), 1, dir_mult=-1.0)
    assert 0 < len(long_side) <= 12
    assert all(a == pytest.approx(-b) for a, b in zip(long_side, short_side))


def test_normalize_thresholds_ignores_unknown_and_bad_values():
    merged = normalize_thresholds({"minAbsIc": 0.05, "nope": 1, "minAbsT": "bad"})
    assert merged["minAbsIc"] == 0.05
    assert merged["minAbsT"] == DEFAULT_THRESHOLDS["minAbsT"]
    assert "nope" not in merged
    assert set(merged) == set(DEFAULT_THRESHOLDS)


# ============================ kept 池重建（§2.10 承重） ============================
def test_rebuild_kept_pool_reproduces_the_original_basis():
    """续跑重建出的 (值序列, 分段 IC 序列) 与首次入库时**逐位一致**（评审 P1-4）。"""
    ctx = _ctx(flip=False)
    fresh = validate_candidate(parse_expression(_DRIVER), ctx, pool=[], trials=10)
    assert fresh.verdict == "kept"
    rebuilt = rebuild_kept_pool([KeptRow(candidate_id=7, expr_text=_DRIVER)], _ctx(flip=False))
    assert len(rebuilt.entries) == 1 and not rebuilt.errors
    entry = rebuilt.entries[0]
    assert entry.candidate_id == 7
    assert entry.direction == fresh.direction
    np.testing.assert_array_equal(np.nan_to_num(entry.values, nan=-999.0),
                                  np.nan_to_num(fresh.values, nan=-999.0))
    assert entry.ic_series == fresh.ic_series


def test_rebuild_reports_errors_and_light_revalidation_failures():
    """解析失败如实进 ``errors``（绝不静默丢弃）；F2/F3/F6 不达标进 ``failures``。"""
    result = rebuild_kept_pool([KeptRow(candidate_id=1, expr_text="ts_mean(ret,21)")],
                               _ctx(flip=False))
    assert result.entries == [] and len(result.errors) == 1
    flipped = rebuild_kept_pool([KeptRow(candidate_id=2, expr_text=_DRIVER)], _ctx(flip=True))
    assert len(flipped.entries) == 1               # 仍进池（保留记录）
    assert flipped.failures == [(2, "F6")]         # 但被标记为复评不达标
