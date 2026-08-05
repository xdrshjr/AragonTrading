"""体检守护的运行时行为（discovery-asset-ledger §2.6 · AL-7 / AL-9 / AL-12 / P1-2）。

四条**承重**：
- **AL-7 双闸**：任一为假 ⇒ 该 tick 零 DB 写、零取数（只在启动时判一次的话运行期关不掉）。
- **AL-12 分组**：同 ``(instId, bar)`` 一组一次取数；不同组各取一次。
- **P1-2 memo 纪律**：``memo`` **一组一份、跨组必新建**——共用一个 memo 时第二个组会在
  **别人的字段上下文**上求值，不抛错、指标照算、全是错的。
- **AL-9 资源闸**：按 ``2 × backtests_per_segment()`` 预估。按 ``per_segment``（不乘 2）计的
  实现会在同一场景下多处理一个资产，且表现为「从没触发过闸」。
"""
from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import delete, func, select

from app.config import Settings, get_settings
from app.db.models import DiscoveryAsset, DiscoveryAssetCheck
from app.db.session import dispose_engine, get_sessionmaker, init_db
from app.discovery import asset_repositories as asset_repo
from app.discovery import health_runtime, window_loader
from app.engine.backtester import candles_to_df

from .discovery_synthetic import trend_candles

TS0 = 1_700_000_000
STEP = 3600
EXPR = "sub(ts_mean(ret,20),ts_mean(ret,60))"


def _candles(n: int = 1600):
    """右端对齐到「已确认根」，使 ``fresh_start_of`` 的窗口落在合成序列内。"""
    end = health_runtime.health.aligned_check_end(_frozen_now(), STEP)
    return trend_candles(n, seed=7, ts0=end - (n - 1) * STEP)


_NOW = float(TS0)


def _frozen_now() -> float:
    return _NOW


@pytest.fixture(autouse=True)
def frozen_clock(monkeypatch):
    """墙钟固定：窗口口径与调度时刻都依赖 ``time.time()``，不冻住用例就不可重复。"""
    monkeypatch.setattr(health_runtime.time, "time", _frozen_now)
    yield


@pytest.fixture
def guard():
    get_settings.cache_clear()
    return health_runtime.HealthGuard()


async def _fresh():
    await init_db()
    sm = get_sessionmaker()
    async with sm() as session:
        await session.execute(delete(DiscoveryAssetCheck))
        await session.execute(delete(DiscoveryAsset))
        await session.commit()
    return sm


async def _seed_factor(sm, *, key: str, inst_id: str = "BTC-USDT-SWAP", bar: str = "1H",
                       expr: str = EXPR) -> int:
    async with sm() as session:
        row = await asset_repo.create_asset(session, asset_repo.AssetValues(
            asset_key=key, kind="factor", inst_id=inst_id, bar=bar, horizon=6,
            direction="long", expr_text=expr, genome={}, describe_text="", mechanism="",
            label=key, found_ts=TS0, source={},
            mined_window={"endTs": int(_frozen_now()) - 400 * STEP},
            accept_metrics={"is": {"rankIc": 0.05, "ir": 0.9}},
            accept_thresholds={"minSamples": 20, "minCoverage": 0.5, "minAbsIc": 0.001,
                               "minAbsIr": 0.1},
            trials_at_accept=1, next_check_ts=TS0 - 10))
    return int(row.id)


async def _seed_rotation(sm, *, key: str) -> int:
    genome = {"members": [{"templateKey": "momentum_breakout", "params": {}},
                          {"templateKey": "bollinger_reversion", "params": {}}],
              "policy": {}}
    async with sm() as session:
        row = await asset_repo.create_asset(session, asset_repo.AssetValues(
            asset_key=key, kind="strategy_combo", inst_id="BTC-USDT-SWAP", bar="1H",
            horizon=0, direction="", expr_text="", genome=genome, describe_text="",
            mechanism="rotation", label=key, found_ts=TS0, source={},
            mined_window={"endTs": int(_frozen_now()) - 400 * STEP},
            accept_metrics={"oos": {"sharpe": 1.0}}, accept_thresholds={},
            trials_at_accept=1, next_check_ts=TS0 - 10))
    return int(row.id)


