"""冷续跑七步流水线（ai-trade-pause-resume §3.5 / G-8 · G-10 · G-14 · G-15 · G-26 ②③）。

「冷」= 进程重启过，内存里什么都没有。本文件最要紧的两条：

- **S3 fail-closed**：读不到「我现在到底有没有仓、有多大」就**拒绝续跑**。本仓库的旁路统计一律
  fail-open，但续跑不是旁路统计，它是一次开仓授权——闭眼下单比把用户挡在门外坏得多。
- **不新建 run**（I-3）：续跑复用同一批 `run_id`，否则一次交易在历史里被劈成两条，`totalReturn`
  的分母就错了。
"""
from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, func, select

from app.ai_auto.manager import get_ai_trade_manager, paused_ref_from_state_row
from app.ai_auto.resume import rebuild_continuity
from app.config import Settings, get_settings
from app.db import ai_trade_state_repositories as state_repo
from app.db import repositories as repo
from app.db.models import AppSetting, StrategyRun, Trade
from app.db.session import dispose_engine, get_sessionmaker, init_db
from app.live_auto.manager import get_live_auto_manager

_START_TS = 1_700_000_000
_BAR_SECONDS = 3600            # 1H
_CONFIG = {"instrumentSupported": True, "availUsdt": 1000.0, "posMode": "long_short_mode",
           "instMeta": {"ctVal": 0.01, "lotSz": 0.01, "minSz": 0.01, "tickSz": 0.1,
                        "maxLever": 125.0}, "lastPx": 30000.0}


class _FakeWindow:
    def __init__(self, index: int = 500):
        self._index = index

    def cur_index(self) -> int:
        return self._index


class _FakeDecider:
    def __init__(self):
        self.seeded: dict | None = None

    def seed_from_snapshot(self, *, summary, last_round):
        self.seeded = {"summary": summary, "lastRound": last_round}


class _FakeSession:
    """会话桩：**足够真实**地承载 S6 的接管（executor / decider / 窗口 / 序号都在）。"""

    fail_start = False
    started: list[str] = []

    def __init__(self, session_id, run_id, specs, inst_id, bar, executor, decider, **kw):
        self.session_id = session_id
        self.run_id = run_id
        self.executor = executor
        self.decider = _FakeDecider()
        self.inst_id = inst_id
        self.bar = bar
        self.state = "running"
        self.stop_reason = None
        self.pause_reason = None
        self.paused_ts = 0
        self.started_ts = _START_TS
        self.rounds_completed = 0
        self.group_id = ""
        self.on_stopped = None
        self.strategy_ids = [s.strategy_id for s in specs]
        self.strategy_names = [s.strategy_name for s in specs]
        self.context_refresh_rounds = kw.get("context_refresh_rounds", 10)
        self.take_profit_usd = kw.get("take_profit_usd")
        self.stop_loss_usd = kw.get("stop_loss_usd")
        self._window = _FakeWindow()
        self._position_seq = 0
        self._known_cl_ord_ids: list[str] = []
        self._last_status_side = "flat"

    async def start(self):
        if _FakeSession.fail_start:
            raise RuntimeError("start failed")
        _FakeSession.started.append(self.inst_id)

    async def stop(self, reason="user_stop", *, flatten=True):
        self.state = "stopped"
        self.stop_reason = reason
        if self.on_stopped is not None:
            await self.on_stopped(self)
        return {"ok": True, "closedSz": 0}

    @property
    def is_active(self):
        return self.state in ("running", "paused")

    async def pause(self, reason="user", *, flatten=False):
        self.state = "paused"
        self.pause_reason = reason
        return {"instId": self.inst_id, "sessionId": self.session_id, "runId": self.run_id,
                "state": "paused", "pauseReason": reason, "pausedTs": _START_TS,
                "position": {"side": "long", "size": 0.6, "avgEntry": 30000.0,
                             "posId": "POS-A", "openCTime": _START_TS - 4 * _BAR_SECONDS,
                             "openWallTs": _START_TS - 4 * _BAR_SECONDS, "positionSeq": 0},
                "positionUnknown": False, "restingCancelled": None,
                "backstop": {"basis": "exchange", "covered": True}, "flattened": None}

    async def resume(self):
        self.state = "running"
        return {"ok": True, "state": self.state}

    def state_member_row(self):
        return {"instId": self.inst_id, "runId": self.run_id, "sessionId": self.session_id,
                "position": None, "positionUnknown": False, "restingClOrdId": None,
                "knownClOrdIds": []}

    def discipline_wire(self):
        return {"rows": [], "hint": None}

    def acceptance_bundle(self):
        return {"acceptance": None, "exits": None, "funding": None, "acceptanceInput": None}

    def info(self):
        return {"sessionId": self.session_id, "runId": self.run_id, "state": self.state,
                "pauseReason": self.pause_reason, "instId": self.inst_id, "bar": self.bar,
                "strategyIds": self.strategy_ids, "strategyNames": self.strategy_names,
                "budgetUsd": self.executor.budget_usd, "leverage": self.executor.leverage,
                "takeProfitUsd": self.take_profit_usd, "stopLossUsd": self.stop_loss_usd,
                "contextRefreshRounds": self.context_refresh_rounds, "roundsCompleted": 0,
                "roundsSinceSummary": 0, "positionSide": self.executor.projected,
                "sessionPnlUsd": 0.0, "startedTs": self.started_ts, "lastDecision": None,
                "contextSummary": None, "degraded": False}


