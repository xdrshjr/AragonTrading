"""高级回测（滑动窗口 / Walk-forward）单测 (spec advanced-backtest-walk-forward §9.1)。

覆盖：纯函数（窗口规划 / 稳定性评分 / 跨窗口聚合）、全区间分页与覆盖透明度、MTF 前置暖机覆盖、
run_walk_forward 端到端、persist 合并写、API 路由与校验。OKX 用伪 client（不触网）。
"""
from __future__ import annotations

import json

import pytest
from httpx import ASGITransport, AsyncClient

from app.agent import services
from app.engine.backtester import plan_walk_forward_windows
from app.engine.metrics import compute_stability_score, summarize_windows
from app.okx.types import Candle
from app.strategies.base import get_strategy_class
from tests.conftest import make_synthetic_candles


# ============================ 伪 OKX client ============================
class _PagingRest:
    """支持向更早翻页的伪 OKX：持有连续历史，按 before/after 游标返回区间内最近 limit 根（升序）。"""

    def __init__(self, *, start_ts: int, n: int, step: int = 3600):
        rows = make_synthetic_candles(n, start_ts=start_ts, step=step)
        self._all = [Candle(ts=r["ts"], o=r["o"], h=r["h"], l=r["l"], c=r["c"], vol=r["vol"])
                     for r in rows]
        self.calls = 0

    async def get_candles(self, inst_id, bar, limit=300, before=None, after=None):
        self.calls += 1
        cs = self._all
        if before is not None:
            cs = [c for c in cs if c.ts < before]
        if after is not None:
            cs = [c for c in cs if c.ts > after]
        return cs[-limit:] if len(cs) > limit else cs


class _StubRest:
    """病态伪 OKX：忽略游标，永远返回同一批次（验证「游标不前进即停」防死循环）。"""

    def __init__(self, *, start_ts: int, n: int, step: int = 3600):
        rows = make_synthetic_candles(n, start_ts=start_ts, step=step)
        self._batch = [Candle(ts=r["ts"], o=r["o"], h=r["h"], l=r["l"], c=r["c"], vol=r["vol"])
                       for r in rows]
        self.calls = 0

    async def get_candles(self, inst_id, bar, limit=300, before=None, after=None):
        self.calls += 1
        return list(self._batch)


# ============================ 纯函数：窗口规划 ============================
def test_plan_windows_exact_count_and_boundaries():
    plan = plan_walk_forward_windows(2401, 1000, 200)
    assert plan[0] == (0, 1000)
    assert plan[-1] == (1400, 2400)        # 末尾尾段（2400..2401）被丢弃
    assert all(b - a == 1000 for a, b in plan)
    assert [a for a, _ in plan] == list(range(0, 1500, 200))


def test_plan_windows_degenerates_to_single_when_short():
    assert plan_walk_forward_windows(300, 1000, 200) == [(0, 300)]


def test_plan_windows_drops_incomplete_tail():
    plan = plan_walk_forward_windows(1100, 1000, 200)
    assert plan == [(0, 1000)]             # 200..1200 超界，尾段 1000..1100 丢弃


def test_plan_windows_returns_empty_on_degenerate_inputs():
    assert plan_walk_forward_windows(0, 1000, 200) == []
    assert plan_walk_forward_windows(500, 0, 200) == []
    assert plan_walk_forward_windows(500, 1000, 0) == []


# ============================ 纯函数：稳定性评分 ============================
def test_stability_score_in_unit_interval_and_monotonic():
    steady = compute_stability_score([0.02, 0.021, 0.019, 0.02], profitable_rate=1.0)
    erratic = compute_stability_score([0.3, -0.28, 0.25, -0.3], profitable_rate=0.5)
    assert 0.0 <= erratic <= steady <= 1.0
    assert steady > 0.9 and erratic < 0.5


def test_stability_score_empty_returns_zero():
    assert compute_stability_score([], profitable_rate=0.0) == 0.0


