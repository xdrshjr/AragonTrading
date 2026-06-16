"""实时模拟预热回放 / 逐笔盯市 / 订阅快照单测（B1/B2/B3，无网络）。

覆盖评审放行条件：P1-1 增量回放无重复 ts、P1-2 primingBars 夹取、seeded 标注、
intrabar transient equity 节流且不落库、snapshot 一致性、去重衔接、本金传递。
"""
from __future__ import annotations

import json

import pytest

from app.okx.types import Candle
from app.strategies import STRATEGY_REGISTRY
from tests.conftest import make_synthetic_candles

_INST = "BTC-USDT-SWAP"
_STRAT = "long_shadow_reversal"


def _downtrend_then_hammer(n: int = 40, start_ts: int = 1_700_000_000,
                           step: int = 3600) -> list[dict]:
    """确定性下跌 + 末段长下影锤子（收阳），保证 long_shadow_reversal 在预热段开多。"""
    rows: list[dict] = []
    price = 200.0
    hammer_at = n - 6
    for i in range(n):
        o = price
        if i == hammer_at:                       # 长下影、小实体、收阳的锤子线
            c = o + 0.1
            low = min(o, c) - 18.0
            high = max(o, c) + 0.3
        else:                                    # 普通下跌阴线
            c = o - 2.0
            high = max(o, c) + 0.3
            low = min(o, c) - 0.3
        rows.append({"ts": start_ts + i * step, "o": round(o, 2), "h": round(high, 2),
                     "l": round(low, 2), "c": round(c, 2), "vol": 1000.0})
        price = c
    return rows


class _FakeRest:
    """记录 limit 并返回预置历史的假 REST 客户端。"""

    def __init__(self, rows: list[dict]):
        self._rows = rows
        self.last_limit: int | None = None

    async def get_candles(self, inst_id, bar, limit=300, before=None, after=None):
        self.last_limit = limit
        return [Candle(ts=r["ts"], o=r["o"], h=r["h"], l=r["l"], c=r["c"], vol=r["vol"])
                for r in self._rows[:limit]]


class _FakeWS:
    """no-op WS：live priming 测试不触网。"""

    def __init__(self, *args, **kwargs):
        pass

    def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass


def _patch(monkeypatch, rows: list[dict]) -> _FakeRest:
    rest = _FakeRest(rows)
    monkeypatch.setattr("app.paper.session.get_rest_client", lambda: rest)
    monkeypatch.setattr("app.paper.session.OKXWSClient", _FakeWS)
    return rest


async def _make_session(*, priming: bool = True, priming_bars: int = 300,
                        initial_capital: float = 10000.0):
    """建一行 paper run 并构造 live PaperSession（不 start）。返回 (session, run_id)。"""
    from app.db import repositories as repo
    from app.db.session import get_sessionmaker
    from app.paper.session import PaperSession
    sm = get_sessionmaker()
    async with sm() as s:
        strategies = await repo.list_strategies(s)
    sid = next(x["id"] for x in strategies if x["templateKey"] == _STRAT)
    async with sm() as s:
        run = await repo.create_run(s, strategy_id=sid, inst_id=_INST, bar="1H",
                                    mode="paper", config={}, status="running")
        run_id = run.id
    strat = STRATEGY_REGISTRY[_STRAT]()
    sess = PaperSession(f"sid-{run_id}", run_id, strat, _INST, "1H", initial_capital,
                        source="live", priming=priming, priming_bars=priming_bars)
    return sess, run_id


async def test_priming_seeds_trades_and_equity(monkeypatch):
    """预热回放即建仓：≥1 笔 seeded 成交、≥1 个净值点（§8.1 建仓）。"""
    from app.db.session import dispose_engine, init_db
    await init_db()
    try:
        _patch(monkeypatch, _downtrend_then_hammer(40))
        sess, _ = await _make_session()
        await sess.start()
        assert sess._seeded_trade_count >= 1
        assert any(t.side in ("long", "short") for t in sess.broker.trades)
        assert len(sess.broker.equity_points) >= 1
    finally:
        await dispose_engine()


async def test_incremental_replay_has_no_duplicate_ts(monkeypatch):
    """增量回放正确性（覆盖 P1-1）：窗口根数 / 末根 ts / bar_index / 无重复 ts。"""
    from app.db.session import dispose_engine, init_db
    await init_db()
    try:
        rows = _downtrend_then_hammer(40)
        _patch(monkeypatch, rows)
        sess, _ = await _make_session(priming_bars=300)
        await sess.start()
        bars = len(rows)                       # 历史仅 40 根 < primingBars → 全取
        assert len(sess._candles) == bars
        assert sess._last_confirmed_ts == rows[-1]["ts"]
        assert sess._bar_index == bars - 1
        eq_ts = [e.ts for e in sess.broker.equity_points]
        assert eq_ts == sorted(set(eq_ts))     # 严格升序且无重复（未发生整窗预填后重复追加）
        assert len(sess.broker.equity_points) == bars
    finally:
        await dispose_engine()


async def test_priming_bars_upper_clamp(monkeypatch):
    """primingBars 上夹取到 _WINDOW_MAX=600（覆盖 P1-2）。"""
    from app.db.session import dispose_engine, init_db
    await init_db()
    try:
        rest = _patch(monkeypatch, make_synthetic_candles(700))
        sess, _ = await _make_session(priming_bars=1000)
        await sess.start()
        assert rest.last_limit == 600
        assert len(sess._candles) == 600
    finally:
        await dispose_engine()