def _load_app():
    from app.main import app
    return app


async def _fresh() -> int:
    await init_db()
    manager = get_ai_trade_manager()
    manager._group = None
    manager._paused_ref = None
    manager._hydration_failed = False
    get_live_auto_manager()._group = None
    _FakeSession.fail_start = False
    _FakeSession.started = []
    sm = get_sessionmaker()
    async with sm() as session:
        for row in await repo.list_okx_accounts(session):
            await session.delete(row)
        await session.execute(delete(AppSetting))
        await session.commit()
        from app.db.models import Strategy
        return (await session.execute(
            select(Strategy.id).order_by(Strategy.id.asc()).limit(1))).scalar_one()


def _override(app) -> None:
    app.dependency_overrides[get_settings] = lambda: Settings(
        live_trading_enabled=True, ai_trade_enabled=True)


async def _client(app) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _patch(monkeypatch, *, positions=None, pending=None, positions_error=False) -> None:
    async def fake_config(*_a, **_k):
        return dict(_CONFIG)

    async def fake_positions(*_a, **_k):
        if positions_error:
            raise RuntimeError("okx unreachable")
        return list(positions or [])

    async def fake_open_orders(*_a, **_k):
        return list(pending or [])

    async def fake_resolve(*_a, **_k):
        return object()

    async def no_fee_rate(*_a, **_k):
        return None

    monkeypatch.setattr("app.ai_auto.manager.fetch_trading_config", fake_config)
    monkeypatch.setattr("app.ai_auto.manager.fetch_live_positions", fake_positions)
    monkeypatch.setattr("app.ai_auto.manager.fetch_live_open_orders", fake_open_orders)
    monkeypatch.setattr("app.ai_auto.manager.fetch_trade_fee", no_fee_rate)
    monkeypatch.setattr("app.ai_auto.manager.resolve_active_provider", fake_resolve)
    monkeypatch.setattr("app.ai_auto.resume.resolve_active_provider", fake_resolve,
                        raising=False)
    monkeypatch.setattr("app.ai_auto.manager.make_llm_client_from_config", lambda cfg: object())
    monkeypatch.setattr("app.ai_auto.manager.AiTradeSession", _FakeSession)

    async def no_position_history(*_a, **_k):
        raise RuntimeError("history unavailable")   # 默认：回读不可用 ⇒ 走诚实缺口那条路

    monkeypatch.setattr("app.ai_auto.resume.fetch_position_history", no_position_history)


async def _seed_account(client) -> int:
    created = await client.post("/api/accounts", json={
        "label": "Dev-Fake", "apiKey": "ak-fake-0001",
        "secretKey": "sk-fake-secret-0001", "passphrase": "pp-fake-123456"})
    assert created.status_code == 201, created.text
    return created.json()["id"]


def _start_body(strategy_id: int) -> dict:
    return {"members": [{"instId": "BTC-USDT-SWAP", "allocationPct": 100}], "bar": "1H",
            "strategyIds": [strategy_id], "contextRefreshRounds": 10, "stopLossPct": 0.01,
            "takeProfitPct": 0.03}