# ============================ 纯函数：跨窗口聚合 ============================
def _win(total_return: float, max_drawdown: float = -0.05, trade_count: int = 3) -> dict:
    return {"startTs": 0, "endTs": 1, "tradeCount": trade_count,
            "metrics": {"totalReturn": total_return, "maxDrawdown": max_drawdown,
                        "winRate": 0.5, "profitFactor": 1.2, "sharpe": 1.0,
                        "tradeCount": trade_count, "avgPnl": 1.0}}


def test_summarize_windows_aggregates_even_count_median():
    windows = [_win(0.10), _win(-0.04), _win(0.06), _win(0.02)]
    s = summarize_windows(windows, uncovered_bars=7, notes=["x"])
    assert s["windowCount"] == 4
    assert s["profitableWindows"] == 3
    assert s["profitableRate"] == round(3 / 4, 6)
    assert s["medianReturn"] == round((0.02 + 0.06) / 2, 6)   # 偶数个 → 中间两者均值
    assert s["bestReturn"] == 0.10 and s["worstReturn"] == -0.04
    assert s["avgReturn"] == round((0.10 - 0.04 + 0.06 + 0.02) / 4, 6)
    assert s["avgMaxDrawdown"] == -0.05
    assert s["uncoveredBars"] == 7 and s["notes"] == ["x"]
    assert 0.0 <= s["stabilityScore"] <= 1.0


def test_summarize_windows_empty():
    s = summarize_windows([], uncovered_bars=3)
    assert s["windowCount"] == 0 and s["uncoveredBars"] == 3 and s["notes"] == []


# ============================ 全区间分页与覆盖透明度 ============================
async def test_load_range_candles_full_coverage_multi_page():
    base = 1_700_000_000
    rest = _PagingRest(start_ts=base, n=2500)
    start_ts, end_ts = base, base + 2400 * 3600
    candles = await services.load_range_candles(rest, inst_id="BTC-USDT-SWAP", bar="1H",
                                                start_ts=start_ts, end_ts=end_ts)
    assert rest.calls >= 2                              # 跨多页翻页
    assert candles[0].ts == start_ts and candles[-1].ts == end_ts
    assert len(candles) == 2401                         # 全覆盖、去重正确
    assert len({c.ts for c in candles}) == len(candles)
    cov = services._coverage_fields(candles, bar="1H", start_ts=start_ts, end_ts=end_ts)
    assert cov["truncated"] is False
    assert cov["effectiveCandleCount"] == cov["requestedBars"] == 2401


async def test_load_range_candles_truncated_when_history_insufficient():
    base = 1_700_000_000
    rest = _PagingRest(start_ts=base + 500 * 3600, n=1000)   # 历史够不到左界
    start_ts, end_ts = base, base + 800 * 3600
    candles = await services.load_range_candles(rest, inst_id="X", bar="1H",
                                                start_ts=start_ts, end_ts=end_ts)
    cov = services._coverage_fields(candles, bar="1H", start_ts=start_ts, end_ts=end_ts)
    assert cov["truncated"] is True
    assert cov["effectiveCandleCount"] < cov["requestedBars"]
    assert candles[0].ts > start_ts


async def test_load_range_candles_terminates_on_fixed_stub():
    base = 1_700_000_000
    rest = _StubRest(start_ts=base, n=300)
    # start_ts 早于 stub 起点 → 永远覆盖不到左界，须靠「游标不前进」判据终止（不死循环）。
    candles = await services.load_range_candles(rest, inst_id="X", bar="1H",
                                                start_ts=base - 100 * 3600, end_ts=base + 200 * 3600)
    assert rest.calls <= 3                              # 有界终止
    assert candles[0].ts == base                        # 仅返回 stub 覆盖到的区间


async def test_load_htf_frames_covers_prewarmup_before_start():
    base = 1_700_000_000
    start_ts, end_ts = base, base + 200 * 14400
    # 4H 历史从 start_ts 之前 100 根开始，断言最早高周期 ts 早于 start_ts（暖机段覆盖，P1-4）。
    rest = _PagingRest(start_ts=start_ts - 100 * 14400, n=400, step=14400)
    frames = await services.load_htf_frames(rest, inst_id="X", start_ts=start_ts, end_ts=end_ts,
                                            base_seconds=3600, timeframes=["4H"], htf_warmup_bars=60)
    assert "4H" in frames and frames["4H"]
    assert frames["4H"][0].ts < start_ts


