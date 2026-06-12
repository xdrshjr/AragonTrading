"""历史回放模拟单测 (F5)：replay 用真实历史驱动撮合、自动停止、stop 幂等（无网络）。"""
from __future__ import annotations

import asyncio

import pytest

from app.okx.types import Candle
from app.strategies import STRATEGY_REGISTRY
from tests.conftest import make_synthetic_candles


class _FakeRest:
    async def get_candles(self, inst_id, bar, limit=300, before=None, after=None):
        rows = make_synthetic_candles(min(limit, 300))
        return [Candle(ts=r["ts"], o=r["o"], h=r["h"], l=r["l"], c=r["c"], vol=r["vol"]) for r in rows]


@pytest.fixture
def patch_session_okx(monkeypatch):
    monkeypatch.setattr("app.paper.session.get_rest_client", lambda: _FakeRest())


async def _make_paper_run(mode: str = "paper") -> tuple[int, int]:
    """建一行 paper run，返回 (strategy_id, run_id)。"""
    from app.db import repositories as repo
    from app.db.session import get_sessionmaker
    sm = get_sessionmaker()
    async with sm() as s:
        strategies = await repo.list_strategies(s)
    sid = next(x["id"] for x in strategies if x["templateKey"] == "long_shadow_reversal")
    async with sm() as s:
        run = await repo.create_run(s, strategy_id=sid, inst_id="BTC-USDT-SWAP", bar="1H",
                                    mode=mode, config={}, status="running")
        return sid, run.id


async def test_replay_drives_broker_and_auto_stops(patch_session_okx):
    from app.db.session import dispose_engine, init_db
    from app.paper.session import PaperSession
    await init_db()
    try:
        sid, run_id = await _make_paper_run()
        strat = STRATEGY_REGISTRY["long_shadow_reversal"]()
        sess = PaperSession("replay-sid", run_id, strat, "BTC-USDT-SWAP", "1H", 10000.0,
                            source="replay", speed=64.0, replay_bars=120)
        q = sess.subscribe()
        await sess.start()

        assert sess._replay_task is not None
        await asyncio.wait_for(sess._replay_task, timeout=15)

        # 回放结束后会话自动停止
        assert sess.state == "stopped"

        types: list[str] = []
        while not q.empty():
            types.append(q.get_nowait()["type"])
        # 至少产生 equity 事件 + running/stopped 两个状态
        assert "equity" in types
        assert types.count("status") >= 2
        # 撮合确实推进了：权益点数量与回放根数一致
        assert len(sess.broker.equity_points) == 120
    finally:
        await dispose_engine()


async def test_stop_is_idempotent(patch_session_okx):
    """回放自动 stop + 手动 stop 重复进入不重复结算（C3）。"""
    from app.db.session import dispose_engine, init_db
    from app.paper.session import PaperSession
    await init_db()
    try:
        sid, run_id = await _make_paper_run()
        strat = STRATEGY_REGISTRY["long_shadow_reversal"]()
        sess = PaperSession("replay-idem", run_id, strat, "BTC-USDT-SWAP", "1H", 10000.0,
                            source="replay", speed=64.0, replay_bars=60)
        await sess.start()
        await asyncio.wait_for(sess._replay_task, timeout=15)
        assert sess.state == "stopped"

        trade_count = len(sess.broker.trades)
        equity_count = len(sess.broker.equity_points)
        # 再次手动 stop：幂等，不重复结算 / 不报错
        await sess.stop()
        await sess.stop()
        assert len(sess.broker.trades) == trade_count
        assert len(sess.broker.equity_points) == equity_count
    finally:
        await dispose_engine()


async def test_live_session_has_no_replay_task():
    """live 路径不受回放改动影响：默认 source、无回放任务。"""
    from app.db.session import dispose_engine, init_db
    from app.paper.session import PaperSession
    await init_db()
    try:
        sid, run_id = await _make_paper_run()
        strat = STRATEGY_REGISTRY["long_shadow_reversal"]()
        sess = PaperSession("live-sid", run_id, strat, "BTC-USDT-SWAP", "1H", 10000.0)
        assert sess.source == "live"
        assert sess._replay_task is None
    finally:
        await dispose_engine()