async def _start_and_pause(client, strategy_id: int) -> str:
    await _seed_account(client)
    started = await client.post("/api/ai-trade/start", json=_start_body(strategy_id))
    assert started.status_code == 200, started.text
    group_id = started.json()["groupId"]
    paused = await client.post(f"/api/ai-trade/{group_id}/pause", json={})
    assert paused.status_code == 200, paused.text
    return group_id


async def _go_cold(group_id: str) -> None:
    """模拟进程重启：清内存 + 走 lifespan 的水合装上冷占位（不启动任何交易）。"""
    manager = get_ai_trade_manager()
    manager._group = None
    async with get_sessionmaker()() as s:
        row = await state_repo.load_group_state(s, group_id)
    manager.adopt_paused_ref(paused_ref_from_state_row(row))


_ACTUAL = {"instId": "BTC-USDT-SWAP", "posSide": "long", "pos": 0.6, "avgPx": 30500.0,
           "posId": "POS-A", "cTimeS": _START_TS - 4 * _BAR_SECONDS, "upl": 3.0}


# ---------------- 主链路：冷续跑复用同一 run ----------------
async def test_cold_resume_reuses_the_same_run_and_adopts_exchange_truth(monkeypatch):
    """G-8 + S6：续跑前后 `strategy_runs` 条数不变；持仓取**交易所真值**而非快照。"""
    strategy_id = await _fresh()
    app = _load_app()
    _override(app)
    _patch(monkeypatch)
    try:
        async with await _client(app) as client:
            group_id = await _start_and_pause(client, strategy_id)
            await _go_cold(group_id)

            sm = get_sessionmaker()
            async with sm() as s:
                before = (await s.execute(select(func.count()).select_from(StrategyRun)
                                          .where(StrategyRun.group_id == group_id))).scalar()

            _patch(monkeypatch, positions=[dict(_ACTUAL)])
            status = (await client.get("/api/ai-trade/status")).json()
            assert status["group"] is None and status["resumable"]["groupId"] == group_id

            preview = await client.get(f"/api/ai-trade/{group_id}/resume-preview")
            assert preview.status_code == 200, preview.text
            assert preview.json()["verdict"] == "ready"
            assert preview.json()["members"][0]["verdict"] == "matched"
            assert preview.json()["contract"]["stopLossPct"] == 0.01   # D-2：契约来自 start_wire

            resumed = await client.post(f"/api/ai-trade/{group_id}/resume",
                                        json={"acknowledge": True})
            assert resumed.status_code == 200, resumed.text

            async with sm() as s:
                after = (await s.execute(select(func.count()).select_from(StrategyRun)
                                         .where(StrategyRun.group_id == group_id))).scalar()
                run = (await s.execute(select(StrategyRun)
                                       .where(StrategyRun.group_id == group_id))).scalars().first()
                row = await state_repo.load_group_state(s, group_id)
        assert after == before                      # I-3：绝不新建 run
        assert run.status == "running"
        assert row["status"] == "running" and row["resumedCount"] == 1

        manager = get_ai_trade_manager()
        assert manager._paused_ref is None          # 三处清空点之一
        member = manager._group.members[0]
        assert member.session.executor.projected == "long"
        assert member.session.executor.entry_qty_sz == 0.6
        assert member.session.executor.entry_price == 30500.0   # 交易所真值，不是快照的 30000
        assert member.session.started_ts == manager._group.created_ts
    finally:
        app.dependency_overrides.clear()
        await dispose_engine()


# ---------------- G-14：S3 fail-closed ----------------
async def test_resume_is_refused_when_the_exchange_cannot_be_read(monkeypatch):
    """G-14：取数失败 ⇒ 409 `AI_TRADE_RESUME_UNVERIFIABLE`，且**没有任何会话被创建**。"""
    strategy_id = await _fresh()
    app = _load_app()
    _override(app)
    _patch(monkeypatch)
    try:
        async with await _client(app) as client:
            group_id = await _start_and_pause(client, strategy_id)
            await _go_cold(group_id)
            _FakeSession.started = []
            _patch(monkeypatch, positions_error=True)

            r = await client.post(f"/api/ai-trade/{group_id}/resume", json={"acknowledge": True})
            assert r.status_code == 409, r.text
            assert r.json()["error"]["code"] == "AI_TRADE_RESUME_UNVERIFIABLE"
            assert _FakeSession.started == []
            assert get_ai_trade_manager()._group is None
            assert get_ai_trade_manager()._paused_ref is not None   # 契约仍在，可再试
    finally:
        app.dependency_overrides.clear()
        await dispose_engine()