# ============================ run_walk_forward 端到端 ============================
@pytest.fixture
def db():
    from app.db.session import dispose_engine, get_sessionmaker, init_db
    return dispose_engine, get_sessionmaker, init_db


async def _pick_atomic_no_htf(session) -> int:
    """取一个无高周期声明的原子策略 id（窗口边界确定，便于断言）。"""
    from app.db import repositories as repo
    for s in await repo.list_strategies(session):
        if s.get("unavailable") or s.get("kind") == "combo":
            continue
        cls = get_strategy_class(s["templateKey"])
        if cls is None:
            continue
        inst = cls(dict(cls.meta.default_params))
        if not inst.timeframes():
            return s["id"]
    raise AssertionError("no atomic strategy without HTF found")


async def test_run_walk_forward_window_count_and_boundaries(db):
    dispose_engine, get_sessionmaker, init_db = db
    await init_db()
    base = 1_700_000_000
    rest = _PagingRest(start_ts=base, n=2500)
    start_ts, end_ts = base, base + 2400 * 3600
    try:
        sm = get_sessionmaker()
        async with sm() as session:
            sid = await _pick_atomic_no_htf(session)
        # 独立复算期望窗口（候选 candles 与 service 内部同口径）。
        candles = await services.load_range_candles(_PagingRest(start_ts=base, n=2500),
                                                    inst_id="BTC-USDT-SWAP", bar="1H",
                                                    start_ts=start_ts, end_ts=end_ts)
        plan = plan_walk_forward_windows(len(candles), 1000, 200)

        async with sm() as session:
            out = await services.run_walk_forward(
                session, rest, strategy_id=sid, inst_id="BTC-USDT-SWAP", bar="1H",
                start_ts=start_ts, end_ts=end_ts, window_bars=1000, step_bars=200,
                reset_capital=True)
        assert out["summary"]["windowCount"] == len(plan)
        assert out["windows"][0]["startTs"] == candles[plan[0][0]].ts
        assert out["windows"][-1]["endTs"] == candles[plan[-1][1] - 1].ts
        assert out["summary"]["uncoveredBars"] == max(0, len(candles) - plan[-1][1])
    finally:
        await dispose_engine()


async def test_run_walk_forward_reset_capital_false_runs_chained(db):
    dispose_engine, get_sessionmaker, init_db = db
    await init_db()
    base = 1_700_000_000
    start_ts, end_ts = base, base + 1600 * 3600
    try:
        sm = get_sessionmaker()
        async with sm() as session:
            sid = await _pick_atomic_no_htf(session)
        async with sm() as session:
            out = await services.run_walk_forward(
                session, _PagingRest(start_ts=base, n=1800), strategy_id=sid,
                inst_id="BTC-USDT-SWAP", bar="1H", start_ts=start_ts, end_ts=end_ts,
                window_bars=500, step_bars=250, reset_capital=False)
        assert out["summary"]["windowCount"] >= 2
        assert {"profitableRate", "stabilityScore", "notes", "uncoveredBars"} <= set(out["summary"])
    finally:
        await dispose_engine()


async def test_run_walk_forward_degenerate_single_window_notes(db):
    dispose_engine, get_sessionmaker, init_db = db
    await init_db()
    base = 1_700_000_000
    start_ts, end_ts = base, base + 120 * 3600   # 121 根 < windowBars=1000 → 退化单窗口
    try:
        sm = get_sessionmaker()
        async with sm() as session:
            sid = await _pick_atomic_no_htf(session)
        async with sm() as session:
            out = await services.run_walk_forward(
                session, _PagingRest(start_ts=base, n=200), strategy_id=sid,
                inst_id="BTC-USDT-SWAP", bar="1H", start_ts=start_ts, end_ts=end_ts,
                window_bars=1000, step_bars=200, reset_capital=True)
        assert out["summary"]["windowCount"] == 1
        assert any("单窗口" in note for note in out["summary"]["notes"])
    finally:
        await dispose_engine()


