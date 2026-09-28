"""真实 SQLite 上验证发送所有权和只读恢复。"""
import json
import asyncio
import time
from uuid import uuid4

import pytest
import httpx
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.agent_trading.reconcile import reconcile_state


@pytest.mark.parametrize("target", ["live", "partially_filled"])
def test_unknown_cancel_cannot_become_accepted_from_get(target):
    assert reconcile_state("cancel", "unknown", target) == "unknown"
    assert reconcile_state("cancel", "accepted", target) == "accepted"


def test_cancel_terminal_and_order_state_are_distinct():
    assert reconcile_state("cancel", "unknown", "filled") == "filled"
    assert reconcile_state("open", "unknown", "live") == "accepted"
    assert reconcile_state("close", "unknown", "partially_filled") == "partially_filled"


@pytest.fixture
async def intent_store(tmp_path):
    from app.db.models import Base
    from app.db.agent_trade_models import AgentTradeIntent
    from app.db.agent_trade_repositories import AgentTradeRepository
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'intent.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    now = int(time.time())
    row = AgentTradeIntent(
        id=str(uuid4()), conversation_id="chat", client_message_id=str(uuid4()),
        tool_use_id="tool", account_id=1, environment="demo", binding_version=1,
        credential_fingerprint="f" * 64, action="open", status="submitting",
        version=2, request_hash="r" * 64, preview_hash="p" * 64,
        request_json="{}", preview_json=json.dumps({"payload": {"instId": "BTC-USDT-SWAP"}}),
        cl_ord_id="agt" + uuid4().hex[:29], created_ts=now,
        expires_ts=now + 60, updated_ts=now, check_attempts=0)
    async with factory() as session:
        session.add(row)
        await session.commit()
    yield factory, AgentTradeRepository(factory), row.id
    await engine.dispose()


@pytest.mark.asyncio
async def test_startup_converts_submitting_without_post(intent_store):
    from app.agent_trading.reconcile import recover_startup
    factory, repository, identity = intent_store
    await recover_startup(factory)
    assert (await repository.get_intent(identity)).status == "unknown"


@pytest.fixture
async def cancel_ready(intent_store, monkeypatch):
    from app.agent_trading import admission
    from app.agent_trading.binding import resolve_account
    from app.config import get_settings
    from app.db.agent_trade_models import AgentTradeBinding, AgentTradeIntent
    from app.db.models import OkxAccount
    from app.ai_auto.manager import get_ai_trade_manager
    from app.live_auto.manager import get_live_auto_manager
    factory, repository, identity = intent_store
    cfg = get_settings()
    for field in ("agent_trading_enabled", "agent_trading_execution_enabled",
                  "live_trading_enabled", "okx_account_demo"):
        monkeypatch.setattr(cfg, field, True)
    monkeypatch.setattr(get_ai_trade_manager(), "status", lambda: None)
    monkeypatch.setattr(get_live_auto_manager(), "group_status", lambda: None)
    async with factory() as session:
        session.add(OkxAccount(id=1, label="测试账户", api_key="test-key",
                               secret_key="secret", passphrase="phrase"))
        session.add(AgentTradeBinding(conversation_id="chat", account_id=1,
                    environment="demo", account_mode="live", version=1,
                    updated_ts=int(time.time())))
        await session.commit()
    account = await resolve_account(1, factory)
    async with factory() as session:
        row = await session.get(AgentTradeIntent, identity)
        row.status, row.action, row.version = "prepared", "cancel", 1
        row.cl_ord_id, row.target_ord_id = None, "123"
        row.credential_fingerprint = account.credential_fingerprint
        row.preview_json = json.dumps({"action": "cancel", "payload": {
            "instId": "BTC-USDT-SWAP", "ordId": "123"}})
        await session.commit()
    admission.set_ready(True)
    yield factory, repository, identity
    from app.agent_trading.execution import shutdown
    await shutdown()


def install_transport(monkeypatch, handler):
    from app.okx.trade_client import OKXTradeClient
    original = OKXTradeClient.__init__
    def initialize(self, *args, **kwargs):
        original(self, *args, **kwargs)
        self._client = httpx.AsyncClient(base_url="https://example.test",
                                         transport=httpx.MockTransport(handler))
    monkeypatch.setattr(OKXTradeClient, "__init__", initialize)


async def await_owner(identity):
    from app.agent_trading.execution import _owners
    task = _owners.get(identity)
    if task:
        await asyncio.shield(task)