# ---------------- G-26 ②：身份对不上即拒绝接管 ----------------
async def test_resume_refuses_a_foreign_position_with_the_same_side_and_size(monkeypatch):
    """G-26 ②（[P0-1]）：同向、同张数、但 `posId` 不同且 `cTime` 晚于暂停时刻 ⇒ 409 + 零会话创建。"""
    strategy_id = await _fresh()
    app = _load_app()
    _override(app)
    _patch(monkeypatch)
    try:
        async with await _client(app) as client:
            group_id = await _start_and_pause(client, strategy_id)
            await _go_cold(group_id)
            _FakeSession.started = []
            foreign = {**_ACTUAL, "posId": "POS-B", "cTimeS": _START_TS + 600}
            _patch(monkeypatch, positions=[foreign])

            preview = (await client.get(f"/api/ai-trade/{group_id}/resume-preview")).json()
            assert preview["verdict"] == "blocked"
            assert preview["members"][0]["verdict"] == "foreign_position"
            assert "从未开过" in preview["members"][0]["message"]

            r = await client.post(f"/api/ai-trade/{group_id}/resume", json={"acknowledge": True})
            assert r.status_code == 409, r.text
            assert r.json()["error"]["code"] == "AI_TRADE_RESUME_BLOCKED"
            assert r.json()["error"]["members"][0]["verdict"] == "foreign_position"
            assert _FakeSession.started == []
    finally:
        app.dependency_overrides.clear()
        await dispose_engine()


async def test_resume_needs_acknowledgement_when_the_position_changed(monkeypatch):
    """`closed_offline` ⇒ `needs_confirm`；未勾选即 400（勾选态必须真的门控提交）。"""
    strategy_id = await _fresh()
    app = _load_app()
    _override(app)
    _patch(monkeypatch)
    try:
        async with await _client(app) as client:
            group_id = await _start_and_pause(client, strategy_id)
            await _go_cold(group_id)
            _patch(monkeypatch, positions=[])          # 离线期间被 OCO 平掉

            preview = (await client.get(f"/api/ai-trade/{group_id}/resume-preview")).json()
            assert preview["verdict"] == "needs_confirm"
            assert preview["members"][0]["verdict"] == "closed_offline"

            blocked = await client.post(f"/api/ai-trade/{group_id}/resume", json={})
            assert blocked.status_code == 400
            assert blocked.json()["error"]["code"] == "AI_TRADE_RESUME_NOT_ACKNOWLEDGED"

            ok = await client.post(f"/api/ai-trade/{group_id}/resume",
                                   json={"acknowledge": True})
            assert ok.status_code == 200, ok.text
            assert get_ai_trade_manager()._group.members[0].session.executor.projected == "flat"
    finally:
        app.dependency_overrides.clear()
        await dispose_engine()


async def test_expired_contract_can_only_be_archived(monkeypatch):
    """G-20：超龄 ⇒ `resume` 409 `AI_TRADE_RESUME_EXPIRED`，`end` 仍可用。"""
    strategy_id = await _fresh()
    app = _load_app()
    _override(app)
    _patch(monkeypatch)
    try:
        async with await _client(app) as client:
            group_id = await _start_and_pause(client, strategy_id)
            async with get_sessionmaker()() as s:    # 把暂停时刻拨回 8 天前
                await state_repo.patch_group_state(s, group_id, {"pausedTs": _START_TS})
            await _go_cold(group_id)
            _patch(monkeypatch, positions=[dict(_ACTUAL)])

            r = await client.post(f"/api/ai-trade/{group_id}/resume", json={"acknowledge": True})
            assert r.status_code == 409 and r.json()["error"]["code"] == "AI_TRADE_RESUME_EXPIRED"

            ended = await client.post(f"/api/ai-trade/{group_id}/end",
                                      json={"flatten": False,
                                            "acknowledgeOrphanPosition": True})
            assert ended.status_code == 200, ended.text
    finally:
        app.dependency_overrides.clear()
        await dispose_engine()


