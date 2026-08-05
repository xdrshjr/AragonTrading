"""登记点与登记内容（discovery-asset-ledger §2.2.2 · AL-5 / P1-1 / P1-10）。

两条**承重**：
- 登记失败 fail-open **但必须计数**（只计数不呈现等于没计数，呈现落点在 DiscoveryMonitor）；
- ``accept_thresholds_json`` **逐轴取对配置键**：因子在 ``thresholds``、策略在
  ``strategyThresholds``。取错了 ``normalize_strategy_thresholds`` 会把不认识的键全部丢掉、
  静默回落默认值，AL-3 第二句整条空转且不报任何错。
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import delete

from app.config import Settings
from app.db.models import DiscoveryAsset, DiscoveryCandidate, DiscoveryIteration, DiscoveryRun
from app.db.session import dispose_engine, get_sessionmaker, init_db
from app.discovery import asset_repositories as asset_repo
from app.discovery import assets
from app.discovery import repositories as repo
from app.discovery.iteration import IterationRunner
from app.discovery.strategy_gates import DEFAULT_STRATEGY_THRESHOLDS

TS0 = 1_700_000_000
EXPR = "sub(ts_mean(overnight,20),delta(amplitude,5))"
CUSTOM_STRATEGY_THRESHOLDS = {"minTrades": 42.0, "minSharpe": 1.7, "maxDrawdown": 0.22,
                              "minCalmar": 2.5, "oosRetain": 0.66, "maxTradeRate": 0.02,
                              "minProfitFactor": 1.9, "maxEquityCorr": 0.5,
                              "costStressMult": 3.0, "trialPenaltyK": 0.3}


@dataclass
class _Spec:
    """``RunSpec`` 的最小替身（``register_kept`` 只用到这五个字段）。"""

    run_id: int
    inst_id: str
    bar: str
    config: dict
    settings: Settings


def _candidate(kind: str, *, genome_hash: str, metrics: dict, genome: dict | None = None,
               expr: str = "") -> repo.CandidateRow:
    return repo.CandidateRow(
        kind=kind, genome_hash=genome_hash, genome=genome or {}, expr_text=expr,
        describe_text="中文渲染", skeleton="sk", mechanism="gap_premium", source="mutate",
        verdict="kept", metrics=metrics, trials_at_accept=812, created_ts=TS0)


async def _seed_run(sm, rows: list, config: dict) -> int:
    async with sm() as session:
        run = await repo.create_run(session, values=repo.RunValues(
            label="x", inst_id="BTC-USDT-SWAP", bar="15m", symbols=["BTC-USDT-SWAP"],
            config=config, checkpoint={}, started_ts=TS0))
        await repo.commit_iteration(
            session, run.id, candidates=rows,
            iteration=repo.IterationRow(idx=1, started_ts=TS0, finished_ts=TS0, budget={},
                                        stats={}, llm={}),
            checkpoint_json="{}", counters={}, candidate_row_cap=100, iteration_row_cap=100)
    return int(run.id)


async def _fresh():
    await init_db()
    sm = get_sessionmaker()
    async with sm() as session:
        for model in (DiscoveryAsset, DiscoveryCandidate, DiscoveryIteration, DiscoveryRun):
            await session.execute(delete(model))
        await session.commit()
    return sm


def _window() -> assets.MinedWindow:
    return assets.MinedWindow(start_ts=TS0 - 4000 * 900, end_ts=TS0, is_ratio=0.7,
                              window_bars=4000, split_ts=TS0 - 1200 * 900)


async def test_kept_candidates_land_in_the_ledger_with_a_full_window() -> None:
    """**P1-1**：``mined_window_json`` 五个键齐全，``endTs`` 就是传进来的那份。"""
    sm = await _fresh()
    try:
        row = _candidate("factor", genome_hash="h1", expr=EXPR,
                         metrics={"direction": "short", "horizon": 9,
                                  "is": {"rankIc": 0.044, "ir": 0.62}})
        run_id = await _seed_run(sm, [row], {"thresholds": {"minAbsIc": 0.09}})
        spec = _Spec(run_id, "BTC-USDT-SWAP", "15m", {"thresholds": {"minAbsIc": 0.09},
                                                      "horizon": 6}, Settings())
        stats = await assets.register_kept(spec, rows=[row], window=_window(),
                                           iteration_idx=3)
        async with sm() as session:
            rows, total = await asset_repo.list_assets(session, asset_repo.AssetQuery())
        assert (stats.created, stats.merged, total) == (1, 0, 1)
        asset = rows[0]
        window = asset_repo.loads(asset.mined_window_json, {})
        assert set(window) == {"startTs", "endTs", "isRatio", "windowBars", "splitTs"}
        assert window["endTs"] == TS0
        assert asset.direction == "short"          # 冻结方向 = AL-1 的输入
        assert asset.horizon == 9                  # 取候选自带的，不取 run 配置（评审 P2-7）
        assert asset.inst_id == "BTC-USDT-SWAP" and asset.bar == "15m"
        source = asset_repo.loads(asset.sources_json, [])[0]
        assert source["iterationIdx"] == 3 and source["candidateId"] > 0
        assert source["acceptSummary"]["isRankIc"] == 0.044
        # 攒够新数据才第一次体检
        assert asset.next_check_ts == TS0 + Settings().discovery_health_min_fresh_bars * 900
    finally:
        await dispose_engine()


async def test_factor_thresholds_snapshot_uses_the_factor_config_key() -> None:
    sm = await _fresh()
    try:
        row = _candidate("factor", genome_hash="h1", expr=EXPR,
                         metrics={"direction": "long", "is": {}})
        run_id = await _seed_run(sm, [row], {"thresholds": {"minAbsIc": 0.09}})
        spec = _Spec(run_id, "BTC-USDT-SWAP", "15m", {"thresholds": {"minAbsIc": 0.09}},
                     Settings())
        await assets.register_kept(spec, rows=[row], window=_window())
        async with sm() as session:
            rows, _total = await asset_repo.list_assets(session, asset_repo.AssetQuery())
        assert asset_repo.loads(rows[0].accept_thresholds_json, {})["minAbsIc"] == 0.09
    finally:
        await dispose_engine()


async def test_strategy_thresholds_snapshot_uses_strategy_config_key() -> None:
    """**P1-10**：全非默认的 ``strategyThresholds`` 必须逐键落进快照，而**不是**默认值表。"""
    sm = await _fresh()
    try:
        genome = {"templateKey": "momentum_breakout", "params": {"lookback": 20}}
        row = _candidate("strategy_param", genome_hash="h2", genome=genome,
                         metrics={"oos": {"sharpe": 1.4, "totalReturn": 0.3}})
        config = {"thresholds": {"minAbsIc": 0.09},
                  "strategyThresholds": dict(CUSTOM_STRATEGY_THRESHOLDS)}
        run_id = await _seed_run(sm, [row], config)
        await assets.register_kept(_Spec(run_id, "BTC-USDT-SWAP", "15m", config, Settings()),
                                   rows=[row], window=_window())
        async with sm() as session:
            rows, _total = await asset_repo.list_assets(session, asset_repo.AssetQuery())
        snapshot = asset_repo.loads(rows[0].accept_thresholds_json, {})
        assert snapshot == CUSTOM_STRATEGY_THRESHOLDS
        assert snapshot != DEFAULT_STRATEGY_THRESHOLDS
        assert rows[0].horizon == 0 and rows[0].direction == ""
        assert asset_repo.loads(rows[0].sources_json, [])[0]["acceptSummary"][
            "oosSharpe"] == 1.4
    finally:
        await dispose_engine()


async def test_asset_limit_stops_creating_but_keeps_merging() -> None:
    """触顶只停「新建」、不停「合并溯源」，且计数如实上报（§4.10：**永不**作为 HTTP 错误）。"""
    sm = await _fresh()
    try:
        first = _candidate("factor", genome_hash="h1", expr=EXPR,
                           metrics={"direction": "long", "is": {}})
        second = _candidate("factor", genome_hash="h2", expr="ts_std(ret,30)",
                            metrics={"direction": "long", "is": {}})
        config = {"thresholds": {}}
        run_id = await _seed_run(sm, [first, second], config)
        settings = Settings(discovery_health_max_assets=100)
        spec = _Spec(run_id, "BTC-USDT-SWAP", "15m", config, settings)
        await assets.register_kept(spec, rows=[first], window=_window())
        capped = _Spec(run_id, "BTC-USDT-SWAP", "15m", config,
                       Settings(discovery_health_max_assets=100))
        # 字段下界是 100（生产不该配这么小），用例只想验证触顶分支，故绕开校验直接改实例。
        object.__setattr__(capped.settings, "discovery_health_max_assets", 1)
        stats = await assets.register_kept(capped, rows=[first, second], window=_window())
        async with sm() as session:
            _rows, total = await asset_repo.list_assets(session, asset_repo.AssetQuery())
        assert (stats.merged, stats.created, stats.skipped) == (1, 0, 1)
        assert total == 1
    finally:
        await dispose_engine()


async def test_four_axes_each_occupy_one_row() -> None:
    """**§7.3-5**：四条轴各挖到一条 ⇒ 台账里各占一行（身份键前缀互不相同）。"""
    sm = await _fresh()
    try:
        combo_genome = {"members": [{"factorKey": "expr",
                                     "params": {"expr": "ts_mean(ret,20)"},
                                     "direction": "long", "name": "expr:a"},
                                    {"factorKey": "expr",
                                     "params": {"expr": "ts_std(ret,30)"},
                                     "direction": "short", "name": "expr:b"}],
                        "weights": [0.5, 0.5], "thresholds": {}}
        param_genome = {"templateKey": "momentum_breakout", "params": {"lookback": 20}}
        rows = [
            _candidate("factor", genome_hash="h1", expr=EXPR,
                       metrics={"direction": "long", "is": {}}),
            _candidate("factor_combo", genome_hash="h2", genome=combo_genome,
                       metrics={"oos": {"sharpe": 1.0}}),
            _candidate("strategy_param", genome_hash="h3", genome=param_genome,
                       metrics={"oos": {"sharpe": 1.2}}),
            _candidate("strategy_combo", genome_hash="h4",
                       genome={"members": [param_genome], "policy": {}},
                       metrics={"oos": {"sharpe": 0.9}}),
        ]
        config = {"thresholds": {}, "strategyThresholds": {}}
        run_id = await _seed_run(sm, rows, config)
        stats = await assets.register_kept(
            _Spec(run_id, "BTC-USDT-SWAP", "15m", config, Settings()), rows=rows,
            window=_window())
        async with sm() as session:
            listed, total = await asset_repo.list_assets(session, asset_repo.AssetQuery())
        assert stats.created == 4 and total == 4
        assert {row.kind for row in listed} == {"factor", "factor_combo",
                                                "strategy_param", "strategy_combo"}
        assert {row.asset_key[0] for row in listed} == {"f", "c", "s", "r"}
    finally:
        await dispose_engine()


async def test_same_expression_in_two_runs_merges_into_one_row() -> None:
    """**§7.3-6**：同一表达式在两个 run 被挖到 ⇒ 一行、``sourceCount == 2``、首次时间取最早。"""
    sm = await _fresh()
    try:
        config = {"thresholds": {}}
        early = _candidate("factor", genome_hash="h1", expr=EXPR,
                           metrics={"direction": "long", "is": {}})
        late = _candidate("factor", genome_hash="h1", expr=EXPR,
                          metrics={"direction": "long", "is": {}})
        late.created_ts = TS0 + 9_000
        first_run = await _seed_run(sm, [early], config)
        second_run = await _seed_run(sm, [late], config)
        await assets.register_kept(_Spec(first_run, "BTC-USDT-SWAP", "15m", config,
                                         Settings()), rows=[early], window=_window())
        stats = await assets.register_kept(_Spec(second_run, "BTC-USDT-SWAP", "15m", config,
                                                 Settings()), rows=[late], window=_window())
        async with sm() as session:
            listed, total = await asset_repo.list_assets(session, asset_repo.AssetQuery())
        assert total == 1 and stats.merged == 1 and stats.created == 0
        asset = listed[0]
        assert asset.source_count == 2
        assert asset.first_found_ts == TS0 and asset.last_found_ts == TS0 + 9_000
        run_ids = {item["runId"]
                   for item in asset_repo.loads(asset.sources_json, [])}
        assert run_ids == {first_run, second_run}
    finally:
        await dispose_engine()


# ============================ AL-5：fail-open 且必须计数 ============================
class _FakeRun:
    """``IterationRunner`` 只用到 ``spec`` / ``run_id`` / ``_counters`` 三样。"""

    def __init__(self) -> None:
        self.run_id = 7
        self.spec = _Spec(7, "BTC-USDT-SWAP", "15m", {}, Settings())
        self._counters: dict = {}


async def test_register_failure_is_fail_open_and_counted(monkeypatch) -> None:
    """登记抛错 ⇒ 迭代照常往下走，且 ``ledgerErrors`` +1（随同轮 ``state`` 事件下发）。"""
    async def _boom(*_args, **_kwargs):
        raise RuntimeError("ledger down")

    monkeypatch.setattr(assets, "register_kept", _boom)
    monkeypatch.setattr(assets.MinedWindow, "of", staticmethod(lambda _run: _window()))
    run = _FakeRun()
    rows = [_candidate("factor", genome_hash="h1", expr=EXPR, metrics={})]
    await IterationRunner(run)._register_assets(1, rows)
    assert run._counters["ledgerErrors"] == 1
    assert "ledgerRegistered" not in run._counters


async def test_register_success_bumps_the_visible_counter(monkeypatch) -> None:
    async def _ok(*_args, **_kwargs):
        return assets.RegisterStats(created=2, merged=1)

    monkeypatch.setattr(assets, "register_kept", _ok)
    monkeypatch.setattr(assets.MinedWindow, "of", staticmethod(lambda _run: _window()))
    run = _FakeRun()
    rows = [_candidate("factor", genome_hash="h1", expr=EXPR, metrics={})]
    await IterationRunner(run)._register_assets(1, rows)
    assert run._counters["ledgerRegistered"] == 3


async def test_rejected_candidates_are_never_registered(monkeypatch) -> None:
    calls: list = []
    monkeypatch.setattr(assets, "register_kept",
                        lambda *a, **k: calls.append(1))
    run = _FakeRun()
    rejected = _candidate("factor", genome_hash="h1", expr=EXPR, metrics={})
    rejected.verdict = "rejected"
    await IterationRunner(run)._register_assets(1, [rejected])
    assert calls == [] and run._counters == {}