def _patch_loader(monkeypatch, calls: list, candles=None):
    async def _load(_rest, **kwargs):
        calls.append((kwargs["inst_id"], kwargs["bar"]))
        return list(candles if candles is not None else _candles()), {}

    monkeypatch.setattr(window_loader, "load_window", _load)
    monkeypatch.setattr(health_runtime.window_loader, "load_window", _load)
    monkeypatch.setattr(health_runtime, "get_rest_client", lambda: object())


async def _count_checks(sm) -> int:
    async with sm() as session:
        return int((await session.execute(
            select(func.count()).select_from(DiscoveryAssetCheck))).scalar() or 0)


# ============================ AL-7：双闸 ============================
@pytest.mark.parametrize("overrides", [{"discovery_enabled": False},
                                       {"discovery_health_enabled": False}])
async def test_either_switch_off_means_zero_side_effects(guard, monkeypatch,
                                                         overrides: dict) -> None:
    sm = await _fresh()
    try:
        await _seed_factor(sm, key="f:switch")
        calls: list = []
        _patch_loader(monkeypatch, calls)
        monkeypatch.setattr(health_runtime, "get_settings", lambda: Settings(**overrides))
        await guard._tick()
        assert calls == []                    # 零取数
        assert await _count_checks(sm) == 0   # 零 DB 写
        assert guard.state()["enabled"] is False
    finally:
        await dispose_engine()


# ============================ AL-12：分组取数 ============================
async def test_same_market_is_fetched_once(guard, monkeypatch) -> None:
    sm = await _fresh()
    try:
        await _seed_factor(sm, key="f:same1")
        await _seed_factor(sm, key="f:same2", expr="ts_zscore(ret,20)")
        calls: list = []
        _patch_loader(monkeypatch, calls)
        await guard._tick()
        assert calls == [("BTC-USDT-SWAP", "1H")]
        assert await _count_checks(sm) == 2
    finally:
        await dispose_engine()


async def test_different_markets_are_fetched_separately(guard, monkeypatch) -> None:
    sm = await _fresh()
    try:
        await _seed_factor(sm, key="f:btc")
        await _seed_factor(sm, key="f:eth", inst_id="ETH-USDT-SWAP")
        calls: list = []
        _patch_loader(monkeypatch, calls)
        await guard._tick()
        assert sorted(calls) == [("BTC-USDT-SWAP", "1H"), ("ETH-USDT-SWAP", "1H")]
    finally:
        await dispose_engine()


# ============================ P1-2：memo 不串味 ============================
async def test_memo_is_never_shared_across_groups(guard, monkeypatch) -> None:
    """两个组各含一条相同表达式的资产 ⇒ 第二组的指标与它**单独跑**时逐位相等。

    共用一个 memo 时，``validation._context_of`` 的固定键会让第二组在第一组的字段上下文上
    求值——不抛错、指标照算、全是错的。
    """
    sm = await _fresh()
    try:
        await _seed_factor(sm, key="f:g1")
        await _seed_factor(sm, key="f:g2", inst_id="ETH-USDT-SWAP")
        first = _candles()
        second = trend_candles(1600, seed=11,
                               ts0=health_runtime.health.aligned_check_end(_frozen_now(),
                                                                          STEP)
                               - 1599 * STEP)

        async def _load(_rest, **kwargs):
            return (list(first if kwargs["inst_id"] == "BTC-USDT-SWAP" else second), {})

        monkeypatch.setattr(health_runtime.window_loader, "load_window", _load)
        monkeypatch.setattr(health_runtime, "get_rest_client", lambda: object())
        await guard._tick()
        async with sm() as session:
            rows = list((await session.execute(select(DiscoveryAssetCheck).order_by(
                DiscoveryAssetCheck.asset_id))).scalars().all())
        grouped = {int(row.asset_id): asset_repo.loads(row.metrics_json, {})["fresh"]
                   for row in rows}
        solo = _measure_alone(second)
        second_asset = max(grouped)
        assert grouped[second_asset]["rankIc"] == solo["rankIc"]
        assert grouped[second_asset]["coverage"] == solo["coverage"]
    finally:
        await dispose_engine()


