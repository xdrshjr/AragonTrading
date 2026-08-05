"""表达式 AST 测试（strategy-factor-discovery §7.1）。

覆盖：解析 / 序列化往返、交换律同哈希、窗口梯子越界报错、量纲推断真值表、深度 / 节点护栏、
**因果性**（在第 i 根之后追加数据不改变前 i 根的值）、记忆化命中、数值异常 → NaN 不抛。
"""
from __future__ import annotations

import numpy as np
import pytest

from app.discovery.expression import (
    MAX_DEPTH,
    MAX_NODES,
    MIN_NODES,
    WINDOW_LADDER,
    ExpressionError,
    build_context,
    const_node,
    depth,
    evaluate,
    evaluate_expression_text,
    expr_hash,
    field_node,
    fields_of,
    infer_dimension,
    is_valid,
    node_count,
    op_node,
    ops_of,
    parse_expression,
    replace_at,
    set_window_at,
    skeleton,
    subtrees,
    to_text,
    validate_node,
    windows_of,
)
from app.engine.backtester import candles_to_df
from tests.conftest import make_synthetic_candles


def _df(n: int = 400):
    return candles_to_df(make_synthetic_candles(n, step=3600))


# ============================ 解析与规范式 ============================
def test_parse_serialize_roundtrip():
    """规范式 → 树 → 规范式 逐字节往返（哈希稳定的前提）。"""
    for text in ("sub(ts_mean(overnight,20),delta(amplitude,5))",
                 "div(abs(ret),ts_rank(turnover,20))",
                 "ts_corr(ret,ts_zscore(volume,20),30)",
                 "neg(ts_zscore(ret,8))"):
        node = parse_expression(text)
        assert to_text(node) == text
        assert to_text(parse_expression(to_text(node))) == text


def test_commutative_operands_share_one_hash():
    """``add`` / ``mul`` 的两个子树交换后必然同规范式、同哈希（否则去重形同虚设）。"""
    left = parse_expression("add(ts_mean(ret,20),ts_rank(volume,30))")
    right = parse_expression("add(ts_rank(volume,30),ts_mean(ret,20))")
    assert to_text(left) == to_text(right)
    assert expr_hash(left) == expr_hash(right)
    # 非交换算子绝不排序：sub(a,b) 与 sub(b,a) 必须是两个不同候选。
    assert expr_hash(parse_expression("sub(ret,overnight)")) != expr_hash(
        parse_expression("sub(overnight,ret)"))


def test_parse_rejects_illegal_tokens_with_position():
    for text in ("", "sub(ret", "nope(ret,20)", "sub(ret,)", "ts_mean(ret,20) extra",
                 "ts_mean(unknown_field,20)", "sub(ret,ret,ret)"):
        with pytest.raises(ExpressionError):
            parse_expression(text)


def test_window_must_be_on_ladder():
    """窗口只能取梯子上的值（离散化是哈希不爆炸与「扰动走一格」语义的前提）。"""
    assert parse_expression("ts_mean(ret,20)").window == 20
    with pytest.raises(ExpressionError):
        parse_expression("ts_mean(ret,21)")
    with pytest.raises(ExpressionError):
        parse_expression("ts_mean(ret,20.5)")
    assert 21 not in WINDOW_LADDER


# ============================ 量纲真值表 ============================
@pytest.mark.parametrize("text,expected", [
    ("ts_mean(close,20)", "price"),
    ("ts_std(close,20)", "price"),
    ("ts_zscore(close,20)", "ratio"),
    ("ts_rank(volume,20)", "ratio"),
    ("delta(close,20)", "price"),
    ("roc(close,20)", "ratio"),
    ("sub(close,ts_mean(close,20))", "price"),
    ("div(close,ts_mean(close,20))", "ratio"),
    ("mul(ret,ts_mean(close,20))", "price"),
    ("sign(ts_mean(ret,20))", "ratio"),
])
def test_dimension_truth_table(text, expected):
    assert infer_dimension(parse_expression(text)) == expected


@pytest.mark.parametrize("text", [
    "add(close,volume)",           # 加减两侧量纲必须相同
    "sub(close,ret)",
    "mul(close,volume)",           # 乘法至少一侧须为 ratio
    "div(close,volume)",           # 除法异量纲且分母非 ratio
    "log1p(close)",                # log1p 只接受 ratio
])
def test_dimension_mismatch_rejected(text):
    with pytest.raises(ExpressionError):
        infer_dimension(parse_expression(text))


# ============================ 复杂度护栏 ============================
def test_complexity_guards():
    too_small = op_node("ts_mean", (field_node("ret"),), 20)   # 2 节点 < MIN_NODES
    assert node_count(too_small) < MIN_NODES
    with pytest.raises(ExpressionError):
        validate_node(too_small)
    deep = field_node("ret")
    for _ in range(MAX_DEPTH + 1):
        deep = op_node("ts_mean", (deep,), 20)
    assert depth(deep) > MAX_DEPTH
    with pytest.raises(ExpressionError):
        validate_node(deep)
    assert not is_valid(deep)
    ok = parse_expression("sub(ts_mean(overnight,20),delta(amplitude,5))")
    validate_node(ok)
    assert is_valid(ok) and node_count(ok) <= MAX_NODES


