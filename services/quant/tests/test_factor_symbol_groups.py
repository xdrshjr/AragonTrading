"""因子实验室「标的组」多标的聚合 / 池化 / 护栏单测（factor-lab-symbol-groups §八）。

覆盖放行条件：① 池化等价性（build_pooled_sample([single]) 与 prepare_curve_sample 字节级相等，
train_weights_on_sample 与 train_weights 权重/指标一致）；② 评估聚合（occurrences 加权均值 + perSymbol
逐标的贡献 + 单标的回归不变）；③ 挖掘归并键去 direction（C-3）；④ 扇出护栏（评估 max_eval_symbols 400 /
挖掘 413 计入标的数，均取数前判定，C-1）。测试共享进程，逐例清取数缓存防串扰（aragon-quant-test-isolation）。
"""
from __future__ import annotations

import numpy as np
import pytest
from fastapi import HTTPException

from app.engine.factors.evaluation import aggregate_eval_results
from app.engine.factors.mining import aggregate_mine_results
from app.engine.factors.training import (
    build_pooled_sample,
    prepare_curve_sample,
    train_weights,
    train_weights_on_sample,
)
from app.factors import service
from app.factors.service import FactorWindow, _guard_mine_workload, clear_candle_cache
from app.okx.types import Candle
from tests.conftest import make_synthetic_candles


@pytest.fixture(autouse=True)
def _isolate_cache():
    clear_candle_cache()
    yield
    clear_candle_cache()


_CFG = {"split": 0.7}
_NAMES = ["a", "b", "c"]


def _matrix_label(n: int, seed: int) -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.RandomState(seed)
    matrix = rng.randn(n, 3)
    label = rng.randn(n)
    matrix[:5, :] = np.nan   # 暖机 NaN（由 _aligned 统一剔除）
    label[-2:] = np.nan      # 前瞻右端截断
    return matrix, label


# ============================ 池化等价性（放行条件 1） ============================
def test_pooled_single_equals_prepare_curve_sample_bytewise():
    matrix, label = _matrix_label(120, seed=0)
    single = prepare_curve_sample(matrix, label, _CFG)
    pooled = build_pooled_sample([(matrix, label)], _CFG)
    assert pooled.cut == single.cut
    assert pooled.matrix.tobytes() == single.matrix.tobytes()
    assert pooled.label.tobytes() == single.label.tobytes()


def test_train_on_pooled_single_equals_train_weights():
    matrix, label = _matrix_label(120, seed=1)
    pooled = build_pooled_sample([(matrix, label)], _CFG)
    direct = train_weights(matrix, label, _NAMES, method="linear", config=_CFG,
                           norm_stats={"normWindow": 200, "clip": 4})
    on_sample = train_weights_on_sample(pooled, _NAMES, method="linear", config=_CFG,
                                        norm_stats={"normWindow": 200, "clip": 4})
    assert on_sample.weights == direct.weights
    assert on_sample.metrics == direct.metrics


def test_pooled_two_symbols_cut_is_sum_of_train_cuts():
    from app.engine.factors.training import _aligned, _train_cut
    m1, l1 = _matrix_label(120, seed=2)
    m2, l2 = _matrix_label(90, seed=3)
    pooled = build_pooled_sample([(m1, l1), (m2, l2)], _CFG)
    c1 = _train_cut(len(_aligned(m1, l1)[1]), _CFG)
    c2 = _train_cut(len(_aligned(m2, l2)[1]), _CFG)
    assert pooled.cut == c1 + c2
    # 池化样本数 = 各标的对齐样本数之和（训练块 + 验证块并集）。
    assert len(pooled.label) == len(_aligned(m1, l1)[1]) + len(_aligned(m2, l2)[1])


def test_pooled_insufficient_raises():
    from app.engine.factors.training import InsufficientSamplesError
    tiny = _matrix_label(10, seed=4)   # 对齐后 < MIN_TRAIN_SAMPLES(30)
    with pytest.raises(InsufficientSamplesError):
        build_pooled_sample([tiny], _CFG)


# ============================ 评估聚合（放行条件 2） ============================
def _mk_eval(inst: str, occ: int, rankic: float) -> dict:
    return {
        "factorKey": "mom_roc", "direction": "long", "bars": 100,
        "windowStartTs": 1, "windowEndTs": 2, "coverage": 0.9, "occurrences": occ,
        "horizons": [{
            "h": 1, "ic": 0.02, "rankIc": rankic, "icMean": None, "icStd": None, "ir": 0.1,
            "tStat": None, "monotonicity": 0.5, "topBottomSpread": 0.01,
            "quantiles": [{"q": 0, "meanRet": -0.01, "count": 10},
                          {"q": 1, "meanRet": 0.01, "count": 10}],
        }],
        "instId": inst, "bar": "1H", "note": "",
    }


def test_aggregate_eval_occurrences_weighted_mean():
    results = [_mk_eval("BTC-USDT-SWAP", 100, 0.04), _mk_eval("ETH-USDT-SWAP", 300, 0.08)]
    agg = aggregate_eval_results(results, ["BTC-USDT-SWAP", "ETH-USDT-SWAP"], "long")
    assert agg["aggregated"] is True
    assert agg["symbols"] == ["BTC-USDT-SWAP", "ETH-USDT-SWAP"]
    assert agg["instId"] == "BTC-USDT-SWAP"   # 焦点标的
    assert agg["occurrences"] == 400          # Σ
    # occurrences 加权均值：(0.04*100 + 0.08*300)/400 = 0.07。
    assert agg["horizons"][0]["rankIc"] == pytest.approx(0.07)
    # perSymbol 逐标的贡献可见（绝不静默丢弃标的）。
    assert [p["instId"] for p in agg["perSymbol"]] == ["BTC-USDT-SWAP", "ETH-USDT-SWAP"]
    assert [p["occurrences"] for p in agg["perSymbol"]] == [100, 300]
    # 分位桶 count 求和。
    assert agg["horizons"][0]["quantiles"][0]["count"] == 20