def _measure_alone(candles) -> dict:
    """同一条资产**单独**跑一次（全新 memo）的基准值。"""
    limits = health_runtime.health.HealthLimits.of(Settings())
    df = candles_to_df(candles)
    data = health_runtime._GroupData(candles=list(candles), frames={}, df_full=df,
                                     window_start_ts=int(df["ts"].iloc[0]),
                                     window_end_ts=int(df["ts"].iloc[-1]))
    fresh_start = health_runtime.health.fresh_start_of(
        int(_frozen_now()) - 400 * STEP, data.window_end_ts, STEP, limits)
    window = health_runtime.health.split_window(
        list(df["ts"]), fresh_start_ts=fresh_start, window_start_ts=data.window_start_ts,
        window_end_ts=data.window_end_ts)
    asset = type("A", (), {"kind": "factor", "expr_text": EXPR, "horizon": 6,
                           "direction": "long", "accept_thresholds_json": "{}"})()
    return health_runtime._measure_factor(asset, data, window)


# ============================ AL-9：资源闸按 2 × per_segment 计 ============================
async def test_backtest_cap_counts_both_segments(guard, monkeypatch) -> None:
    """3 个 2 成员轮动资产（底层 ``2 × 2 == 4`` 次）+ cap=10 ⇒ 只处理 2 个，第 3 个计入闸。

    按 ``per_segment``（=2）计的实现会处理 **3** 个——护栏放行量正好翻倍，而表现是
    「从没触发过闸」，看起来完全正常。
    """
    sm = await _fresh()
    try:
        for index in range(3):
            await _seed_rotation(sm, key=f"r:combo{index}")
        _patch_loader(monkeypatch, [])
        monkeypatch.setattr(
            health_runtime, "get_settings",
            lambda: _settings(discovery_health_max_backtests_per_tick=10,
                              discovery_health_batch=8))
        monkeypatch.setattr(health_runtime, "_measure",
                            lambda *_a: ({"tradeCount": 30, "sharpe": 1.0,
                                          "totalReturn": 0.2, "maxDrawdown": -0.1},
                                         "rebased", 4))
        await guard._tick()
        assert await _count_checks(sm) == 2
        assert guard.state()["skippedByCap"] == 1
    finally:
        await dispose_engine()


def _settings(**overrides) -> Settings:
    return Settings(**overrides)


# ============================ AL-8：取数失败 vs 求值失败 ============================
async def test_market_failure_writes_no_check_row(guard, monkeypatch) -> None:
    """取数失败整组跳过、**不写** check 行——否则每次网络抖动都在健康时间线插一根尖刺。"""
    sm = await _fresh()
    try:
        asset_id = await _seed_factor(sm, key="f:down")
        async with sm() as session:
            before = int((await asset_repo.get_asset(session, asset_id)).next_check_ts)

        async def _boom(_rest, **_kwargs):
            raise OSError("network down")

        monkeypatch.setattr(health_runtime.window_loader, "load_window", _boom)
        monkeypatch.setattr(health_runtime, "get_rest_client", lambda: object())
        await guard._tick()
        async with sm() as session:
            after = int((await asset_repo.get_asset(session, asset_id)).next_check_ts)
        assert await _count_checks(sm) == 0
        assert after == before                 # ``next_check_ts`` 不动（AL-8 第四支）
        assert guard.state()["dataFailures"] == 1
    finally:
        await dispose_engine()


async def test_evaluation_failure_writes_an_error_row(guard, monkeypatch) -> None:
    """求值失败**写** ``status="error"`` 的 check 行（是资产自身的问题，与取数失败相对）。"""
    sm = await _fresh()
    try:
        await _seed_factor(sm, key="f:broken", expr="ts_mean(ret,21)")   # 窗口越梯，解析必失败
        _patch_loader(monkeypatch, [])
        await guard._tick()
        async with sm() as session:
            rows = list((await session.execute(
                select(DiscoveryAssetCheck))).scalars().all())
        assert len(rows) == 1 and rows[0].status == "error" and rows[0].error
    finally:
        await dispose_engine()