async def test_cold_resume_rolls_the_whole_group_back_when_a_session_fails_to_start(monkeypatch):
    """S7：任一成员起会话失败 ⇒ **整组回滚**，契约留在 paused（可再试）。"""
    strategy_id = await _fresh()
    app = _load_app()
    _override(app)
    _patch(monkeypatch)
    try:
        async with await _client(app) as client:
            group_id = await _start_and_pause(client, strategy_id)
            await _go_cold(group_id)
            _patch(monkeypatch, positions=[dict(_ACTUAL)])
            _FakeSession.fail_start = True

            with pytest.raises(RuntimeError):   # 原始异常不被掩盖（ASGI 直传）
                await client.post(f"/api/ai-trade/{group_id}/resume",
                                  json={"acknowledge": True})
            assert get_ai_trade_manager()._group is None
            async with get_sessionmaker()() as s:
                row = await state_repo.load_group_state(s, group_id)
            assert row["status"] == "paused"
    finally:
        _FakeSession.fail_start = False
        app.dependency_overrides.clear()
        await dispose_engine()


# ---------------- G-10：连续量重建 ----------------
async def _seed_trades(run_id: int, rows: list[dict]) -> None:
    sm = get_sessionmaker()
    async with sm() as s:
        for row in rows:
            s.add(Trade(run_id=run_id, ts=row["ts"], side=row["side"], price=row["price"],
                        qty=row["qty"], fee=row["fee"], pnl=row["pnl"],
                        position_after=row.get("positionAfter", 0.0),
                        reason=row.get("reason", "")))
        await s.commit()


async def test_continuity_rebuild_matches_hand_computed_numbers():
    """G-10：3 笔往返 + 1 笔未平开仓 ⇒ 四项与手算相等；`net_mode` 两态各跑一次。"""
    await init_db()
    try:
        sm = get_sessionmaker()
        async with sm() as s:
            run = await repo.create_run(s, strategy_id=1, inst_id="BTC-USDT-SWAP", bar="1H",
                                        mode="ai-trade", config={}, status="running")
            run_id = run.id
        legs = []
        for i in range(3):                          # 三笔往返：开仓腿 fee 0.1，平仓腿 fee 0.2 / pnl 5
            legs.append({"ts": _START_TS + i * 10, "side": "long", "price": 100.0, "qty": 1.0,
                         "fee": 0.1, "pnl": 0.0})
            legs.append({"ts": _START_TS + i * 10 + 1, "side": "close", "price": 105.0,
                         "qty": 1.0, "fee": 0.2, "pnl": 5.0})
        legs.append({"ts": _START_TS + 100, "side": "long", "price": 100.0, "qty": 1.0,
                     "fee": 0.15, "pnl": 0.0})      # 未平的当前持仓周期
        await _seed_trades(run_id, legs)

        net = await rebuild_continuity(run_id, net_mode=True)
        gross = await rebuild_continuity(run_id, net_mode=False)

        assert len(net["trades"]) == 7
        assert net["positionSeq"] == 3
        assert net["entryFeeUsd"] == 0.15           # 只算当前未平周期的开仓腿
        assert net["realizedPnlUsd"] == 15.0        # net_mode：pnl 已是净额
        assert gross["realizedPnlUsd"] == round(3 * (5.0 - 0.3), 6)   # gross：再扣两条腿费
    finally:
        await dispose_engine()


