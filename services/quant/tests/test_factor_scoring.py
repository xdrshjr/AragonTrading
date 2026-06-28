"""因子打分测试：滚动 z-score 因果性、空成员→NaN、tanh 压缩边界、拒绝 auto 方向。"""
from __future__ import annotations

import numpy as np
import pandas as pd

from app.engine.backtester import candles_to_df
from app.engine.factors import causal_zscore, compose_score, resolve_norm_contract
from app.engine.factors.scoring import DEFAULT_CLIP
from tests.conftest import make_synthetic_candles


def _df(n: int = 320):
    return candles_to_df(make_synthetic_candles(n, step=3600))


_MEMBERS = [
    {"factorKey": "mom_roc", "params": {"win": 20}, "direction": "long", "weight": 0.6},
    {"factorKey": "rev_rsi", "params": {}, "direction": "short", "weight": 0.4},
]


def test_causal_zscore_is_causal():
    """滚动 z-score 只用 ≤ i 数据：篡改未来不改变 ≤k 的输出。"""
    df = _df()
    series = df["c"]
    base = causal_zscore(series, 60).to_numpy()
    tampered = series.copy()
    tampered.loc[200:] = tampered.loc[200:] * 2.0
    after = causal_zscore(tampered, 60).to_numpy()
    for i in range(200):
        a, b = base[i], after[i]
        assert (np.isnan(a) and np.isnan(b)) or abs(a - b) < 1e-12


def test_compose_score_squash_bounds():
    """squash=True 时复合得分被 tanh 压到 [-1,1]。"""
    df = _df()
    score = compose_score(df, _MEMBERS, None, norm_window=80, squash=True, bar="1H")
    finite = score.dropna()
    assert len(finite) > 0
    assert finite.min() >= -1.0 and finite.max() <= 1.0


def test_compose_score_empty_members_is_nan():
    df = _df()
    score = compose_score(df, [], None, norm_window=80, bar="1H")
    assert score.isna().all()


def test_compose_score_rejects_auto_direction():
    """compose_score 不接受 auto 方向（杜绝打分期前视泄漏，评审 C-P1-1）。"""
    df = _df()
    bad = [{"factorKey": "mom_roc", "params": {}, "direction": "auto", "weight": 1.0}]
    try:
        compose_score(df, bad, None, norm_window=80, bar="1H")
        assert False, "auto 方向应抛 ValueError"
    except ValueError:
        pass


def test_resolve_norm_contract_prefers_norm_stats():
    """归一化契约（H1）：normStats 有 normWindow 时优先取它（含 clip），无视 thresholds 漂移。"""
    nw, clip = resolve_norm_contract(norm_stats={"normWindow": 120, "clip": 3.0},
                                     thresholds={"normWindow": 500})
    assert nw == 120 and clip == 3.0


def test_resolve_norm_contract_missing_clip_defaults():
    """normStats 缺 clip → 回默认 clip（向后兼容旧训练产物）。"""
    nw, clip = resolve_norm_contract(norm_stats={"normWindow": 80}, thresholds={"normWindow": 500})
    assert nw == 80 and clip == DEFAULT_CLIP


def test_resolve_norm_contract_falls_back_to_thresholds():
    """无训练 normStats（equal/未训练/旧组合）→ 回落 thresholds.normWindow + 默认 clip。"""
    nw, clip = resolve_norm_contract(norm_stats=None, thresholds={"normWindow": 333})
    assert nw == 333 and clip == DEFAULT_CLIP
    nw2, clip2 = resolve_norm_contract(norm_stats={}, thresholds={})
    assert clip2 == DEFAULT_CLIP and nw2 == 200   # DEFAULT_NORM_WINDOW


def test_clip_changes_zscore_so_contract_matters():
    """非默认 clip 改变 z-score → 训练/打分 clip 不一致会错配；故 clip 须经契约单源（H1）。"""
    df = _df()
    tight = causal_zscore(df["c"], 60, clip=0.5).dropna()
    loose = causal_zscore(df["c"], 60, clip=4.0).dropna()
    assert tight.abs().max() <= 0.5 + 1e-9
    assert loose.abs().max() > 0.5   # 夹断阈值不同 → 量纲不同，印证 clip 必须端到端一致


def test_compose_score_weights_override_member_weight():
    """显式 weights 字典覆盖成员自带 weight（训练产物注入打分）。"""
    df = _df()
    from app.engine.factors import member_names
    names = member_names(_MEMBERS)
    weights = {names[0]: 1.0, names[1]: 0.0}
    score = compose_score(df, _MEMBERS, weights, norm_window=80, squash=False, bar="1H")
    # 仅第一个成员生效：等价于单成员 z-score（同一根上成比例）
    single = compose_score(df, [{**_MEMBERS[0], "weight": 1.0}], None, norm_window=80,
                           squash=False, bar="1H")
    merged = pd.concat([score, single], axis=1).dropna()
    assert np.allclose(merged.iloc[:, 0].to_numpy(), merged.iloc[:, 1].to_numpy())