async def test_error_backoff_uses_its_own_streak_not_the_fail_streak(guard,
                                                                    monkeypatch) -> None:
    """连续 error 的退避指数走**独立**计数：AL-8 不许 error 动 ``consecutive_fail``，
    拿后者当指数就是用一个语义无关的数决定重试节奏；任何非 error 结果立刻清零。"""
    sm = await _fresh()
    try:
        asset_id = await _seed_factor(sm, key="f:backoff", expr="ts_mean(ret,21)")
        _patch_loader(monkeypatch, [])
        limits = health_runtime.health.HealthLimits.of(Settings())
        seen = []
        for _ in range(3):
            await guard._tick()
            async with sm() as session:
                row = await asset_repo.get_asset(session, asset_id)
                seen.append(int(row.next_check_ts))
                row.next_check_ts = TS0 - 10          # 强制下一 tick 仍到期
                assert int(row.consecutive_fail or 0) == 0    # error 不动迟滞计数
                await session.commit()
        base = int(limits.interval_s)
        assert [ts - int(_frozen_now()) for ts in seen] == [base, base * 2, base * 4]
        guard._error_streak.pop(asset_id, None)
        assert guard._note_error_streak(asset_id, "healthy") == 0
    finally:
        await dispose_engine()


async def test_asset_timeout_is_recorded_as_error(guard, monkeypatch) -> None:
    sm = await _fresh()
    try:
        await _seed_factor(sm, key="f:slow")
        _patch_loader(monkeypatch, [])
        monkeypatch.setattr(
            health_runtime, "get_settings",
            lambda: _settings(discovery_health_asset_timeout_s=0.001))

        def _slow(*_args):
            import time as real_time

            real_time.sleep(0.2)
            return {}, "", 0

        monkeypatch.setattr(health_runtime, "_measure", _slow)
        await guard._tick()
        async with sm() as session:
            rows = list((await session.execute(
                select(DiscoveryAssetCheck))).scalars().all())
        assert len(rows) == 1 and rows[0].status == "error" and rows[0].error == "timeout"
    finally:
        await dispose_engine()


# ============================ claim：同步预占 ============================
def test_claim_is_exclusive_and_releasable(guard) -> None:
    assert guard.claim(7) is True
    assert guard.claim(7) is False           # 无 await，杜绝 TOCTOU
    guard.release(7)
    assert guard.claim(7) is True


async def test_check_now_releases_the_claim_even_on_failure(guard, monkeypatch) -> None:
    """取数失败也必须放开预占，否则该资产此后永远 409。"""
    sm = await _fresh()
    try:
        asset_id = await _seed_factor(sm, key="f:claim")

        async def _boom(_rest, **_kwargs):
            raise OSError("network down")

        monkeypatch.setattr(health_runtime.window_loader, "load_window", _boom)
        monkeypatch.setattr(health_runtime, "get_rest_client", lambda: object())
        guard.claim(asset_id)
        try:
            with pytest.raises(Exception):
                await guard.check_now(asset_id)
        finally:
            guard.release(asset_id)
        assert guard.claim(asset_id) is True
    finally:
        await dispose_engine()


# ============================ AL-6：守护绝不触碰实体表 ============================
async def test_a_full_tick_never_touches_the_entity_tables(guard, monkeypatch) -> None:
    """**§7.3-10**：跑完一轮完整体检后，四张实体表的内容**逐字节不变**。

    一个后台协程自动改用户正在用的策略，是本项目能犯的最严重的错误。因此这条判据不看
    「我们有没有写代码去改」，而是前后各 dump 一次直接比对。
    """
    from app.db.models import Factor, FactorSet, Strategy, StrategyRun

    sm = await _fresh()
    try:
        await _seed_factor(sm, key="f:safety")
        await _seed_rotation(sm, key="r:safety")
        _patch_loader(monkeypatch, [])
        monkeypatch.setattr(health_runtime, "_measure",
                            lambda *_a: ({"tradeCount": 30, "sharpe": 1.0,
                                          "totalReturn": 0.2, "maxDrawdown": -0.1},
                                         "rebased", 4))

        async def _dump() -> dict:
            async with sm() as session:
                out = {}
                for model in (Factor, FactorSet, Strategy, StrategyRun):
                    rows = list((await session.execute(select(model))).scalars().all())
                    out[model.__name__] = [
                        {col.name: getattr(row, col.name) for col in model.__table__.columns}
                        for row in rows]
                return out

        before = await _dump()
        await guard._tick()
        assert await _count_checks(sm) == 2      # 体检确实跑过（否则这条判据是空转的）
        assert await _dump() == before
    finally:
        await dispose_engine()


async def test_guard_shutdown_is_cancel_safe(guard) -> None:
    guard.start()
    await asyncio.sleep(0)
    await guard.shutdown()
    assert guard.state()["running"] is False