@pytest.mark.asyncio
async def test_concurrent_execute_and_timeout_send_only_once(cancel_ready, monkeypatch):
    from app.agent_trading.execution import execute_intent
    factory, repository, identity = cancel_ready
    calls = []
    def handler(request):
        calls.append(request.method)
        if request.method == "POST":
            raise httpx.ReadTimeout("响应丢失")
        return httpx.Response(200, json={"code": "0", "data": [{
            "instId": "BTC-USDT-SWAP", "ordId": "123", "state": "live"}]})
    install_transport(monkeypatch, handler)
    request = {"version": 1, "previewHash": "p" * 64}
    await asyncio.gather(*(execute_intent(factory, "chat", identity, request)
                           for _ in range(5)))
    await await_owner(identity)
    assert (await repository.get_intent(identity)).status == "unknown"
    await execute_intent(factory, "chat", identity, request)
    assert calls.count("POST") == 1


@pytest.mark.asyncio
async def test_cancel_unknown_get_live_stays_unknown(cancel_ready, monkeypatch):
    from app.agent_trading.reconcile import reconcile_one
    factory, repository, identity = cancel_ready
    row = await repository.get_intent(identity)
    row = await repository.transition(identity, row.version, "submitting")
    row = await repository.transition(identity, row.version, "unknown")
    calls = []
    def handler(request):
        calls.append(request.method)
        return httpx.Response(200, json={"code": "0", "data": [{
            "instId": "BTC-USDT-SWAP", "ordId": "123", "state": "live",
            "accFillSz": "1", "avgPx": "100"}]})
    install_transport(monkeypatch, handler)
    current = await reconcile_one(repository, row)
    assert current.status == "unknown"
    assert json.loads(current.result_json)["accFillSz"] == "1"
    assert calls == ["GET"]


@pytest.mark.asyncio
async def test_environment_change_blocks_recovery_without_get(cancel_ready, monkeypatch):
    from app.agent_trading.reconcile import reconcile_one
    from app.config import get_settings
    factory, repository, identity = cancel_ready
    row = await repository.get_intent(identity)
    row = await repository.transition(identity, row.version, "submitting")
    row = await repository.transition(identity, row.version, "unknown")
    calls = []
    install_transport(monkeypatch, lambda request: calls.append(request))
    monkeypatch.setattr(get_settings(), "okx_account_demo", False)
    current = await reconcile_one(repository, row)
    assert current.status == "unknown"
    assert current.error_code == "ENVIRONMENT_CHANGED"
    assert calls == []


@pytest.mark.asyncio
async def test_missing_fill_is_null_not_synthetic_zero(cancel_ready, monkeypatch):
    from app.agent_trading.reconcile import reconcile_one
    _, repository, identity = cancel_ready
    row = await repository.get_intent(identity)
    row = await repository.transition(identity, row.version, "submitting")
    row = await repository.transition(identity, row.version, "unknown")
    install_transport(monkeypatch, lambda request: httpx.Response(200, json={
        "code": "0", "data": [{"instId": "BTC-USDT-SWAP", "ordId": "123", "state": "live"}]}))
    current = await reconcile_one(repository, row)
    assert json.loads(current.result_json)["accFillSz"] is None


@pytest.mark.asyncio
async def test_caller_cancel_does_not_cancel_managed_sender(cancel_ready, monkeypatch):
    from app.agent_trading.execution import execute_intent, has_owner
    factory, repository, identity = cancel_ready
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []
    async def handler(request):
        calls.append(request.method)
        if request.method == "POST":
            entered.set()
            await release.wait()
            return httpx.Response(200, json={"code": "0", "data": [{"ordId": "123"}]})
        return httpx.Response(200, json={"code": "0", "data": [{
            "instId": "BTC-USDT-SWAP", "ordId": "123", "state": "live"}]})
    install_transport(monkeypatch, handler)
    result = await execute_intent(factory, "chat", identity,
                                  {"version": 1, "previewHash": "p" * 64})
    assert result.status == "submitting"
    await entered.wait()
    assert has_owner(identity)
    release.set()
    await await_owner(identity)
    assert (await repository.get_intent(identity)).status == "accepted"
    assert calls.count("POST") == 1


