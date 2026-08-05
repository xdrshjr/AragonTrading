"""策略 kept 池的重建与降级（discovery-strategy-axes §2.6.1 · BL-5）。

**一个实现，两个调用点**：续跑与数据刷新都必须收敛到 ``rebuild_strategy_pool``。窗口一右移，
逐根收益序列就与新窗口不再等长，S8 会失去基准——每次重启后都会开始收一批同质策略。

重建必须**复跑一次装配 + 回测**（上游 I-2 的教训：解析得通不等于仍合法；被外部改坏或跨版本
遗留的基因进池就成了一条错误的去重基准，会误杀本该入库的新候选且完全不可见）。
"""
from __future__ import annotations

import ast
import inspect

from app.config import Settings
from app.discovery import runtime as runtime_module
from app.discovery.backtest_lane import run_split_backtest
from app.discovery.catalogs import SearchInputs, minable_templates
from app.discovery.genomes import ParamGenome
from app.discovery.lanes import StrategyLane
from app.discovery.lanes import strategy_lane as strategy_lane_module
from app.discovery.strategy_gates import (
    StrategyKeptRow,
    normalize_strategy_thresholds,
    rebuild_strategy_pool,
)

from .discovery_synthetic import lane_context, trend_candles

_PARAMS = {"surge_pct": 0.02, "breakout_window": 20, "vol_mult": 1.2, "trail_pct": 0.02,
           "stop_loss_pct": 0.03, "max_hold_bars": 48, "strength": 0.9}


def _row(candidate_id: int, params: dict | None = None) -> StrategyKeptRow:
    genome = ParamGenome("momentum_breakout", params or _PARAMS).payload()
    return StrategyKeptRow(candidate_id=candidate_id, kind="strategy_param",
                           genome_hash=f"h{candidate_id}", genome=genome,
                           skeleton="momentum_breakout", family="breakout")


def _build(kind: str, genome_payload):
    from app.discovery.lanes.strategy_lane import assembler_for

    return assembler_for(kind, genome_payload, Settings())


def _rebuild(rows: list, ctx, **over):
    from app.discovery.genomes import GENOME_LOADERS

    return rebuild_strategy_pool(
        rows, ctx, thresholds=normalize_strategy_thresholds(over.get("thresholds")),
        run_split=over.get("run_split", run_split_backtest),
        build_assembler=lambda kind, payload: _build(kind, GENOME_LOADERS[kind](payload)))


# ============================ 一个实现，两个调用点 ============================
def test_resume_and_refresh_share_one_rebuild_entrypoint():
    """AST 收集调用节点（上游 I-8：文本计数会被无关的写法变动打红）。"""
    source = inspect.getsource(runtime_module.DiscoveryRun)
    sites = [node for node in ast.walk(ast.parse(source))
             if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
             and node.func.attr == "_rebuild_pool"
             and isinstance(node.func.value, ast.Name) and node.func.value.id == "self"]
    assert len(sites) == 2, "续跑与刷新必须各调一次同一方法"


def test_strategy_lane_has_exactly_one_rebuild_implementation():
    source = inspect.getsource(strategy_lane_module)
    calls = [node for node in ast.walk(ast.parse(source))
             if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
             and node.func.id == "rebuild_strategy_pool"]
    assert len(calls) == 1, "策略池重建只允许一份实现"


# ============================ 重建行为 ============================
def test_rebuild_recomputes_the_dedupe_baseline_on_the_current_window():
    ctx = lane_context(trend_candles(700))
    result = _rebuild([_row(1), _row(2, {**_PARAMS, "breakout_window": 40})], ctx)
    assert len(result.entries) == 2 and not result.errors
    for entry in result.entries:
        assert entry.bar_returns, "重建出来的条目没有收益序列 ⇒ S8 去重形同虚设"
        assert len(entry.bar_returns) == ctx.split - 1


def test_a_broken_genome_is_reported_not_silently_dropped():
    ctx = lane_context(trend_candles(500))
    broken = StrategyKeptRow(candidate_id=9, kind="strategy_param", genome_hash="hx",
                             genome={"templateKey": "no_such_template", "params": {}})
    result = _rebuild([broken, _row(1)], ctx)
    assert [cid for cid, _reason in result.errors] == [9]
    assert [entry.candidate_id for entry in result.entries] == [1]


def test_total_failure_signals_the_caller_to_pause():
    """运行时判据是 ``if result.errors and not result.entries: return False``——fail-closed。"""
    ctx = lane_context(trend_candles(400))
    broken = [StrategyKeptRow(candidate_id=i, kind="strategy_param", genome_hash=f"h{i}",
                              genome={"templateKey": "nope", "params": {}})
              for i in (1, 2)]
    result = _rebuild(broken, ctx)
    assert result.errors and not result.entries


def test_light_revalidation_marks_failures_without_dropping_the_baseline():
    """``revalidateOnRefresh`` 只决定要不要淘汰；重建**无条件**执行。"""
    ctx = lane_context(trend_candles(700))
    strict = _rebuild([_row(1)], ctx, thresholds={"minSharpe": 99.0})
    assert [cid for cid, _reason in strict.failures] == [1]
    assert len(strict.entries) == 1, "复评不达标不等于失去去重基准"


def test_split_rejection_is_reported_as_an_error(monkeypatch):
    ctx = lane_context(trend_candles(400))

    class _Rejected:
        reject_reason = "S0:missing_htf:12H"

    result = _rebuild([_row(1)], ctx, run_split=lambda a, c: _Rejected())
    assert result.errors and result.errors[0][1] == "S0:missing_htf:12H"


# ============================ 模板签名漂移 ============================
def test_template_signature_changes_when_a_ladder_changes():
    """升级后模板改了 min/max，旧基因可能已越界；不检查就会静默继续繁殖。"""
    catalog = minable_templates("1H", max_htf_frames=3)
    baseline = runtime_module._template_signature(catalog.templates)
    assert baseline and baseline == runtime_module._template_signature(catalog.templates)
    trimmed = tuple(spec for spec in catalog.templates
                    if spec.key != "momentum_breakout")
    assert runtime_module._template_signature(trimmed) != baseline


def test_lane_readiness_reflects_the_upstream_pool():
    lane = StrategyLane(kind="strategy_combo", config={}, momentum=None, llm=None,
                        catalog=SearchInputs(rotation_pool=()))
    assert lane.ready() == (False, "upstream_pool_too_small")
    lane.inputs.rotation_pool = (ParamGenome("momentum_breakout", _PARAMS),
                                 ParamGenome("ema_cross_scalper", {"fast": 9}))
    assert lane.ready() == (True, "")
    assert lane.ready_reason_cn() == ""