def test_entry_bar_is_derived_from_the_exchange_open_time():
    """G-15：`entry_bar = cur_index - floor((now - openTs)/barSeconds)`，下界 0；缺失回落 cur_index。"""
    import time

    from app.ai_auto.resume import _Assessment, _adopt_entry_bars

    class _Ex:
        projected = "long"
        entry_bar = -1

    class _S:
        bar = "1H"
        executor = _Ex()
        _window = _FakeWindow(500)

    class _M:
        inst_id = "BTC-USDT-SWAP"
        session = _S()

    class _G:
        members = [_M()]

    now = int(time.time())
    assessment = _Assessment(
        row={"members": [{"instId": "BTC-USDT-SWAP",
                          "position": {"openWallTs": now - 10 * _BAR_SECONDS}}]},
        req=None, creds=None,
        members=[{"instId": "BTC-USDT-SWAP",
                  "actual": {"cTimeS": now - 4 * _BAR_SECONDS}}])
    executor = _S.executor
    _adopt_entry_bars(_G(), assessment)
    assert executor.entry_bar == 496                # 交易所 cTime 优先于本地 wallTs（[P2-14]）

    assessment.members = [{"instId": "BTC-USDT-SWAP", "actual": {}}]
    _adopt_entry_bars(_G(), assessment)
    assert executor.entry_bar == 490                # 回落本地 openWallTs

    assessment.row = {"members": [{"instId": "BTC-USDT-SWAP", "position": {}}]}
    _adopt_entry_bars(_G(), assessment)
    assert executor.entry_bar == 500                # 两者皆缺 ⇒ 回落 cur_index（如实说明）


# ---------------- G-13：`closed_offline` 的盈亏归属（最容易写出假数据的一处）----------------
async def test_closed_offline_never_writes_a_fake_zero_pnl(monkeypatch):
    """G-13 ①：回读抛错 ⇒ **`trades` 表零新增行**，且 `metrics.aiStats.offlineGap.pnlUnknown` 为真。

    绝不写 `pnl=0.0`：那是一条「这笔交易不赚不亏」的假数据，会污染胜率、验收卡与已实现 edge 校准。
    """
    strategy_id = await _fresh()
    app = _load_app()
    _override(app)
    _patch(monkeypatch)
    try:
        async with await _client(app) as client:
            group_id = await _start_and_pause(client, strategy_id)
            await _go_cold(group_id)
            _patch(monkeypatch, positions=[])          # 离线期间被平掉
            sm = get_sessionmaker()
            async with sm() as s:
                run_id = (await s.execute(select(StrategyRun.id).where(
                    StrategyRun.group_id == group_id))).scalar_one()
                before = (await s.execute(select(func.count()).select_from(Trade)
                                          .where(Trade.run_id == run_id))).scalar()

            ok = await client.post(f"/api/ai-trade/{group_id}/resume",
                                   json={"acknowledge": True})
            assert ok.status_code == 200, ok.text

            async with sm() as s:
                after = (await s.execute(select(func.count()).select_from(Trade)
                                         .where(Trade.run_id == run_id))).scalar()
                run = await s.get(StrategyRun, run_id)
        assert after == before
        import json as _json
        gap = _json.loads(run.metrics_json)["aiStats"]["offlineGap"]
        assert gap["pnlUnknown"] is True and gap["instId"] == "BTC-USDT-SWAP"
    finally:
        app.dependency_overrides.clear()
        await dispose_engine()


def test_offline_exit_accounting_is_not_a_verbatim_copy_of_realized_pnl():
    """G-13 ②（[P1-7]）：`realizedPnl` 是**已扣费且含资金费**的口径，两种 `costBasis` 各有换算。

    给定一行 `realizedPnl=-12.0, fee=1.5, fundingFee=0.4, liqPenalty=0`（归一后「支出为正」）：
    - `gross` ⇒ 把费加回去还原毛额 ⇒ `pnl == -10.5`、`fee == 1.5`；
    - `net`   ⇒ 只剥掉本地不入 `trades.pnl` 的资金费与强平罚金 ⇒ `pnl == -12.4`。
    资金费只进 `explain.meta`，**不进 pnl**——否则会与 `funding_ledger` 双记。
    """
    from app.ai_auto.resume import _offline_exit_trade

    row = {"posId": "POS-A", "realizedPnl": -12.0, "fee": 1.5, "fundingFee": 0.4,
           "liqPenalty": 0.0, "uTime": _START_TS + 60, "closeAvgPx": 29000.0}
    expected = {"size": 0.6, "openCTime": _START_TS - 4 * _BAR_SECONDS}

    gross = _offline_exit_trade(row, expected, cost_basis="gross", local_trades=[])
    assert round(gross["pnl"], 6) == -10.5 and round(gross["fee"], 6) == 1.5

    net = _offline_exit_trade(row, expected, cost_basis="net", local_trades=[])
    assert round(net["pnl"], 6) == -12.4 and round(net["fee"], 6) == 1.5
    assert net["explain"]["meta"]["offlineFundingUsd"] == 0.4
    assert net["reason"] == "offline_exit"
    assert net["explain"]["meta"]["exitSource"] == "offline"