@pytest.mark.asyncio
async def test_canceled_owner_preserves_unknown_and_recovers_only_get(cancel_ready, monkeypatch):
    from app.agent_trading.execution import execute_intent, _owners
    from app.agent_trading.reconcile import reconcile_one
    factory, repository, identity = cancel_ready
    entered = asyncio.Event()
    calls = []
    async def handler(request):
        calls.append(request.method)
        if request.method == "POST":
            entered.set()
            await asyncio.Event().wait()
        return httpx.Response(200, json={"code": "0", "data": [{
            "instId": "BTC-USDT-SWAP", "ordId": "123", "state": "canceled"}]})
    # 首次查询活单，发送后取消 owner，再查终态。
    count = 0
    async def transport(request):
        nonlocal count
        if request.method == "GET" and count == 0:
            count += 1
            calls.append("GET")
            return httpx.Response(200, json={"code": "0", "data": [{
                "instId": "BTC-USDT-SWAP", "ordId": "123", "state": "live"}]})
        return await handler(request)
    install_transport(monkeypatch, transport)
    await execute_intent(factory, "chat", identity,
                          {"version": 1, "previewHash": "p" * 64})
    await entered.wait()
    task = _owners[identity]
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    row = await repository.get_intent(identity)
    assert row.status == "unknown"
    row = await reconcile_one(repository, row)
    assert row.status == "canceled"
    assert calls.count("POST") == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("dispatch_allowed,risk_allowed", [(False, True), (True, False)])
async def test_real_open_boundary_combines_both_guards(
        intent_store, monkeypatch, dispatch_allowed, risk_allowed):
    from types import SimpleNamespace
    from app.okx import trading
    from app.okx.accounts import AccountCredentials
    from app.okx.order_builder import InstrumentMeta, IntentOrderInput, SubmissionTrace
    from app.okx.private_client import OKXPrivateError
    from app.config import Settings
    factory, _, _ = intent_store
    calls, checks = [], []
    def handler(request):
        calls.append(request.method)
        result = {"posMode": "net_mode"} if "config" in request.url.path else None
        return httpx.Response(200, json={"code": "0", "data": [result] if result else []})
    install_transport(monkeypatch, handler)
    async def metadata(_, **kwargs):
        return InstrumentMeta(0.01, "BTC", 1, 1, 0.1, 100, "linear", "USDT")
    async def ticker(_):
        return 100
    monkeypatch.setattr(trading, "_load_meta", metadata)
    monkeypatch.setattr(trading, "get_rest_client", lambda: SimpleNamespace(get_ticker=ticker))
    class Guard:
        def validate(self, evidence):
            checks.append("dispatch")
            if not dispatch_allowed:
                raise OKXPrivateError("PREVIEW_STALE", "预览失效")
    class Risk:
        def validate(self, *args, **kwargs):
            checks.append("risk")
            return risk_allowed
    trace = SubmissionTrace()
    order = IntentOrderInput("BTC-USDT-SWAP", "long", "market", "cross",
                       None, 2, None, None, None, "agt-test", trace, Guard())
    async with factory() as session:
        with pytest.raises(OKXPrivateError):
            await trading.place_live_order(session, AccountCredentials("a", "b", "c"),
                1, order, settings=Settings(live_max_order_notional_usd=10000), risk_guard=Risk())
    assert "dispatch" in checks
    if dispatch_allowed:
        assert "risk" in checks
    assert "POST" not in calls
    assert trace.phase == "not_sent"


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["open", "close"])
async def test_frozen_order_executes_via_one_normal_order_post(cancel_ready, monkeypatch, action):
    from types import SimpleNamespace
    from app.agent_trading.execution import execute_intent
    from app.db.agent_trade_models import AgentTradeIntent
    from app.okx import trading
    from app.okx.order_builder import InstrumentMeta
    factory, repository, identity = cancel_ready
    payload = {"instId": "BTC-USDT-SWAP", "tdMode": "cross", "side": "buy",
               "ordType": "market", "sz": "2"}
    preview = {"payload": payload, "referencePrice": "100", "acctLv": "2",
               "posMode": "net_mode", "leverageObserved": "5"}
    position = {"instId": payload["instId"], "pos": "10", "posId": "pos1",
                "posSide": "net", "mgnMode": "cross", "cTime": "100"}
    if action == "close":
        payload.update(side="sell", reduceOnly=True)
        preview.update(positionObservedSz="10", positionIdentity={
            "posId": "pos1", "exchangeCTimeMs": "100", "netSign": 1, "mgnMode": "cross"})
    async with factory() as session:
        row = await session.get(AgentTradeIntent, identity)
        row.action, row.target_ord_id = action, None
        row.preview_json = json.dumps(preview)
        await session.commit()
    posts, paths = [], []
    def handler(request):
        paths.append(request.url.path)
        if request.method == "POST":
            posts.append(json.loads(request.content))
            return httpx.Response(200, json={"code": "0", "data": [{"ordId": "456"}]})
        result = {
            "/api/v5/account/config": [{"posMode": "net_mode", "acctLv": "2"}],
            "/api/v5/account/positions": [position] if action == "close" else [],
            "/api/v5/account/leverage-info": [{"instId": payload["instId"], "mgnMode": "cross", "posSide": "net", "lever": "5"}],
            "/api/v5/account/max-size": [{"instId": payload["instId"], "maxBuy": "20", "maxSell": "20"}],
            "/api/v5/account/balance": [{"details": [{"ccy": "USDT", "availEq": "1000"}]}],
        }[request.url.path]
        return httpx.Response(200, json={"code": "0", "data": result})
    install_transport(monkeypatch, handler)
    async def metadata(_, **kwargs):
        return InstrumentMeta(0.01, "BTC", 1, 1, 0.1, 100, "linear", "USDT")
    async def ticker(_):
        return 100
    monkeypatch.setattr(trading, "_load_meta", metadata)
    monkeypatch.setattr(trading, "get_rest_client", lambda: SimpleNamespace(get_ticker=ticker))
    await execute_intent(factory, "chat", identity, {"version": 1, "previewHash": "p" * 64})
    await await_owner(identity)
    current = await repository.get_intent(identity)
    assert current.status == "accepted", current.error_code
    assert len(posts) == 1
    assert posts[0]["sz"] == "2"
    assert posts[0]["clOrdId"] == current.cl_ord_id
    if action == "close":
        assert posts[0]["reduceOnly"] is True
        assert "/api/v5/account/balance" not in paths
        assert "/api/v5/account/max-size" not in paths
    assert "/api/v5/trade/close-position" not in paths


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value", [("accFillSz", "NaN"), ("accFillSz", "-1"),
    ("accFillSz", "3"), ("avgPx", "-10"), ("avgPx", "Infinity")])
