"""``expr`` 桥测试（strategy-factor-discovery §7.1 / D-1）。

两条主线：
① **零改地走完既有全管线**：``compute_factor`` / ``evaluate_factor_key`` /
   ``build_factor_matrix`` / ``factor_score`` 回测都能吃下挖掘表达式；
② **hidden 过滤四处现场一处不漏**：目录 / Agent 工具枚举 / seed / 经典挖掘候选集均不含
   ``expr``（漏一处即出现「一条永远算不出值的空表达式因子」，R-10）。

另含评审 P0-3 的承重断言：``validate_members`` 对 ``expr`` 成员**透传 name**，且
**打乱成员顺序后列名与成员的对应关系不变**——这是防止训练权重被静默错配的唯一机器判据。
"""
from __future__ import annotations

import numpy as np
import pytest
from fastapi import HTTPException

from app.agent.tools.factor_tools import FACTOR_KEY_ENUM
from app.discovery.expression import expr_hash, parse_expression
from app.engine.backtester import candles_to_df, run_backtest
from app.engine.factors import (
    FACTOR_LIBRARY,
    available_factors,
    build_factor_matrix,
    compose_score,
    compute_factor,
    default_candidates,
    evaluate_factor_key,
    member_names,
    visible_factor_keys,
)
from app.factors.specs import validate_members
from app.strategies.factor_score_strategy import FactorScoreStrategy
from tests.conftest import make_synthetic_candles

_EXPRS = ("sub(ts_mean(overnight,20),delta(amplitude,5))",
          "div(sub(close,ts_mean(close,20)),ts_std(close,20))",
          "ts_corr(ret,ts_zscore(volume,20),30)")


def _candles(n: int = 600):
    return make_synthetic_candles(n, step=3600)


def _df(n: int = 600):
    return candles_to_df(_candles(n))


def _member(text: str, weight: float = 1.0, direction: str = "long") -> dict:
    return {"factorKey": "expr", "params": {"expr": text},
            "name": f"expr:{expr_hash(parse_expression(text))[:8]}",
            "weight": weight, "direction": direction}


# ============================ ① hidden 过滤四处现场 ============================
def test_expr_is_in_library_but_hidden_everywhere():
    assert "expr" in FACTOR_LIBRARY and FACTOR_LIBRARY["expr"].hidden
    assert "expr" not in visible_factor_keys()
    assert "expr" not in {f["key"] for f in available_factors()}
    assert "expr" not in FACTOR_KEY_ENUM
    assert "expr" not in {c["factorKey"] for c in default_candidates("1H")}


def test_seed_skips_hidden_entries():
    """``_seed_builtin_factors`` 跳过 hidden：源码层断言（避免为一条判据起整套 DB）。"""
    import inspect

    from app.db import session as db_session

    source = inspect.getsource(db_session._seed_builtin_factors)
    assert "spec.hidden" in source and "continue" in source


# ============================ ② 走完既有全管线 ============================
def test_compute_factor_accepts_expr_params():
    df = _df()
    series = compute_factor(df, "expr", {"expr": _EXPRS[1]}, bar="1H")
    assert len(series) == len(df)
    assert list(series.index) == list(range(len(df)))
    assert series.notna().sum() > 100


def test_evaluate_factor_key_runs_on_expr():
    df = _df()
    result = evaluate_factor_key(df, "expr", {"expr": _EXPRS[1]}, "auto",
                                 bar="1H", horizons=(1, 6))
    assert result.factorKey == "expr"
    assert result.direction in ("long", "short")
    assert result.coverage > 0.0 and result.occurrences > 0


def test_build_factor_matrix_and_compose_score_on_expr_members():
    df = _df()
    members = [_member(text) for text in _EXPRS]
    matrix, names, norm = build_factor_matrix(df, members, bar="1H")
    assert matrix.shape == (len(df), len(members))
    assert len(set(names)) == len(members)
    score = compose_score(df, members, {name: 1.0 for name in names},
                          norm_window=norm["normWindow"], clip=norm["clip"], bar="1H")
    assert len(score) == len(df)
    assert np.isfinite(score.to_numpy()).sum() > 0


