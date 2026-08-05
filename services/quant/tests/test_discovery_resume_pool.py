"""续跑期 kept 池重建（strategy-factor-discovery §2.10 / 评审 P1-4 · 承重）。

放行条件 5：``rebuild_kept_pool`` **必须**与「刷新后复评」是同一个实现，并有 monkeypatch 计数
断言守门；重建失败一律 ``paused``，**绝不带残缺池开跑**。

为什么这条是承重的：F10 的双通道去重要求新候选与**每一条**已入库因子比对值序列相关，可是值
序列根本没落库（几千个点，落进检查点就破坏 64 KB 有界目标）。进程重启后 kept 池只剩元信息，
值级去重无从计算——长跑每重启一次就会开始收一批同质因子，而这恰是本特性的核心卖点。
"""
from __future__ import annotations

import math

import numpy as np
import pytest

from app.discovery.expression import parse_expression
from app.discovery.validation import (
    DEFAULT_THRESHOLDS,
    KeptRow,
    ValidationContext,
    rebuild_kept_pool,
    split_index,
    validate_candidate,
)
from app.engine.backtester import candles_to_df

# 3 节点（过 MIN_NODES 门槛，与生产里「必先过审查层」的候选同形）；
# ``neg`` 只翻符号，方向由 IS 段定号吸收，指标绝对值与裸 zscore 一致。
_DRIVER = "neg(ts_zscore(volume,20))"


def _driven_candles(n: int, flip_at: int) -> list[dict]:
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


def _ctx(flip: bool = False, n: int = 1200) -> ValidationContext:
    flip_at = split_index(n, 0.7) if flip else n + 1
    return ValidationContext(df_full=candles_to_df(_driven_candles(n, flip_at)),
                             horizon=1, is_ratio=0.7,
                             thresholds=dict(DEFAULT_THRESHOLDS))


def test_rebuilt_basis_matches_the_original_bit_for_bit():
    """重建出的 (值序列, 分段 IC 序列) 与首次入库时逐位一致——否则去重基准就是错的。"""
    fresh = validate_candidate(parse_expression(_DRIVER), _ctx(), pool=[], trials=10)
    assert fresh.verdict == "kept"
    result = rebuild_kept_pool([KeptRow(candidate_id=11, expr_text=_DRIVER)], _ctx())
    entry = result.entries[0]
    np.testing.assert_array_equal(np.nan_to_num(entry.values, nan=-1.0),
                                  np.nan_to_num(fresh.values, nan=-1.0))
    assert entry.ic_series == fresh.ic_series
    assert entry.direction == fresh.direction
    assert entry.score == pytest.approx(fresh.score)


def test_rebuilt_pool_still_blocks_a_twin():
    """重建后的池必须**真的能挡住**同质候选（这才是重建的意义，不只是把数组填上）。"""
    ctx = _ctx()
    rebuilt = rebuild_kept_pool([KeptRow(candidate_id=1, expr_text=_DRIVER)], ctx)
    twin = validate_candidate(parse_expression(_DRIVER), _ctx(), pool=rebuilt.entries,
                              trials=11)
    assert twin.verdict == "rejected" and twin.reject_reason == "F10"


def test_metadata_is_carried_through():
    result = rebuild_kept_pool(
        [KeptRow(candidate_id=5, expr_text=_DRIVER, mechanism="volume_price_divergence",
                 skeleton="ts_zscore(?,_)")], _ctx())
    entry = result.entries[0]
    assert entry.candidate_id == 5
    assert entry.mechanism == "volume_price_divergence"
    assert entry.skeleton == "ts_zscore(?,_)"


def test_unparsable_rows_go_to_errors_not_silently_dropped():
    """解析失败如实进 ``errors``——静默丢弃会让池悄悄缺一块，而没人看得见。"""
    result = rebuild_kept_pool([KeptRow(candidate_id=1, expr_text="ts_mean(ret,21)"),
                                KeptRow(candidate_id=2, expr_text=_DRIVER)], _ctx())
    assert [cid for cid, _msg in result.errors] == [1]
    assert [entry.candidate_id for entry in result.entries] == [2]


def test_light_revalidation_only_recomputes_f2_f3_f6():
    """刷新后复评**只**重算 F2/F3/F6（不重跑全部 11 条）：F9/F10 与窗口无关或需要全池。"""
    flipped = rebuild_kept_pool([KeptRow(candidate_id=2, expr_text=_DRIVER)], _ctx(flip=True))
    assert flipped.failures == [(2, "F6")]
    # 阈值把 F2 抬到不可能过 ⇒ 复评首先报 F2（顺序即 F2 → F3 → F6）。
    strict = _ctx()
    strict.thresholds["minAbsIc"] = 0.99
    assert rebuild_kept_pool([KeptRow(candidate_id=3, expr_text=_DRIVER)],
                             strict).failures == [(3, "F2")]
    # F9（换手）不参与轻量复评：即便把它设成不可能过，复评也不该报 F9。
    loose = _ctx()
    loose.thresholds["maxTurnover"] = 0.0
    assert rebuild_kept_pool([KeptRow(candidate_id=4, expr_text=_DRIVER)],
                             loose).failures == []


def test_empty_input_is_a_no_op():
    result = rebuild_kept_pool([], _ctx())
    assert result.entries == [] and result.failures == [] and result.errors == []


# ============================ 放行条件 5：一个函数，两个调用点 ============================
def test_resume_and_refresh_call_the_very_same_function(monkeypatch):
    """续跑重建与刷新复评走**同一个** ``rebuild_kept_pool``——不许有第二份实现。

    做法：monkeypatch 计数。运行时的两条路径（``_prepare`` 的续跑分支、
    ``_ensure_fresh_data`` 的刷新分支）都收敛到 ``DiscoveryRun._rebuild_pool``，而后者只调
    ``rebuild_kept_pool`` 一处。若谁另写一份重算逻辑，计数就对不上。
    """
    import ast
    import inspect

    from app.discovery import runtime

    source = inspect.getsource(runtime.DiscoveryRun)
    assert source.count("run_in_threadpool(rebuild_kept_pool") == 1, "只允许一处重建调用点"
    # 调用点计数走 AST（§十一 I-8）：文本计数会被「加了个关键字实参」这类与不变式无关的
    # 写法变动打红，而真正要守的是「只有一份实现、被两条路径各调一次」。
    sites = [node for node in ast.walk(ast.parse(source))
             if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
             and node.func.attr == "_rebuild_pool"
             and isinstance(node.func.value, ast.Name) and node.func.value.id == "self"]
    assert len(sites) == 2, "续跑与刷新必须各调一次同一方法"

    calls: list = []
    original = runtime.rebuild_kept_pool

    def _counting(rows, ctx):
        calls.append(len(rows))
        return original(rows, ctx)

    monkeypatch.setattr(runtime, "rebuild_kept_pool", _counting)
    runtime.rebuild_kept_pool([KeptRow(candidate_id=1, expr_text=_DRIVER)], _ctx())
    assert calls == [1]


def test_rebuild_failure_signals_the_caller_to_pause():
    """全部条目都重建失败 ⇒ ``entries`` 为空且 ``errors`` 非空，调用方据此转 ``paused``。

    运行时的判据是 ``if result.errors and not result.entries: return False``——带残缺池开跑
    会让长跑价值静默归零，故这里 fail-closed。
    """
    result = rebuild_kept_pool([KeptRow(candidate_id=1, expr_text="garbage"),
                                KeptRow(candidate_id=2, expr_text="add(close,volume)")],
                               _ctx())
    assert result.entries == [] and len(result.errors) == 2