async def test_invalid_exchange_fill_cannot_create_terminal(intent_store, field, value):
    from app.agent_trading.reconcile import _apply
    from app.agent_trading.errors import TradingError
    _, repository, identity = intent_store
    row = await repository.get_intent(identity)
    row = await repository.transition(identity, row.version, "unknown")
    row.preview_json = json.dumps({"payload": {"sz": "2"}})
    result = {"ordId": "1", "state": "filled", "accFillSz": "2", "avgPx": "100", field: value}
    with pytest.raises(TradingError):
        await _apply(repository, row, result)
    assert (await repository.get_intent(identity)).status == "unknown"


def test_recovery_size_identity_compares_decimal_values():
    from types import SimpleNamespace
    from app.agent_trading.reconcile import _check_order_identity
    row = SimpleNamespace(action="open", cl_ord_id="agt1", preview_json=json.dumps({
        "payload": {"instId": "BTC-USDT-SWAP", "sz": "2", "side": "buy"}}))
    _check_order_identity(row, {"instId": "BTC-USDT-SWAP", "ordId": "1",
        "clOrdId": "agt1", "sz": "2.0", "side": "buy"}, "1")


@pytest.mark.asyncio
async def test_unreadable_store_closes_admission_with_stable_error(intent_store):
    from sqlalchemy import text
    from app.agent_trading import admission
    from app.agent_trading.errors import TradingError
    factory, _, _ = intent_store
    async with factory() as session:
        await session.execute(text("DROP TABLE agent_trade_intents"))
        await session.commit()
    admission.set_ready(True)
    with pytest.raises(TradingError) as failure:
        await admission.assert_no_unresolved(factory)
    assert failure.value.code == "STATE_STORE_UNAVAILABLE"


@pytest.mark.asyncio
async def test_credentials_changed_during_rate_wait_block_actual_post(cancel_ready, monkeypatch):
    from app.agent_trading.execution import execute_intent
    from app.db.models import OkxAccount
    from app.okx.trade_client import OKXTradeClient
    factory, repository, identity = cancel_ready
    calls, slots = [], 0
    async def acquire(_):
        nonlocal slots
        slots += 1
        if slots == 2:
            async with factory() as session:
                account = await session.get(OkxAccount, 1)
                account.secret_key = "changed-secret"
                await session.commit()
    def transport(request):
        calls.append(request.method)
        result = {"ordId": "123", "instId": "BTC-USDT-SWAP", "state": "live"}
        return httpx.Response(200, json={"code": "0", "data": [result]})
    install_transport(monkeypatch, transport)
    monkeypatch.setattr(OKXTradeClient, "_acquire_slot", acquire)
    await execute_intent(factory, "chat", identity, {"version": 1, "previewHash": "p" * 64})
    await await_owner(identity)
    current = await repository.get_intent(identity)
    assert current.status == "rejected"
    assert current.error_code == "ACCOUNT_CREDENTIALS_CHANGED"
    assert calls == ["GET"]