def test_factor_score_backtest_runs_with_expr_members():
    """挖掘因子组合能被 ``factor_score`` 策略消费并跑完真回测（``expr`` 桥打通的判据）。"""
    members = validate_members([_member(text) for text in _EXPRS])
    strategy = FactorScoreStrategy({"members": members,
                                    "thresholds": {"enterLong": 0.2, "enterShort": 0.2,
                                                   "exitBand": 0.05, "normWindow": 60}})
    result = run_backtest(strategy, _candles(800), bar="1H", initial_capital=10000.0,
                          taker_fee_bps=5.0, slippage_bps=2.0)
    assert result.metrics is not None
    assert result.equity_points


# ============================ ③ validate_members 的 name 透传（评审 P0-3 承重） ============================
def test_expr_member_requires_name():
    with pytest.raises(HTTPException) as exc:
        validate_members([{"factorKey": "expr", "params": {"expr": _EXPRS[0]},
                           "weight": 1.0, "direction": "long"}])
    assert exc.value.detail["code"] == "FACTOR_MEMBER_NAME_REQUIRED"


def test_expr_member_rejects_malformed_name():
    for name in ("expr:XYZ", "expr:1234567", "mined-1", "expr:0123456789"):
        member = {**_member(_EXPRS[0]), "name": name}
        with pytest.raises(HTTPException) as exc:
            validate_members([member])
        assert exc.value.detail["code"] == "FACTOR_MEMBER_NAME_REQUIRED"


def test_expr_member_rejects_unparsable_expression():
    member = {**_member(_EXPRS[0]), "params": {"expr": "ts_mean(ret,21)"}}
    with pytest.raises(HTTPException) as exc:
        validate_members([member])
    assert exc.value.detail["code"] == "FACTOR_EXPR_INVALID"


def test_member_names_bind_to_expression_not_position():
    """打乱成员顺序后，**列名与表达式的对应关系不变**。

    这是 P0-3 的机器判据：不透传 ``name`` 时三个 ``expr`` 成员会退化成
    ``expr`` / ``expr#2`` / ``expr#3`` 的纯位置序，重排即把训练权重静默错配到别的因子上，
    而 ``assert_weights_cover`` 的键集相等检查照样通过（查不出来）。
    """
    members = [_member(text) for text in _EXPRS]
    forward = validate_members(members)
    reversed_ = validate_members(list(reversed(members)))
    forward_map = dict(zip(member_names(forward), [m["params"]["expr"] for m in forward]))
    reversed_map = dict(zip(member_names(reversed_),
                            [m["params"]["expr"] for m in reversed_]))
    assert len(forward_map) == len(_EXPRS)
    assert forward_map == reversed_map


def test_non_expr_members_are_byte_identical_to_before():
    """非 ``expr`` 成员的返回 dict 逐键与改动前一致（零回归：不多一个 ``name`` 键）。"""
    out = validate_members([
        {"factorKey": "mom_roc", "params": {"win": 20}, "weight": 0.6, "direction": "long"},
        {"factorKey": "rev_rsi", "params": {}, "weight": 0.4, "direction": "short"}])
    for row in out:
        assert set(row) == {"factorKey", "params", "weight", "direction"}
    assert member_names(out) == ["mom_roc", "rev_rsi"]


def test_mixed_members_only_expr_carries_name():
    out = validate_members([
        {"factorKey": "mom_roc", "params": {"win": 20}, "weight": 0.5, "direction": "long"},
        _member(_EXPRS[0], weight=0.5)])
    assert set(out[0]) == {"factorKey", "params", "weight", "direction"}
    assert out[1]["name"].startswith("expr:")
    assert member_names(out) == ["mom_roc", out[1]["name"]]