async def test_priming_bars_lower_clamp(monkeypatch):
    """primingBars 下夹取到 warmup+5（覆盖 P1-2）。"""
    from app.db.session import dispose_engine, init_db
    await init_db()
    try:
        rest = _patch(monkeypatch, make_synthetic_candles(700))
        warmup = STRATEGY_REGISTRY[_STRAT]().warmup()
        sess, _ = await _make_session(priming_bars=5)
        await sess.start()
        assert rest.last_limit == warmup + 5
    finally:
        await dispose_engine()


async def test_snapshot_equity_nonempty_without_trades(monkeypatch):
    """无信号也建净值：snapshot().equity ≥1（§8.1）。"""
    from app.db.session import dispose_engine, init_db
    await init_db()
    try:
        _patch(monkeypatch, make_synthetic_candles(50))
        sess, _ = await _make_session(priming_bars=50)
        await sess.start()
        snap = sess.snapshot()
        assert len(snap["equity"]) >= 1
        assert snap["status"]["warmedBars"] == len(sess._candles)
    finally:
        await dispose_engine()


async def test_intrabar_pushes_one_throttled_transient_equity(monkeypatch):
    """intrabar 盯市：节流后仅一次 transient equity 推送，且不落库（§8.1 / R3）。"""
    from app.db.session import dispose_engine, init_db
    await init_db()
    try:
        rows = make_synthetic_candles(50)
        _patch(monkeypatch, rows)
        sess, _ = await _make_session(priming_bars=50)
        await sess.start()
        q = sess.subscribe()
        eq_before = len(sess.broker.equity_points)
        last = rows[-1]
        forming = Candle(ts=last["ts"] + 3600, o=last["c"], h=last["c"] + 1.0,
                         l=last["c"] - 1.0, c=last["c"] + 0.5, vol=1.0, confirm=False)
        await sess.feed_candle(forming)
        await sess.feed_candle(forming)        # 同价立即再来 → 被节流
        events = []
        while not q.empty():
            events.append(q.get_nowait())
        transient = [e for e in events
                     if e["type"] == "equity" and e["data"].get("transient")]
        assert len(transient) == 1
        assert len(sess.broker.equity_points) == eq_before   # transient 不落库
    finally:
        await dispose_engine()


async def test_snapshot_matches_broker_state(monkeypatch):
    """snapshot 完整且与 broker 一致；seeded 段成交全部标 seeded（§8.1 / P2-5）。"""
    from app.db.session import dispose_engine, init_db
    await init_db()
    try:
        _patch(monkeypatch, _downtrend_then_hammer(40))
        sess, _ = await _make_session()
        await sess.start()
        snap = sess.snapshot()
        assert len(snap["trades"]) == len(sess.broker.trades)
        assert all(t["seeded"] for t in snap["trades"][:sess._seeded_trade_count])
        assert len(snap["equity"]) == min(len(sess.broker.equity_points), 1000)
        assert snap["status"]["seededTrades"] == sess._seeded_trade_count
        if sess.broker.pos.side == "flat":
            assert snap["position"] is None
        else:
            assert snap["position"]["side"] == sess.broker.pos.side
    finally:
        await dispose_engine()


async def test_live_confirmed_dedups_priming_last_bar(monkeypatch):
    """去重衔接（R6）：与预热末根同 ts 的确认根不重复处理。"""
    from app.db.session import dispose_engine, init_db
    await init_db()
    try:
        rows = _downtrend_then_hammer(40)
        _patch(monkeypatch, rows)
        sess, _ = await _make_session()
        await sess.start()
        bars, eq_before = len(sess._candles), len(sess.broker.equity_points)
        last = rows[-1]
        await sess.feed_candle(Candle(ts=last["ts"], o=last["o"], h=last["h"],
                                      l=last["l"], c=last["c"], vol=last["vol"], confirm=True))
        assert len(sess._candles) == bars
        assert len(sess.broker.equity_points) == eq_before
    finally:
        await dispose_engine()


async def test_manager_start_records_priming_config(monkeypatch):
    """本金传递 + run.config 记录 priming/primingBars/seededTrades（§8.1 / §7.3）。"""
    from app.db import repositories as repo
    from app.db.session import dispose_engine, get_sessionmaker, init_db
    from app.paper.manager import PaperManager
    await init_db()
    try:
        _patch(monkeypatch, make_synthetic_candles(120))
        sm = get_sessionmaker()
        async with sm() as s:
            strategies = await repo.list_strategies(s)
        sid = next(x["id"] for x in strategies if x["templateKey"] == _STRAT)
        strat = STRATEGY_REGISTRY[_STRAT]()
        mgr = PaperManager()
        sess = await mgr.start(strat, sid, _INST, "1H", 50000.0,
                               source="live", priming=True, priming_bars=120)
        assert sess.broker.initial_capital == 50000.0
        async with sm() as s:
            run = await repo.get_run(s, sess.run_id)
            cfg = json.loads(run.config_json or "{}")
        assert cfg["initialCapital"] == 50000.0
        assert cfg["priming"] is True
        assert cfg["primingBars"] == 120
        assert cfg["seededTrades"] == sess._seeded_trade_count
        await sess.stop()
    finally:
        await dispose_engine()
