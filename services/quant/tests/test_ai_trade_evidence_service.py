"""实证准入的快照 / 接管 / 回填 / 端点（ai-trade-evidence-gated-capital §7.4，S-1..S-11）。

临时库上跑（与其余用例共用同一个测试库）：标的名一律用本文件独有的 `EVT*`，每条用例先清掉自己的行。
"""
from __future__ import annotations

import time
import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import create_async_engine

from app.ai_auto.evidence_gate import rule_from_settings
from app.ai_auto.evidence_runtime import EvidenceRuntime
from app.ai_auto.evidence_service import (
    EvidenceFetchError,
    backfill,
    load_snapshots,
    persist_rows,
    row_values,
)
from app.ai_auto.shadow_ledger import open_shadow
from app.config import Settings
from app.db import ai_evidence_repositories as erepo
from app.db.ai_evidence_models import AiTradeShadowIntent
from app.db.models import Base, StrategyRun, Trade
from app.db.session import dispose_engine, get_sessionmaker, init_db

BAR = "1H"
BAR_S = 3600
NOW = float(time.time() // BAR_S * BAR_S)
RULE = rule_from_settings(Settings())
VERSION = RULE.ref.version()


async def _clean(*insts: str) -> None:
    async with get_sessionmaker()() as db:
        await db.execute(delete(AiTradeShadowIntent).where(
            AiTradeShadowIntent.inst_id.in_(insts)))
        await db.commit()


def _row(inst, side, *, net=5.0, matured=NOW - 3600, status="matured", version=VERSION,
         opened=None, source="live", run_id=0, bars_seen=0):
    key = uuid.uuid4().hex
    return AiTradeShadowIntent(
        shadow_key=key, source=source, source_ref=key, inst_id=inst, bar=BAR, side=side,
        policy_version=version, run_id=run_id, opened_wall=opened or matured - 7200,
        opened_bar_ts=int((opened or matured - 7200) // BAR_S * BAR_S), entry_px=100.0,
        cost_bps=10.0, atr_bps=60.0, sl_bps=120.0, tp_bps=240.0, status=status,
        matured_wall=matured if status == "matured" else None,
        net_bps=net if status == "matured" else None, bars_seen=bars_seen)


async def _add(*rows) -> None:
    async with get_sessionmaker()() as db:
        db.add_all(list(rows))
        await db.commit()


async def _snap(insts):
    async with get_sessionmaker()() as db:
        return await load_snapshots(db, inst_ids=list(insts), bar=BAR, rule=RULE, now=NOW)


# ---------------- S-1 / S-2 / S-3 ----------------
async def test_s1_s2_snapshot_filters_version_status_age_and_pools_across_insts():
    await init_db()
    try:
        await _clean("EVTA", "EVTB")
        await _add(*[_row("EVTA", "long", net=float(i), matured=NOW - 3600 * (i + 1))
                     for i in range(40)],
                   _row("EVTA", "long", version="ref-v0:old"),
                   _row("EVTA", "long", matured=NOW - 40 * 86400),
                   _row("EVTA", "short", status="void"),
                   _row("EVTB", "short", net=-7.0))
        snap = (await _snap(["EVTA", "EVTB"]))
    finally:
        await dispose_engine()
    long_a = snap["EVTA"].samples["long"]
    assert len(long_a) == RULE.window and [v for _, v in long_a] == [float(i) for i in
                                                                    range(29, -1, -1)]
    assert snap["EVTA"].samples["short"] == []
    assert snap["EVTA"].pooled["short"] == snap["EVTB"].pooled["short"]
    assert [v for _, v in snap["EVTA"].pooled["short"]] == [-7.0]
    assert snap["EVTA"].error is False


async def test_s3_stale_open_voided_recent_open_taken_over():
    await init_db()
    try:
        await _clean("EVTC")
        old = _row("EVTC", "long", status="open", opened=NOW - 100 * BAR_S)
        fresh = _row("EVTC", "short", status="open", opened=NOW - 3 * BAR_S)
        await _add(old, fresh)
        snap = await _snap(["EVTC"])
        async with get_sessionmaker()() as db:
            status = (await db.execute(select(AiTradeShadowIntent.status).where(
                AiTradeShadowIntent.shadow_key == old.shadow_key))).scalar_one()
    finally:
        await dispose_engine()
    assert status == "void"
    assert [s.shadow_id for s in snap["EVTC"].open_shadows] == [fresh.shadow_key]


# ---------------- S-4：接管从头重算 ----------------
def _bars(start_ts, n, px=100.0):
    return [{"ts": start_ts + k * BAR_S, "o": px, "h": px + 0.05, "l": px - 0.05, "c": px}
            for k in range(n)]


async def test_s4_prime_replays_from_scratch_on_closed_bars_only():
    await init_db()
    try:
        await _clean("EVTD")
        opened = NOW - 5 * BAR_S + 10
        row = _row("EVTD", "long", status="open", opened=opened, bars_seen=999)
        await _add(row)
        snap = (await _snap(["EVTD"]))["EVTD"]
    finally:
        await dispose_engine()
    runtime = EvidenceRuntime(RULE, snap, inst_id="EVTD", bar=BAR, run_id=42, group_id=None,
                              fallback_cost_bps=12.0, now=lambda: NOW + 30)
    open_bar = int(opened // BAR_S * BAR_S)
    window = _bars(open_bar - 3 * BAR_S, 9)      # 末根 ts = NOW + BAR_S - … 未收盘
    runtime.prime(window)
    shadow = runtime._open_for("long")
    assert shadow is not None and shadow.run_id == 42
    assert shadow.bars_seen == 4, "只推进入场后、且已收盘的根；库里的 bars_seen=999 被无视"
    uncovered = EvidenceRuntime(RULE, snap, inst_id="EVTD", bar=BAR, run_id=43, group_id=None,
                                fallback_cost_bps=12.0, now=lambda: NOW + 30)
    uncovered.prime(_bars(open_bar + 3 * BAR_S, 2))
    assert uncovered._open_for("long") is None and uncovered.stats()["voided"] == 1


# ---------------- S-5 / S-6 / S-9 / S-10：回填 ----------------
async def _seed_run(inst: str, legs: list[tuple[int, float, float]]) -> int:
    """legs = [(ts, price, position_after)]：开仓腿 position_after≠0，平仓腿 0 且带 pnl。"""
    async with get_sessionmaker()() as db:
        run = StrategyRun(strategy_id=1, inst_id=inst, bar=BAR, mode="ai-trade",
                          status="done", config_json="{}")
        db.add(run)
        await db.flush()
        for ts, price, after in legs:
            db.add(Trade(run_id=run.id, ts=ts, side="long" if after else "close", price=price,
                         qty=1.0, fee=0.1, pnl=0.0 if after else -0.5, position_after=after,
                         reason="t"))
        await db.commit()
        return int(run.id)


def _fetcher(missing: set[str] = frozenset(), calls=None):
    async def fetch(inst, bar, start, end):
        if calls is not None:
            calls.append((inst, start, end))
        if inst in missing:
            raise RuntimeError("okx down")
        first = start // BAR_S * BAR_S
        return _bars(first, (end - first) // BAR_S + 1)
    return fetch


async def _backfill(insts, **kw):
    async with get_sessionmaker()() as db:
        return await backfill(db, rule=RULE, inst_ids=insts, bar=BAR, days=30,
                              fallback_cost_bps=12.0, now=NOW, **kw)


def _legs(start):
    return [(start, 100.0, 1.0), (start + 2 * BAR_S, 100.0, 0.0),
            (start + 40 * BAR_S, 100.0, 1.0), (start + 42 * BAR_S, 100.0, 0.0)]


async def test_s5_s6_backfill_is_idempotent_and_counts_missing_candles():
    await init_db()
    try:
        await _clean("EVTE", "EVTF")
        start = int(NOW - 10 * 86400) // BAR_S * BAR_S
        await _seed_run("EVTE", _legs(start))
        await _seed_run("EVTF", _legs(start))
        first = await _backfill(["EVTE", "EVTF"], fetch=_fetcher({"EVTF"}))
        # 公共读恢复后再点一次：已插入的计为 duplicate，上次缺 K 线的这次补上。
        second = await _backfill(["EVTE", "EVTF"], fetch=_fetcher())
        third = await _backfill(["EVTE", "EVTF"], fetch=_fetcher())
    finally:
        await dispose_engine()
    assert first["inserted"] == 2 and first["skipped"]["candlesMissing"] == 2
    assert second["inserted"] == 2 and second["skipped"]["duplicate"] == 2
    assert third["inserted"] == 0 and third["skipped"]["duplicate"] == 4
    assert first["truncated"] is False and first["remaining"] == 0


async def test_s6b_all_fetches_failing_raises():
    await init_db()
    try:
        await _clean("EVTG")
        await _seed_run("EVTG", _legs(int(NOW - 5 * 86400) // BAR_S * BAR_S))
        with pytest.raises(EvidenceFetchError):
            await _backfill(["EVTG"], fetch=_fetcher({"EVTG"}))
    finally:
        await dispose_engine()


async def test_s9_runs_with_live_shadows_are_skipped():
    await init_db()
    try:
        await _clean("EVTH")
        run_id = await _seed_run("EVTH", _legs(int(NOW - 5 * 86400) // BAR_S * BAR_S))
        await _add(_row("EVTH", "long", run_id=run_id))
        out = await _backfill(["EVTH"], fetch=_fetcher())
    finally:
        await dispose_engine()
    assert out["inserted"] == 0 and out["skipped"]["liveCovered"] == 2


async def test_s10_deadline_truncates_and_second_call_resumes():
    await init_db()
    try:
        await _clean("EVTI")
        await _seed_run("EVTI", _legs(int(NOW - 5 * 86400) // BAR_S * BAR_S))
        cut = await _backfill(["EVTI"], fetch=_fetcher(), deadline_s=0.0)
        rest = await _backfill(["EVTI"], fetch=_fetcher())
    finally:
        await dispose_engine()
    assert cut["truncated"] is True and cut["remaining"] == 2 and cut["inserted"] == 0
    assert rest["inserted"] == 2 and rest["truncated"] is False


# ---------------- S-7：端点 ----------------
async def test_s7_evidence_endpoint_on_empty_buckets_and_after_persist():
    await init_db()
    try:
        await _clean("EVTJ")
        from app.main import app

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            empty = (await client.get("/api/ai-trade/evidence",
                                      params={"instIds": "EVTJ", "bar": BAR})).json()
            shadow = open_shadow(inst_id="EVTJ", bar=BAR, side="long", now_wall=NOW, px=100.0,
                                 atr_px=0.6, cost_bps=10.0, policy=RULE.ref, run_id=1,
                                 round_no=1, fallback_cost_bps=12.0)
            await persist_rows([shadow])
            full = (await client.get("/api/ai-trade/evidence",
                                     params={"instIds": "EVTJ", "bar": BAR,
                                             "policy": "observe"})).json()
    finally:
        await dispose_engine()
    assert empty["buckets"] == [] and empty["refPolicy"] == VERSION
    bucket = full["buckets"][0]
    assert bucket["openN"] == 1 and bucket["n"] == 0 and bucket["gating"] == "live"
    assert full["recentShadowRatePerDay"]["EVTJ"]["long"] == pytest.approx(1 / 7, abs=0.01)
    assert row_values(shadow)["source_ref"] == shadow.shadow_id


# ---------------- S-8：老库升级 ----------------
async def test_s8_create_all_adds_the_table_to_an_existing_db(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{(tmp_path / 'old.db').as_posix()}")
    try:
        async with engine.begin() as conn:
            await conn.execute(text("create table strategies (id integer primary key)"))
            await conn.run_sync(Base.metadata.create_all)
            names = (await conn.execute(text(
                "select name from sqlite_master where type='table'"))).scalars().all()
    finally:
        await engine.dispose()
    assert "ai_trade_shadow_intents" in names


# ---------------- S-11：读库失败不阻断启动 ----------------
class _BrokenSession:
    def __init__(self, exc):
        self.exc = exc
        self.rolled_back = False

    async def execute(self, *_a, **_k):
        raise self.exc

    async def rollback(self):
        self.rolled_back = True


async def test_s11_snapshot_db_error_yields_empty_error_snapshot():
    broken = _BrokenSession(OperationalError("select", {}, Exception("locked")))
    out = await load_snapshots(broken, inst_ids=["EVTK"], bar=BAR, rule=RULE, now=NOW)
    assert out["EVTK"].error is True and out["EVTK"].samples == {}
    assert broken.rolled_back is True
    with pytest.raises(ZeroDivisionError):
        await load_snapshots(_BrokenSession(ZeroDivisionError()), inst_ids=["EVTK"], bar=BAR,
                             rule=RULE, now=NOW)
    assert await load_snapshots(broken, inst_ids=["EVTK"], bar=BAR, rule=None, now=NOW) is None


async def test_repository_upsert_updates_in_place():
    await init_db()
    try:
        await _clean("EVTL")
        shadow = open_shadow(inst_id="EVTL", bar=BAR, side="short", now_wall=NOW, px=100.0,
                             atr_px=0.6, cost_bps=10.0, policy=RULE.ref, run_id=1, round_no=1,
                             fallback_cost_bps=12.0)
        await persist_rows([shadow])
        await persist_rows([shadow.__class__(**{**shadow.__dict__, "repeats": 3})])
        async with get_sessionmaker()() as db:
            rows = (await db.execute(select(AiTradeShadowIntent).where(
                AiTradeShadowIntent.inst_id == "EVTL"))).scalars().all()
            live = await erepo.runs_with_live_shadows(db, [1])
    finally:
        await dispose_engine()
    assert len(rows) == 1 and rows[0].repeats == 3 and 1 in live