def test_recovery_rejects_wrong_position_side_for_implicit_net():
    from types import SimpleNamespace
    from app.agent_trading.errors import TradingError
    from app.agent_trading.reconcile import _check_order_identity
    row = SimpleNamespace(action="open", cl_ord_id="agt1", preview_json=json.dumps({
        "payload": {"instId": "BTC-USDT-SWAP", "sz": "2", "side": "buy"},
        "posMode": "net_mode"}))
    result = {"instId": "BTC-USDT-SWAP", "ordId": "1", "clOrdId": "agt1",
              "sz": "2", "side": "buy", "posSide": "long"}
    with pytest.raises(TradingError, match="IDENTITY_MISMATCH"):
        _check_order_identity(row, result, "1")


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["open", "close", "cancel"])
@pytest.mark.parametrize("response_kind,expected", [
    ("51002", "rejected"), ("51131", "rejected"),
    ("50004", "unknown"), ("51149", "unknown"),
    ("51016", "unknown"), ("http500", "unknown"),
    ("timeout", "unknown"), ("top_only", "unknown"),
    ("empty_ack", "unknown"),
])
async def test_only_definite_business_rejection_releases_unsettled(
        cancel_ready, monkeypatch, response_kind, expected, action):
    from types import SimpleNamespace
    from app.agent_trading.execution import execute_intent
    from app.db.agent_trade_models import AgentTradeIntent
    from app.okx import trading
    from app.okx.order_builder import InstrumentMeta
    factory, repository, identity = cancel_ready
    payload = {"instId": "BTC-USDT-SWAP", "tdMode": "cross", "side": "buy",
               "ordType": "market", "sz": "2"}
    preview = {"payload": payload, "referencePrice": "100",
               "acctLv": "2", "posMode": "net_mode", "leverageObserved": "5"}
    position = {"instId": payload["instId"], "pos": "10", "posId": "pos1",
                "posSide": "net", "mgnMode": "cross", "cTime": "100"}
    if action == "close":
        payload.update(side="sell", reduceOnly=True)
        preview.update(positionObservedSz="10", positionIdentity={
            "posId": "pos1", "exchangeCTimeMs": "100", "netSign": 1, "mgnMode": "cross"})
    elif action == "cancel":
        preview = {"payload": {"instId": payload["instId"], "ordId": "123"}}
    async with factory() as session:
        row = await session.get(AgentTradeIntent, identity)
        row.action = action
        row.target_ord_id = "123" if action == "cancel" else None
        row.preview_json = json.dumps(preview)
        await session.commit()
    posts = []
    def handler(request):
        if request.method == "POST":
            posts.append(request)
            if response_kind == "http500":
                return httpx.Response(500, json={"code": "51002", "data": []})
            if response_kind == "timeout":
                raise httpx.ReadTimeout("响应丢失")
            if response_kind == "top_only":
                return httpx.Response(200, json={"code": "51002", "data": []})
            if response_kind == "empty_ack":
                return httpx.Response(200, json={"code": "0", "data": [{}]})
            return httpx.Response(200, json={"code": "1", "data": [{
                "sCode": response_kind, "sMsg": "交易所回执", "ordId": ""}]})
        result = {
            "/api/v5/account/config": [{"posMode": "net_mode", "acctLv": "2"}],
            "/api/v5/account/positions": [position] if action == "close" else [],
            "/api/v5/trade/order": [{"instId": payload["instId"], "ordId": "123", "state": "live"}],
            "/api/v5/account/leverage-info": [{"instId": payload["instId"],
                "mgnMode": "cross", "posSide": "net", "lever": "5"}],
            "/api/v5/account/max-size": [{"instId": payload["instId"], "maxBuy": "20"}],
            "/api/v5/account/balance": [{"details": [{"ccy": "USDT", "availEq": "1000"}]}],
        }[request.url.path]
        return httpx.Response(200, json={"code": "0", "data": result})
    install_transport(monkeypatch, handler)
    async def metadata(_, **kwargs):
        return InstrumentMeta(0.01, "BTC", 1, 1, 0.1, 100, "linear", "USDT")
    async def ticker(_):
        return 100
    monkeypatch.setattr(trading, "_load_meta", metadata)
    monkeypatch.setattr(trading, "get_rest_client", lambda: SimpleNamespace(get_ticker=ticker))
    await execute_intent(factory, "chat", identity, {"version": 1, "previewHash": "p" * 64})
    await await_owner(identity)
    row = await repository.get_intent(identity)
    assert len(posts) == 1
    assert row.status == expected
    assert bool(await repository.list_unsettled()) == (expected == "unknown")