async def test_run_walk_forward_rejects_combo(db):
    from fastapi import HTTPException
    dispose_engine, get_sessionmaker, init_db = db
    await init_db()
    base = 1_700_000_000
    try:
        sm = get_sessionmaker()
        async with sm() as session:
            from app.db import repositories as repo
            combo = next(s for s in await repo.list_strategies(session) if s.get("kind") == "combo")
        async with sm() as session:
            with pytest.raises(HTTPException) as exc:
                await services.run_walk_forward(
                    session, _PagingRest(start_ts=base, n=1500), strategy_id=combo["id"],
                    inst_id="BTC-USDT-SWAP", bar="1H", start_ts=base, end_ts=base + 1400 * 3600,
                    window_bars=500, step_bars=250, reset_capital=True)
            assert exc.value.detail["code"] == "WF_COMBO_UNSUPPORTED"
    finally:
        await dispose_engine()


async def test_persist_walk_forward_merges_config_and_projects_metrics(db):
    dispose_engine, get_sessionmaker, init_db = db
    await init_db()
    base = 1_700_000_000
    start_ts, end_ts = base, base + 2400 * 3600
    try:
        sm = get_sessionmaker()
        async with sm() as session:
            sid = await _pick_atomic_no_htf(session)
        async with sm() as session:
            out = await services.run_walk_forward(
                session, _PagingRest(start_ts=base, n=2500), strategy_id=sid,
                inst_id="BTC-USDT-SWAP", bar="1H", start_ts=start_ts, end_ts=end_ts,
                window_bars=1000, step_bars=200, reset_capital=True)
        run_id = out["runId"]
        async with sm() as session:
            from app.db import repositories as repo
            run = await repo.get_run(session, run_id)
        cfg = json.loads(run.config_json)
        # 合并写：原窗口参数键与 summary/windows 并存（不被覆盖）。
        assert cfg["windowBars"] == 1000 and cfg["stepBars"] == 200 and cfg["resetCapital"] is True
        assert "summary" in cfg and "windows" in cfg
        assert len(cfg["windows"]) == out["summary"]["windowCount"]
        # metrics_json 为展示投影。
        metrics = json.loads(run.metrics_json)
        assert metrics["totalReturn"] == out["summary"]["avgReturn"]
        assert metrics["sharpe"] == out["summary"]["stabilityScore"]
        assert metrics["winRate"] == out["summary"]["profitableRate"]
        assert run.status == "done"
        assert run.start_ts == start_ts and run.end_ts == end_ts
    finally:
        await dispose_engine()


# ============================ API 路由与校验 ============================
def _load_app():
    from app.main import app
    return app


@pytest.fixture
def patch_okx(monkeypatch):
    fake = _PagingRest(start_ts=1_700_000_000, n=2500)
    monkeypatch.setattr("app.api.strategies.get_rest_client", lambda: fake)
    return fake


async def test_walk_forward_api_returns_full_structure(patch_okx):
    from app.db.session import dispose_engine, init_db
    await init_db()
    base = 1_700_000_000
    transport = ASGITransport(app=_load_app())
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            sid = await _atomic_no_htf_id(client)
            r = await client.post(f"/api/strategies/{sid}/walk-forward", json={
                "instId": "BTC-USDT-SWAP", "bar": "1H", "startTs": base,
                "endTs": base + 2400 * 3600, "windowBars": 1000, "stepBars": 200,
                "resetCapital": True})
            assert r.status_code == 200, r.text
            body = r.json()
            assert "runId" in body
            summary = body["summary"]
            for key in ("windowCount", "profitableWindows", "profitableRate", "medianReturn",
                        "avgReturn", "worstReturn", "bestReturn", "avgMaxDrawdown",
                        "stabilityScore", "uncoveredBars", "notes"):
                assert key in summary
            assert len(body["windows"]) == summary["windowCount"]
            assert {"startTs", "endTs", "metrics", "tradeCount"} <= set(body["windows"][0])

            # GET 历史复看与非 walkforward → 404。
            again = await client.get(f"/api/runs/{body['runId']}/walk-forward")
            assert again.status_code == 200
            assert again.json()["summary"]["windowCount"] == summary["windowCount"]
    finally:
        await dispose_engine()