def test_aggregate_eval_all_null_metric_is_none_not_zero():
    a, b = _mk_eval("A-USDT-SWAP", 100, 0.0), _mk_eval("B-USDT-SWAP", 100, 0.0)
    a["horizons"][0]["ir"] = None
    b["horizons"][0]["ir"] = None
    agg = aggregate_eval_results([a, b], ["A-USDT-SWAP", "B-USDT-SWAP"], "long")
    assert agg["horizons"][0]["ir"] is None   # 全空 → None，绝不臆造 0


# ============================ 挖掘归并键去 direction（放行条件 2，C-3） ============================
def _ranked(key: str, direction: str, ir: float, cov: float = 1.0) -> dict:
    return {"factorKey": key, "params": {"win": 10}, "direction": direction, "category": "momentum",
            "ic": ir, "rankIc": ir, "ir": ir, "monotonicity": 0.5, "coverage": cov,
            "occurrences": 100, "note": ""}


def test_aggregate_mine_merges_across_direction_and_uses_focus_direction():
    # 同一候选在焦点标的解析为 long、另一标的解析为 short（归并键去 direction，C-3）→ 归并为一条、方向取焦点。
    focus = [_ranked("mom_roc", "long", 0.3), _ranked("trend_ema_dist", "long", 0.1)]
    other = [_ranked("mom_roc", "short", 0.1)]
    merged = aggregate_mine_results([focus, other], ["BTC-USDT-SWAP", "ETH-USDT-SWAP"], top_n=10)
    mom = [m for m in merged if m["factorKey"] == "mom_roc"]
    assert len(mom) == 1                       # 未被 direction 拆成两键
    assert mom[0]["direction"] == "long"       # 统一取焦点标的方向
    # 组分 = 各标的 IR coverage 加权均值：(0.3 + 0.1)/2 = 0.2。
    assert mom[0]["ir"] == pytest.approx(0.2)
    # 按 abs(组 IR) 降序：mom(0.2) 先于 trend(0.1)。
    assert merged[0]["factorKey"] == "mom_roc"


# ============================ 扇出护栏（放行条件 3，C-1/C-3，取数前判定） ============================
def _candles(n: int = 60) -> list[Candle]:
    return [Candle(ts=r["ts"], o=r["o"], h=r["h"], l=r["l"], c=r["c"], vol=r["vol"])
            for r in make_synthetic_candles(n, start_ts=1_700_000_000, step=3600)]


_W = FactorWindow(inst_id="BTC-USDT-SWAP", bar="1H", start_ts=1_700_000_000,
                  end_ts=1_700_000_000 + 59 * 3600)


async def test_evaluate_too_many_symbols_400_before_fetch(monkeypatch):
    calls = {"n": 0}

    async def _load(rest, *, inst_id, bar, start_ts, end_ts, max_bars=None):
        calls["n"] += 1
        return _candles()

    monkeypatch.setattr(service, "load_range_candles", _load)
    too_many = [f"S{i}-USDT-SWAP" for i in range(31)]   # > 默认 max_eval_symbols=30
    with pytest.raises(HTTPException) as ei:
        await service.evaluate(None, None, _W, factor_key="mom_roc", params=None, direction="auto",
                               horizons=(1, 3, 6, 12), quantiles=5, symbols=too_many)
    assert ei.value.status_code == 400
    assert ei.value.detail["code"] == "FACTOR_EVAL_TOO_MANY_SYMBOLS"
    assert calls["n"] == 0   # 取数前判定，绝不无界扇出 OKX 请求


async def test_evaluate_group_aggregates_and_single_regression(monkeypatch):
    async def _load(rest, *, inst_id, bar, start_ts, end_ts, max_bars=None):
        return _candles()

    monkeypatch.setattr(service, "load_range_candles", _load)
    group = await service.evaluate(None, None, _W, factor_key="mom_roc", params=None, direction="auto",
                                   horizons=(1, 3, 6, 12), quantiles=5,
                                   symbols=["BTC-USDT-SWAP", "ETH-USDT-SWAP"])
    assert group["aggregated"] is True
    assert group["symbols"] == ["BTC-USDT-SWAP", "ETH-USDT-SWAP"]
    assert len(group["perSymbol"]) == 2
    assert group["instId"] == "BTC-USDT-SWAP"        # 焦点标的
    assert group["direction"] in ("long", "short")    # auto 已冻结为具体方向
    # 单标的路径不聚合（回归护栏）。
    single = await service.evaluate(None, None, _W, factor_key="mom_roc", params=None, direction="auto",
                                    horizons=(1, 3, 6, 12), quantiles=5, symbols=["BTC-USDT-SWAP"])
    assert "aggregated" not in single


def test_mine_workload_guard_counts_symbols():
    # 单标的通过、标的组因工作量 × 标的数超限 413（取数前，C-3）。
    candidates = [{} for _ in range(20)]
    big = FactorWindow(inst_id="BTC-USDT-SWAP", bar="1H", start_ts=0, end_ts=20000 * 3600)
    _guard_mine_workload(candidates, big, symbol_count=1)   # 20 × 20000 = 400k ≤ 3M → 不抛
    with pytest.raises(HTTPException) as ei:
        _guard_mine_workload(candidates, big, symbol_count=8)   # ×8 = 3.2M > 3M → 413
    assert ei.value.status_code == 413
    assert ei.value.detail["code"] == "FACTOR_MINE_TOO_LARGE"