def test_offline_exit_only_takes_the_increment_and_bails_out_when_inconsistent():
    """G-13 ③：`positions-history` 按 `posId` 汇总整个生命周期 ⇒ 只取增量；不自洽即退回诚实缺口。"""
    from app.ai_auto.resume import _offline_exit_trade

    row = {"posId": "POS-A", "realizedPnl": -12.0, "fee": 1.5, "fundingFee": 0.0,
           "liqPenalty": 0.0, "uTime": _START_TS + 60, "closeAvgPx": 29000.0}
    expected = {"size": 0.6, "openCTime": _START_TS}
    local = [{"ts": _START_TS + 10, "side": "close", "pnl": -4.0, "fee": 0.5}]

    incremental = _offline_exit_trade(row, expected, cost_basis="net", local_trades=local)
    assert round(incremental["pnl"], 6) == -8.0      # -12.0 减去暂停前已落的 -4.0
    assert round(incremental["fee"], 6) == 1.0       # 1.5 减去已落的 0.5

    over_counted = [{"ts": _START_TS + 10, "side": "close", "pnl": -4.0, "fee": 9.0}]
    assert _offline_exit_trade(row, expected, cost_basis="net",
                               local_trades=over_counted) is None    # 负手续费 ⇒ 退回缺口
    assert _offline_exit_trade(None, expected, cost_basis="net", local_trades=[]) is None


async def test_cold_end_archives_runs_without_placing_any_order(monkeypatch):
    """冷路径 `end`：进程里没有会话对象，只把各成员 run 落终态 + 状态表置 `ended`，**绝不下任何单**。

    「保留持仓」分支必须如实回执 `orphanPositions` —— 那笔仓仍在交易所上，而本地已经归档；
    不把它说出来，就是本轮立论要堵的那个洞（「今天已经存在孤儿持仓，只是没人告诉用户」）。
    """
    strategy_id = await _fresh()
    app = _load_app()
    _override(app)
    _patch(monkeypatch)
    try:
        async with await _client(app) as client:
            group_id = await _start_and_pause(client, strategy_id)
            await _go_cold(group_id)
            _FakeSession.started = []

            missing = await client.post(f"/api/ai-trade/{group_id}/end", json={})
            assert missing.status_code == 400          # 带仓 ⇒ flatten 必填
            assert missing.json()["error"]["code"] == "AI_TRADE_END_FLATTEN_REQUIRED"

            # RV-1：冷路径**做不到**平仓（没有 Executor、没有凭证），故 `flatten=true` 必须当场
            # 拒绝——照它归档会回一句「已平仓、无孤儿持仓」，而交易所上那笔仓原样还在。
            cannot = await client.post(f"/api/ai-trade/{group_id}/end",
                                       json={"flatten": True})
            assert cannot.status_code == 409
            assert cannot.json()["error"]["code"] == "AI_TRADE_END_FLATTEN_UNAVAILABLE"

            ok = await client.post(f"/api/ai-trade/{group_id}/end",
                                   json={"flatten": False,
                                         "acknowledgeOrphanPosition": True})
            assert ok.status_code == 200, ok.text
            assert ok.json()["orphanPositions"][0]["instId"] == "BTC-USDT-SWAP"

            sm = get_sessionmaker()
            async with sm() as s:
                row = await state_repo.load_group_state(s, group_id)
                run = (await s.execute(select(StrategyRun)
                                       .where(StrategyRun.group_id == group_id))).scalars().first()
        assert row["status"] == "ended" and run.status == "stopped"
        assert _FakeSession.started == []              # 冷路径连会话都没建，更没下单
        assert get_ai_trade_manager()._paused_ref is None
        status = None
        async with await _client(app) as client:
            status = (await client.get("/api/ai-trade/status")).json()
        assert status["group"] is None and status["resumable"] is None
    finally:
        app.dependency_overrides.clear()
        await dispose_engine()
