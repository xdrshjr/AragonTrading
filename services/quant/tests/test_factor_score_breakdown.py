"""score_breakdown 测试（auto-trade-explainability §6.1）：与 compose_score 逐位等价、
逐成员贡献符号 / 量纲正确、权重 0 不计入、因果（无未来泄漏）。

钉死「解释一套、决策另一套」双口径风险（评审 R2）：breakdown.score 必须与 compose_score 完全一致。
"""
from __future__ import annotations

import numpy as np

from app.engine.backtester import candles_to_df
from app.engine.factors import compose_score, member_names, score_breakdown
from tests.conftest import make_synthetic_candles


def _df(n: int = 320):
    return candles_to_df(make_synthetic_candles(n, step=3600))


_MEMBERS = [
    {"factorKey": "mom_roc", "params": {"win": 20}, "direction": "long", "weight": 0.6},
    {"factorKey": "rev_rsi", "params": {}, "direction": "short", "weight": 0.4},
]


def test_breakdown_score_equals_compose_score_bitwise():
    """breakdown.score 与 compose_score 逐位相等（同 df/members/weights/window/clip/squash）。"""
    df = _df()
    bd = score_breakdown(df, _MEMBERS, None, norm_window=80, clip=4.0, squash=True, bar="1H")
    composed = compose_score(df, _MEMBERS, None, norm_window=80, clip=4.0, squash=True, bar="1H")
    a, b = bd.score.to_numpy(), composed.to_numpy()
    assert a.shape == b.shape
    for x, y in zip(a, b):
        assert (np.isnan(x) and np.isnan(y)) or x == y


def test_sum_of_member_contributions_equals_composite():
    """Σ member.contribution（pre-squash）== composite；squash 后 == score。"""
    df = _df()
    bd = score_breakdown(df, _MEMBERS, None, norm_window=80, clip=4.0, squash=True, bar="1H")
    summed = sum((m.contribution for m in bd.members), start=bd.composite * 0.0)
    merged = np.column_stack([summed.to_numpy(), bd.composite.to_numpy()])
    finite = merged[~np.isnan(merged).any(axis=1)]
    assert len(finite) > 0
    assert np.allclose(finite[:, 0], finite[:, 1])
    squashed = np.tanh(bd.composite.to_numpy(dtype=float))
    score = bd.score.to_numpy()
    for x, y in zip(squashed, score):
        assert (np.isnan(x) and np.isnan(y)) or abs(x - y) < 1e-12


def test_direction_flip_changes_contribution_sign():
    """单成员方向翻转（long↔short）使其 value / contribution 变号。"""
    df = _df()
    long_member = [{"factorKey": "mom_roc", "params": {"win": 20}, "direction": "long",
                    "weight": 1.0}]
    short_member = [{**long_member[0], "direction": "short"}]
    bd_long = score_breakdown(df, long_member, None, norm_window=80, squash=False, bar="1H")
    bd_short = score_breakdown(df, short_member, None, norm_window=80, squash=False, bar="1H")
    vl = bd_long.members[0].value.to_numpy()
    vs = bd_short.members[0].value.to_numpy()
    finite = ~np.isnan(vl) & ~np.isnan(vs)
    assert finite.any()
    assert np.allclose(vl[finite], -vs[finite])


def test_zero_weight_member_is_excluded():
    """权重 0 的成员不计入 members 明细，也不进求和。"""
    df = _df()
    names = member_names(_MEMBERS)
    weights = {names[0]: 1.0, names[1]: 0.0}
    bd = score_breakdown(df, _MEMBERS, weights, norm_window=80, squash=False, bar="1H")
    assert len(bd.members) == 1
    assert bd.members[0].name == names[0]


def test_breakdown_is_causal_no_future_leak():
    """在 i 处的 zscore 不因 df[i+1:] 改变（截断重算一致）。"""
    df = _df()
    member = [{"factorKey": "mom_roc", "params": {"win": 20}, "direction": "long", "weight": 1.0}]
    base = score_breakdown(df, member, None, norm_window=60, squash=False, bar="1H")
    tampered = df.copy()
    tampered.loc[200:, "c"] = tampered.loc[200:, "c"] * 2.0
    after = score_breakdown(tampered, member, None, norm_window=60, squash=False, bar="1H")
    z_base = base.members[0].zscore.to_numpy()
    z_after = after.members[0].zscore.to_numpy()
    for i in range(200):
        a, b = z_base[i], z_after[i]
        assert (np.isnan(a) and np.isnan(b)) or abs(a - b) < 1e-12


def test_empty_members_yields_nan_score_and_no_members():
    """空成员：score 全 NaN、members 为空（与 compose_score 行为一致）。"""
    df = _df()
    bd = score_breakdown(df, [], None, norm_window=80, bar="1H")
    assert bd.members == []
    assert bd.score.isna().all()
