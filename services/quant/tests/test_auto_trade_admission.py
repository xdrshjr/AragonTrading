"""共享准入锁互斥单测（ai-auto-trading §2.5 / P1-4）：AI 与 live-auto 并发 start 经
`AUTO_TRADE_ADMISSION_LOCK` 串行，至多一方成功、另一方 409（覆盖 TOCTOU 竞争）。

AI 侧 preflight 取数刻意 `await asyncio.sleep(0)` 让出事件循环，模拟真实网络往返窗口——若无共享锁，
另一 start 会在此窗口并发通过双双注册；有锁则第二方在锁释放后 `_require_capacity` 侦测到对方已注册而 409。
OKX / LLM / 会话全程打桩，铁律：占位假凭证。
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

from app.ai_auto.manager import (
    AiTradeGroupMemberInput,
    AiTradeGroupStartRequest,
    AiTradeSessionLimitError,
    get_ai_trade_manager,
)
from app.config import Settings
from app.db import repositories as repo
from app.db.session import dispose_engine, get_sessionmaker, init_db
from app.live_auto.manager import (
    GroupMemberInput,
    GroupStartRequest,
    LiveAutoSessionLimitError,
    get_live_auto_manager,
)
from app.paper.strategy_factory import build_paper_strategy_spec
from sqlalchemy import select

from app.db.models import Strategy

_CONFIG = {"instrumentSupported": True, "availUsdt": 1000.0, "posMode": "long_short_mode",
           "instMeta": {"ctVal": 0.01, "lotSz": 0.01, "minSz": 0.01, "tickSz": 0.1,
                        "maxLever": 125.0}, "lastPx": 30000.0}
_ACCOUNT = SimpleNamespace(id=1, api_key="ak", secret_key="sk-000000", passphrase="pp-000000")


class _FakeAiSession:
    def __init__(self, session_id, run_id, specs, inst_id, bar, executor, decider, **kw):
        self.session_id = session_id
        self.run_id = run_id
        self.executor = executor
        self.state = "running"
        self.strategy_ids = [s.strategy_id for s in specs]

    async def start(self):
        return None

    async def stop(self, reason="user_stop"):
        self.state = "stopped"
        return {"ok": True, "closedSz": 0}

    def info(self):
        return {"sessionId": self.session_id, "state": self.state}

    def discipline_wire(self):
        """生效纪律自检的会话桩（discipline-preset 轴 B）：桩不装配任何层 ⇒ 空行 + 无提示。

        `group_status()` 会调它——真实 `AiTradeSession` 新增的**被 manager 读到的**方法，
        测试替身必须同步跟上，否则守的是一个与生产不同构的接口。
        """
        return {"rows": [], "hint": None}

    def acceptance_bundle(self):
        """验收闭环四键的会话桩（horizon-acceptance）：无成交样本 ⇒ 四键恒 None。"""
        return {"acceptance": None, "exits": None, "funding": None,
                "acceptanceInput": None}


class _FakeLiveSession:
    def __init__(self, session_id, run_id, strategy, inst_id, bar, executor, **kw):
        self.session_id = session_id
        self.run_id = run_id
        self.executor = executor
        self.state = "running"

    async def start(self):
        return None

    async def stop(self, reason="user_stop"):
        self.state = "stopped"
        return {"ok": True, "closedSz": 0}

    def info(self):
        return {"sessionId": self.session_id, "state": self.state}


def _patch(monkeypatch) -> None:
    async def ai_config(*_a, **_k):
        await asyncio.sleep(0)                # 让出事件循环，暴露 TOCTOU 窗口
        return dict(_CONFIG)

    async def live_config(*_a, **_k):
        return dict(_CONFIG)

    async def positions(*_a, **_k):
        return []

    async def open_orders(*_a, **_k):
        return []

    async def resolve(*_a, **_k):
        return object()

    monkeypatch.setattr("app.ai_auto.manager.fetch_trading_config", ai_config)
    monkeypatch.setattr("app.ai_auto.manager.fetch_live_positions", positions)
    # ai-trade-limit-orders §2.9：启动前置校验新增「已有普通挂单即拒」，须一并桩掉，否则用例出网。
    monkeypatch.setattr("app.ai_auto.manager.fetch_live_open_orders", open_orders)
    monkeypatch.setattr("app.ai_auto.manager.resolve_active_provider", resolve)
    monkeypatch.setattr("app.ai_auto.manager.make_llm_client_from_config", lambda cfg: object())
    monkeypatch.setattr("app.ai_auto.manager.AiTradeSession", _FakeAiSession)
    monkeypatch.setattr("app.live_auto.manager.fetch_trading_config", live_config)
    monkeypatch.setattr("app.live_auto.manager.fetch_live_positions", positions)
    monkeypatch.setattr("app.live_auto.manager.LiveAutoSession", _FakeLiveSession)


async def _spec():
    sm = get_sessionmaker()
    async with sm() as s:
        sid = (await s.execute(
            select(Strategy.id).order_by(Strategy.id.asc()).limit(1))).scalar_one()
        row = await repo.get_strategy_row(s, sid)
    return build_paper_strategy_spec(row)


async def test_concurrent_ai_and_live_auto_start_only_one_succeeds(monkeypatch):
    await init_db()
    get_ai_trade_manager()._group = None
    get_live_auto_manager()._group = None
    _patch(monkeypatch)
    settings = Settings(live_trading_enabled=True, ai_trade_enabled=True,
                        live_auto_trading_enabled=True)
    spec = await _spec()
    ai_req = AiTradeGroupStartRequest(
        specs=[spec],
        members_in=[AiTradeGroupMemberInput(inst_id="BTC-USDT-SWAP", allocation_pct=100.0)],
        bar="1H", account_row=_ACCOUNT, settings=settings, context_refresh_rounds=10)
    live_req = GroupStartRequest(
        strategy_spec=spec, bar="1H",
        members_in=[GroupMemberInput(inst_id="BTC-USDT-SWAP", allocation_pct=100.0)],
        account_row=_ACCOUNT, settings=settings)
    try:
        results = await asyncio.gather(
            get_ai_trade_manager().start_group(ai_req),
            get_live_auto_manager().start_group(live_req),
            return_exceptions=True)
        successes = [r for r in results if not isinstance(r, Exception)]
        failures = [r for r in results if isinstance(r, Exception)]
        assert len(successes) == 1, results
        assert len(failures) == 1, results
        assert isinstance(failures[0], (AiTradeSessionLimitError, LiveAutoSessionLimitError))
    finally:
        grp = get_ai_trade_manager()._group
        if grp is not None:
            for member in grp.members:
                await member.session.stop("cleanup")
        get_ai_trade_manager()._group = None
        get_live_auto_manager()._group = None
        await dispose_engine()
