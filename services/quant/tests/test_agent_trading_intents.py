"""真实 SQLite 的冻结身份、静态去重和状态 CAS 契约。"""

import asyncio
import json
import time
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


@pytest.fixture
async def store(tmp_path):
    from app.db.models import Base
    from app.db import agent_trade_models  # noqa: F401

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/trade.db")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


def intent_values(**patch):
    now = int(time.time())
    values = dict(
        id=str(uuid4()),
        conversation_id="chat",
        client_message_id="msg",
        tool_use_id="tool",
        account_id=1,
        environment="demo",
        binding_version=1,
        credential_fingerprint="f" * 64,
        action="open",
        status="prepared",
        version=1,
        request_hash="r" * 64,
        preview_hash="p" * 64,
        request_json="{}",
        preview_json="{}",
        created_ts=now,
        expires_ts=now + 60,
        updated_ts=now,
    )
    return values | patch


@pytest.mark.asyncio
async def test_unique_request_and_compare_and_swap(store):
    from app.db.agent_trade_repositories import AgentTradeRepository
    from app.db.agent_trade_models import AgentTradeEvent
    from app.agent_trading.errors import TradingError

    repo = AgentTradeRepository(store)
    rows = await asyncio.gather(
        *[repo.create_intent(intent_values()) for _ in range(3)]
    )
    assert len({row.id for row in rows}) == 1
    row = await repo.transition(rows[0].id, 1, "submitting")
    assert row.version == 2
    with pytest.raises(TradingError, match="INTENT_VERSION_CONFLICT"):
        await repo.transition(row.id, 1, "submitting")
    assert await repo.recover_submitting() == 1
    assert (await repo.get_intent(row.id)).status == "unknown"
    async with store() as session:
        events = list((await session.scalars(select(AgentTradeEvent))).all())
    assert [event.intent_version for event in events] == [1, 2, 3]


@pytest.mark.asyncio
async def test_terminal_is_immutable_and_fill_never_decreases(store):
    from app.db.agent_trade_repositories import AgentTradeRepository
    from app.agent_trading.errors import TradingError

    repo = AgentTradeRepository(store)
    row = await repo.create_intent(intent_values())
    row = await repo.transition(row.id, row.version, "submitting")
    row = await repo.transition(
        row.id,
        row.version,
        "accepted",
        {"result_json": json.dumps({"accFillSz": "2"})},
    )
    with pytest.raises(TradingError):
        await repo.transition(
            row.id,
            row.version,
            "accepted",
            {"result_json": json.dumps({"accFillSz": "1"})},
        )
    row = await repo.transition(row.id, row.version, "filled")
    with pytest.raises(TradingError):
        await repo.transition(row.id, row.version, "accepted")


def test_schema_rejects_ambiguous_nonfinite_and_unknown_inputs():
    from app.agent_trading.schemas import parse_trade
    from app.agent_trading.errors import TradingError

    request = dict(
        action="open",
        instId="BTC-USDT-SWAP",
        direction="long",
        ordType="market",
        tdMode="cross",
        size={"unit": "contracts", "value": "1"},
    )
    for value in [True, 1.0, "NaN", "Infinity", "1e999", "0", "-1"]:
        with pytest.raises(TradingError):
            parse_trade(
                request | {"size": {"unit": "contracts", "value": value}}
            )
    with pytest.raises(TradingError):
        parse_trade(request | {"accountId": 2})
    assert parse_trade(request).size.value == "1"


def test_preview_decimal_floor_and_frozen_close_identity():
    from app.agent_trading.preview import build_preview
    from app.agent_trading.schemas import parse_trade
    from app.agent_trading.read_service import position_key
    from app.agent_trading.errors import TradingError

    facts = {
        "config": {"acctLv": "2", "posMode": "net_mode"},
        "balance": {"details": [{"ccy": "USDT", "availEq": "1000"}]},
        "positions": [],
        "orders": {"complete": True, "rows": []},
        "meta": {
            "instType": "SWAP",
            "ctType": "linear",
            "settleCcy": "USDT",
            "ctVal": "0.01",
            "ctValCcy": "BTC",
            "lotSz": "0.1",
            "minSz": "0.1",
            "tickSz": "0.1",
            "lever": "100",
        },
        "ticker": "100",
        "leverage": [
            {
                "instId": "BTC-USDT-SWAP",
                "mgnMode": "cross",
                "posSide": "net",
                "lever": "5",
            }
        ],
        "capacity": [
            {"instId": "BTC-USDT-SWAP", "maxBuy": "100", "maxSell": "100"}
        ],
    }
    request = parse_trade(
        dict(
            action="open",
            instId="BTC-USDT-SWAP",
            direction="long",
            ordType="market",
            tdMode="cross",
            size={"unit": "notional_usdt", "value": "1.29"},
        )
    )
    preview = build_preview(request, facts)
    assert preview["sizeContracts"] == "1.2"
    assert preview["marginEstimateUsdt"] == "0.24"
    assert preview["payload"]["sz"] == "1.2"
    position = {
        "instId": "BTC-USDT-SWAP",
        "posSide": "net",
        "mgnMode": "cross",
        "pos": "2",
        "posId": "42",
        "cTime": "1000",
    }
    facts["positions"] = [position]
    close = parse_trade(
        {
            "action": "close",
            "positionKey": position_key(position),
            "ordType": "market",
            "closeSize": "all",
        }
    )
    preview = build_preview(close, facts)
    assert preview["sizeContracts"] == "2"
    assert preview["positionIdentity"]["posId"] == "42"
    assert preview["payload"]["reduceOnly"] is True
    del position["posId"]
    with pytest.raises(TradingError, match="POSITION_IDENTITY_UNAVAILABLE"):
        build_preview(close, facts)


