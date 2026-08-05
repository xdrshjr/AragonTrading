"""机制族分类测试（strategy-factor-discovery §7.1）：13 族代表性样例 + 未知结构回落。"""
from __future__ import annotations

import pytest

from app.discovery.expression import build_context, evaluate, parse_expression, validate_node
from app.discovery.mechanisms import (
    MECHANISM_FAMILIES,
    MECHANISM_KEYS,
    UNCLASSIFIED,
    classify_mechanism,
    empty_coverage,
    get_family,
    mechanism_catalog,
    mechanism_cn,
    uncovered_families,
)
from app.engine.backtester import candles_to_df
from tests.conftest import make_synthetic_candles


def _gapped_df(n: int = 600):
    """合成 K 线 + 确定性的微小开盘跳空。

    ``make_synthetic_candles`` 令 ``o`` 逐字等于上一根 ``c``，于是 ``overnight`` 恒为 0、
    其标准分恒 NaN——这是**夹具的退化**而非表达式缺陷（真实永续的跨根跳空虽小但非零）。
    故此处注入确定性微扰，让 ``gap_premium`` 族的原型在可算数据上被检验。
    """
    rows = make_synthetic_candles(n, step=3600)
    for i, row in enumerate(rows):
        row["o"] = round(row["o"] * (1.0 + 0.0004 * ((i % 7) - 3)), 4)
        row["h"] = max(row["h"], row["o"])
        row["l"] = min(row["l"], row["o"])
    return candles_to_df(rows)


def test_thirteen_families_declared():
    assert len(MECHANISM_FAMILIES) == 13
    assert len(set(MECHANISM_KEYS)) == 13


@pytest.mark.parametrize("family", MECHANISM_FAMILIES, ids=[f.key for f in MECHANISM_FAMILIES])
def test_every_prototype_parses_validates_and_self_classifies(family):
    """每族原型必须：① 解析通过；② 过复杂度 / 量纲护栏；③ **被分类回本族**。

    第三条是承重的——机制覆盖度统计、LLM 引导探索的「补哪个方向」全部读
    ``classify_mechanism``。原型自己都归不到本族，覆盖度就是噪声。
    """
    assert family.prototypes, f"{family.key} 必须至少有一个原型"
    for text in family.prototypes:
        node = parse_expression(text)
        validate_node(node)
        assert classify_mechanism(node) == family.key, f"{text} 应归 {family.key}"


@pytest.mark.parametrize("family", MECHANISM_FAMILIES, ids=[f.key for f in MECHANISM_FAMILIES])
def test_every_prototype_is_computable_on_real_shaped_frame(family):
    """原型在真实形状的 K 线上必须能算出**非空**因子值（不是一堆 NaN 的摆设）。"""
    df = _gapped_df()
    ctx = build_context(df)
    for text in family.prototypes:
        values = evaluate(parse_expression(text), ctx)
        assert len(values) == len(df)
        finite = float((values == values).mean())
        assert finite > 0.3, f"{text} 有限值比例仅 {finite:.2f}"


def test_unknown_structure_falls_back_to_unclassified():
    """无任何字段特征的纯常数表达式回落 ``unclassified``（绝不硬塞进某一族）。"""
    assert classify_mechanism(parse_expression("add(1,mul(2,3))")) == UNCLASSIFIED


def test_band_deviation_beats_trend_difference():
    """「原始价 − 其均值」判均值回归；「长短均线之差」判趋势——同算子相反语义的分水岭。"""
    assert classify_mechanism(
        parse_expression("sub(close,ts_mean(close,20))")) == "mean_reversion_band"
    assert classify_mechanism(
        parse_expression("sub(ema(close,20),ema(close,60))")) == "trend_persistence"


def test_orderflow_is_marked_as_proxy():
    """``orderflow_proxy`` 必须显式标注为代理（OKX 公共 K 线不含主动买卖量，不可当真实单流读）。"""
    family = get_family("orderflow_proxy")
    assert family is not None and family.proxy_note
    assert "代理" in family.proxy_note


def test_coverage_helpers():
    coverage = empty_coverage()
    assert set(coverage) == set(MECHANISM_KEYS)     # 13 族一个不少，绝不惰性建键
    assert all(v == 0 for v in coverage.values())
    assert uncovered_families(coverage) == list(MECHANISM_KEYS)
    coverage["gap_premium"] = 3
    assert "gap_premium" not in uncovered_families(coverage)


def test_catalog_and_cn():
    catalog = mechanism_catalog()
    assert {item["key"] for item in catalog} == set(MECHANISM_KEYS)
    for item in catalog:
        assert item["cn"] and item["hint"] and item["prototypes"]
    assert mechanism_cn("gap_premium") == "跨根跳空"
    assert mechanism_cn("no_such_family") == "no_such_family"