async def test_walk_forward_api_validation_errors(patch_okx):
    from app.db.session import dispose_engine, init_db
    await init_db()
    base = 1_700_000_000
    transport = ASGITransport(app=_load_app())
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            sid = await _atomic_no_htf_id(client)
            common = {"instId": "BTC-USDT-SWAP", "bar": "1H", "startTs": base,
                      "endTs": base + 2400 * 3600}
            # startTs >= endTs
            r1 = await client.post(f"/api/strategies/{sid}/walk-forward", json={
                **common, "startTs": base + 3000 * 3600, "windowBars": 1000, "stepBars": 200,
                "resetCapital": True})
            assert r1.status_code == 422
            # stepBars > windowBars
            r2 = await client.post(f"/api/strategies/{sid}/walk-forward", json={
                **common, "windowBars": 200, "stepBars": 500, "resetCapital": True})
            assert r2.status_code == 422
            # windowBars < 100（下界，v2·P1-3）
            r3 = await client.post(f"/api/strategies/{sid}/walk-forward", json={
                **common, "windowBars": 50, "stepBars": 20, "resetCapital": True})
            assert r3.status_code == 422
    finally:
        await dispose_engine()


async def test_walk_forward_api_too_many_windows(monkeypatch, patch_okx):
    from app.config import get_settings
    from app.db.session import dispose_engine, init_db
    await init_db()
    monkeypatch.setattr(get_settings(), "walk_forward_max_windows", 2)
    base = 1_700_000_000
    transport = ASGITransport(app=_load_app())
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            sid = await _atomic_no_htf_id(client)
            r = await client.post(f"/api/strategies/{sid}/walk-forward", json={
                "instId": "BTC-USDT-SWAP", "bar": "1H", "startTs": base,
                "endTs": base + 2400 * 3600, "windowBars": 200, "stepBars": 100,
                "resetCapital": True})
            assert r.status_code == 400
            assert r.json()["error"]["code"] == "WF_TOO_MANY_WINDOWS"
    finally:
        await dispose_engine()


async def test_walk_forward_api_rejects_combo(patch_okx):
    from app.db.session import dispose_engine, init_db
    await init_db()
    base = 1_700_000_000
    transport = ASGITransport(app=_load_app())
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            combos = [s for s in (await client.get("/api/strategies")).json()
                      if s.get("kind") == "combo"]
            r = await client.post(f"/api/strategies/{combos[0]['id']}/walk-forward", json={
                "instId": "BTC-USDT-SWAP", "bar": "1H", "startTs": base,
                "endTs": base + 1400 * 3600, "windowBars": 500, "stepBars": 250,
                "resetCapital": True})
            assert r.status_code == 400
            assert r.json()["error"]["code"] == "WF_COMBO_UNSUPPORTED"
    finally:
        await dispose_engine()


async def test_walk_forward_history_404_for_non_walkforward_run(patch_okx):
    from app.db.session import dispose_engine, init_db
    await init_db()
    base = 1_700_000_000
    transport = ASGITransport(app=_load_app())
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            sid = await _atomic_no_htf_id(client)
            bt = await client.post(f"/api/strategies/{sid}/backtest", json={
                "instId": "BTC-USDT-SWAP", "bar": "1H", "startTs": base,
                "endTs": base + 100 * 3600})
            assert bt.status_code == 200, bt.text
            r = await client.get(f"/api/runs/{bt.json()['runId']}/walk-forward")
            assert r.status_code == 404
            assert r.json()["error"]["code"] == "NOT_WALK_FORWARD"
    finally:
        await dispose_engine()


async def _atomic_no_htf_id(client: AsyncClient) -> int:
    strategies = (await client.get("/api/strategies")).json()
    for s in strategies:
        if s.get("unavailable") or s.get("kind") == "combo":
            continue
        cls = get_strategy_class(s["templateKey"])
        if cls is None:
            continue
        if not cls(dict(cls.meta.default_params)).timeframes():
            return s["id"]
    raise AssertionError("no atomic strategy without HTF found")