@pytest.mark.asyncio
async def test_static_dedup_precedes_market_read_and_clear_rejects_late_prepare(
    store, monkeypatch
):
    from app.agent_trading import intent_service
    from app.agent_trading.binding import (
        BindingService,
        clear_conversation,
        freeze_call,
    )
    from app.agent_trading.errors import TradingError
    from app.db.models import OkxAccount

    async with store() as session:
        session.add(
            OkxAccount(
                id=1, label="测试", api_key="k", secret_key="s", passphrase="p"
            )
        )
        await session.commit()
    await BindingService(store).put(
        "chat", {"accountId": 1, "accountMode": "live", "expectedVersion": 0}
    )
    call = await freeze_call(store, "chat", "msg", "live")
    reads = []

    async def read_facts(*args):
        reads.append(1)
        return {}

    monkeypatch.setattr(intent_service, "load_facts", read_facts)
    monkeypatch.setattr(
        intent_service,
        "build_preview",
        lambda *args: {"referencePrice": str(len(reads))},
    )
    request = dict(
        action="open",
        instId="BTC-USDT-SWAP",
        direction="long",
        tdMode="cross",
        ordType="market",
        size={"unit": "contracts", "value": "1.00"},
    )
    first = await intent_service.prepare_trade(request, call, store)
    second = await intent_service.prepare_trade(
        request | {"size": {"unit": "contracts", "value": "1"}}, call, store
    )
    assert first["id"] == second["id"]
    assert len(reads) == 1
    await clear_conversation(store, "chat")
    with pytest.raises(TradingError, match="BINDING_CHANGED"):
        await intent_service.prepare_trade(request, call, store)
    from app.db.agent_trade_repositories import AgentTradeRepository

    assert (
        await AgentTradeRepository(store).get_intent(first["id"])
    ).status == "dismissed"


@pytest.mark.asyncio
async def test_preview_quota_dedup_and_expiry_are_atomic(store):
    from app.db.agent_trade_repositories import AgentTradeRepository
    from app.agent_trading.errors import TradingError

    repo = AgentTradeRepository(store)
    for index in range(20):
        await repo.create_intent(intent_values(request_hash=str(index)))
    assert await repo.create_intent(intent_values(request_hash="0"))
    with pytest.raises(TradingError, match="TOO_MANY_TRADE_PREVIEWS"):
        await repo.create_intent(intent_values(request_hash="extra"))


@pytest.mark.asyncio
async def test_twenty_one_concurrent_distinct_requests_never_exceed_quota(
    store,
):
    from app.db.agent_trade_repositories import AgentTradeRepository
    from app.agent_trading.errors import TradingError
    from app.db.agent_trade_models import AgentTradeIntent, AgentTradeEvent

    repo = AgentTradeRepository(store)
    outcomes = await asyncio.gather(
        *[
            repo.create_intent(intent_values(request_hash=f"parallel-{index}"))
            for index in range(21)
        ],
        return_exceptions=True,
    )
    rejected = [result for result in outcomes if isinstance(result, Exception)]
    assert len(rejected) == 1
    assert isinstance(rejected[0], TradingError)
    assert rejected[0].code == "TOO_MANY_TRADE_PREVIEWS"
    async with store() as session:
        intents = list((await session.scalars(select(AgentTradeIntent))).all())
        events = list((await session.scalars(select(AgentTradeEvent))).all())
    assert len(intents) == len(events) == 20
    assert all(row.status == "prepared" for row in intents)
    assert len({row.request_hash for row in intents}) == 20


@pytest.mark.asyncio
async def test_expired_message_keeps_id_and_ttl_new_message_gets_new_intent(
    store,
):
    from app.db.agent_trade_repositories import AgentTradeRepository

    repo = AgentTradeRepository(store)
    values = intent_values(expires_ts=int(time.time()) - 1)
    first = await repo.create_intent(values)
    expired = await repo.find_request("chat", "msg", values["request_hash"])
    assert expired.id == first.id
    assert expired.status == "expired"
    assert expired.expires_ts == values["expires_ts"]
    retry = await repo.create_intent(intent_values())
    assert retry.id == first.id
    assert retry.expires_ts == values["expires_ts"]
    regenerated = await repo.create_intent(
        intent_values(client_message_id="new-msg")
    )
    assert regenerated.id != first.id
    assert regenerated.status == "prepared"


@pytest.mark.asyncio
async def test_event_failure_rolls_back_state_transition(store):
    from app.db.agent_trade_repositories import AgentTradeRepository
    from app.db.agent_trade_models import AgentTradeEvent
    from sqlalchemy import event

    repo = AgentTradeRepository(store)
    row = await repo.create_intent(intent_values())

    def fail_insert(*args):
        raise RuntimeError("audit unavailable")

    event.listen(AgentTradeEvent, "before_insert", fail_insert)
    try:
        with pytest.raises(RuntimeError):
            await repo.transition(row.id, 1, "submitting")
    finally:
        event.remove(AgentTradeEvent, "before_insert", fail_insert)
    recovered = await repo.get_intent(row.id)
    assert recovered.status == "prepared"
    assert recovered.version == 1


def test_wire_projection_drops_internal_and_unknown_fields():
    from types import SimpleNamespace
    from app.agent_trading.result_view import intent_view

    row = SimpleNamespace(
        **(
            intent_values()
            | {
                "result_json": json.dumps({"ordId": "42", "apiKey": "SECRET"}),
                "preview_json": json.dumps(
                    {"payload": {"secret": "SECRET"}, "arbitrary": "SECRET"}
                ),
                "error_code": None,
            }
        )
    )
    assert "SECRET" not in json.dumps(intent_view(row))