def test_distinct_window_cap():
    """同一表达式内不同窗口的数量 ≤ 4（超出即拒，防搜索空间爆炸）。"""
    five = parse_expression(
        "add(add(ts_mean(ret,3),ts_mean(ret,5)),"
        "add(ts_mean(ret,8),add(ts_mean(ret,13),ts_mean(ret,20))))")
    assert len(set(windows_of(five))) == 5
    with pytest.raises(ExpressionError):
        validate_node(five)


# ============================ 骨架 / 子树 / 替换 ============================
def test_skeleton_erases_leaves_and_windows():
    a = parse_expression("sub(ts_mean(overnight,20),delta(amplitude,5))")
    b = parse_expression("sub(ts_mean(ret,60),delta(body,13))")
    assert skeleton(a) == skeleton(b) == "sub(ts_mean(?,_),delta(?,_))"
    assert skeleton(a) != skeleton(parse_expression("div(ts_mean(ret,20),delta(body,5))"))


def test_subtrees_and_replace_are_pure():
    node = parse_expression("sub(ts_mean(overnight,20),delta(amplitude,5))")
    paths = [p for p, _n in subtrees(node)]
    assert () in paths and (0,) in paths and (0, 0) in paths
    replaced = replace_at(node, (1,), parse_expression("ts_rank(volume,30)"))
    assert to_text(replaced) == "sub(ts_mean(overnight,20),ts_rank(volume,30))"
    assert to_text(node) == "sub(ts_mean(overnight,20),delta(amplitude,5))"   # 原树不变
    moved = set_window_at(node, (0,), 60)
    assert to_text(moved) == "sub(ts_mean(overnight,60),delta(amplitude,5))"
    with pytest.raises(ExpressionError):
        set_window_at(node, (0, 0), 60)   # 叶子无窗口


def test_fields_and_ops_collection():
    node = parse_expression("ts_mean(mul(body,ts_rank(volume,20)),20)")
    assert sorted(fields_of(node)) == ["body", "volume"]
    assert sorted(ops_of(node)) == ["mul", "ts_mean", "ts_rank"]


# ============================ 求值：因果性 / 记忆化 / 数值安全 ============================
def test_evaluation_is_strictly_causal():
    """篡改第 k 根之后的数据，断言 ≤k 的输出逐元素不变（R-7 前视泄漏的机器判据）。"""
    df = _df()
    k = 200
    exprs = ["sub(ts_mean(overnight,20),delta(amplitude,5))",
             "div(sub(close,ts_mean(close,20)),ts_std(close,20))",
             "ts_corr(ret,ts_zscore(volume,20),30)",
             "ts_rank(ts_std(ret,20),120)",
             "ema(mul(body,ts_rank(volume,20)),30)"]
    tampered = df.copy()
    for col, mult in (("c", 1.9), ("h", 1.9), ("l", 0.5), ("vol", 4.0), ("o", 1.5)):
        tampered.loc[k + 1:, col] = tampered.loc[k + 1:, col] * mult
    base_ctx, tam_ctx = build_context(df), build_context(tampered)
    for text in exprs:
        node = parse_expression(text)
        base = evaluate(node, base_ctx)
        after = evaluate(node, tam_ctx)
        for i in range(k + 1):
            a, b = base[i], after[i]
            assert (np.isnan(a) and np.isnan(b)) or a == b, f"{text} 在第 {i} 根非因果"


def test_memoization_computes_shared_subtree_once():
    """记忆化键 = ``to_text``：共享子树只算一次（交叉产物大量共享子树，§2.3.3）。"""
    df = _df(200)
    ctx = build_context(df)
    memo: dict = {}
    node = parse_expression("sub(ts_mean(ret,20),ts_mean(ret,20))")
    evaluate(node, ctx, memo)
    assert "ts_mean(ret,20)" in memo
    before = len(memo)
    evaluate(parse_expression("ts_mean(ret,20)"), ctx, memo)
    assert len(memo) == before        # 命中缓存，无新键


def test_numeric_anomalies_degrade_to_nan_without_raising():
    """除零 / 非有限一律就地置 NaN 并向上传播，绝不抛（长跑期单候选异常不得拖垮整轮）。"""
    df = _df(120)
    ctx = build_context(df)
    values = evaluate(parse_expression("div(ret,sub(close,close))"), ctx)
    assert len(values) == len(df)
    assert np.isnan(values).all()
    assert not np.isinf(values).any()
    log_values = evaluate(parse_expression("log1p(mul(ret,-1000))"), ctx)
    assert not np.isinf(log_values).any()


def test_const_node_rejects_non_finite():
    with pytest.raises(ExpressionError):
        const_node(float("inf"))


# ============================ expr 桥的降级契约（§2.2 承重） ============================
def test_expression_text_degrades_to_full_length_nan_series():
    """解析 / 校验失败一律返回**与 df 等长、index 0..n-1 的全 NaN Series**（评审 P1-1）。"""
    df = _df(150)
    for text in ("", "not-an-expression", "ts_mean(ret,21)", "add(close,volume)", None):
        series = evaluate_expression_text(df, text or "")
        assert len(series) == len(df)
        assert list(series.index) == list(range(len(df)))
        assert series.isna().all()


def test_expression_text_computes_valid_expression():
    df = _df(300)
    series = evaluate_expression_text(df, "sub(ts_mean(overnight,20),delta(amplitude,5))")
    assert len(series) == len(df)
    assert series.notna().sum() > 0
    assert series.iloc[0] != series.iloc[0]     # 暖机段为 NaN，绝不补零
