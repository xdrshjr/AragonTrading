"""短事务中的请求唯一性、状态 CAS 和审计事件，不跨网络持锁。"""

from __future__ import annotations

import json
import time
from decimal import Decimal

from sqlalchemy import func, select, text, update

from ..agent_trading.errors import TradingError
from .agent_trade_models import (
    AgentTradeBinding,
    AgentTradeEvent,
    AgentTradeIntent,
)

UNSETTLED = frozenset({"submitting", "unknown", "accepted", "partially_filled"})
TERMINAL = frozenset({"filled", "canceled", "rejected", "expired", "dismissed"})
_TRANSITIONS = {
    "prepared": {"dismissed", "expired", "rejected", "submitting"},
    "submitting": {"rejected", "unknown", "accepted"},
    "unknown": {"accepted", "partially_filled", "filled", "canceled"},
    "accepted": {"partially_filled", "filled", "canceled"},
    "partially_filled": {"filled", "canceled"},
}
_MUTABLE = {
    "result_json",
    "cl_ord_id",
    "target_ord_id",
    "error_code",
    "next_check_ts",
    "check_attempts",
    "updated_ts",
}


def add_event(session, row, kind):
    """事件与对应状态写在同一事务，版本不要求连续。"""
    session.add(
        AgentTradeEvent(
            intent_id=row.id,
            intent_version=row.version,
            kind=kind,
            detail_json=json.dumps(
                {"status": row.status, "errorCode": row.error_code}
            ),
            created_ts=int(time.time()),
        )
    )


async def expire_previews(session, conversation_id=None):
    """惰性过期只影响 prepared；调用者持有短写事务。"""
    query = select(AgentTradeIntent).where(
        AgentTradeIntent.status == "prepared",
        AgentTradeIntent.expires_ts <= int(time.time()),
    )
    if conversation_id is not None:
        query = query.where(AgentTradeIntent.conversation_id == conversation_id)
    for row in (await session.scalars(query)).all():
        row.status = "expired"
        row.version += 1
        row.updated_ts = int(time.time())
        add_event(session, row, "expired")
    await session.flush()


def _validate_changes(row, status, changes):
    if set(changes) - _MUTABLE:
        raise TradingError("INVALID_TRADING_REQUEST")
    if row.status in TERMINAL or (
        status != row.status
        and status not in _TRANSITIONS.get(row.status, set())
    ):
        raise TradingError("INTENT_VERSION_CONFLICT")
    if row.action == "cancel" and status == "partially_filled":
        raise TradingError("INTENT_VERSION_CONFLICT")
    old_result = json.loads(row.result_json or "{}")
    new_result = json.loads(
        changes.get("result_json") or row.result_json or "{}"
    )
    previous, current = old_result.get("accFillSz"), new_result.get("accFillSz")
    if previous is not None and (
        current is None or Decimal(current) < Decimal(previous)
    ):
        raise TradingError("IDENTITY_MISMATCH")


class AgentTradeRepository:
    """每次调用独占短 session，返回提交后的 detached 行。"""

    def __init__(self, session_factory):
        self.session_factory = session_factory

    async def get_intent(self, intent_id, conversation_id=None):
        """按资源归属读取，prepared 过期会原子记录。"""
        async with self.session_factory() as session:
            query = select(AgentTradeIntent).where(
                AgentTradeIntent.id == intent_id
            )
            if conversation_id is not None:
                query = query.where(
                    AgentTradeIntent.conversation_id == conversation_id
                )
            row = await session.scalar(query)
        if (
            row
            and row.status == "prepared"
            and row.expires_ts <= int(time.time())
        ):
            try:
                return await self.transition(row.id, row.version, "expired")
            except TradingError:
                return await self.get_intent(intent_id, conversation_id)
        return row

    async def find_request(self, conversation_id, message_id, request_hash):
        """静态请求去重在行情读取前执行。"""
        async with self.session_factory() as session:
            row = await session.scalar(
                select(AgentTradeIntent).where(
                    AgentTradeIntent.conversation_id == conversation_id,
                    AgentTradeIntent.client_message_id == message_id,
                    AgentTradeIntent.request_hash == request_hash,
                )
            )
        return await self.get_intent(row.id) if row else None

    async def create_intent(self, values):
        """UNIQUE 去重优先于事务内额度；胜者 TTL 永不重置。"""
        values = dict(values)
        check_binding = values.pop("_check_binding", False)
        async with self.session_factory() as session:
            await session.execute(text("BEGIN IMMEDIATE"))
            if check_binding:
                await self._check_binding(session, values)
            existing = await session.scalar(
                select(AgentTradeIntent).where(
                    AgentTradeIntent.conversation_id
                    == values["conversation_id"],
                    AgentTradeIntent.client_message_id
                    == values["client_message_id"],
                    AgentTradeIntent.request_hash == values["request_hash"],
                )
            )
            if existing:
                if any(
                    getattr(existing, key) != values[key]
                    for key in (
                        "account_id",
                        "environment",
                        "binding_version",
                        "credential_fingerprint",
                    )
                ):
                    raise TradingError("BINDING_CHANGED")
                return existing
            await expire_previews(session, values["conversation_id"])
            count = await session.scalar(
                select(func.count())
                .select_from(AgentTradeIntent)
                .where(
                    AgentTradeIntent.conversation_id
                    == values["conversation_id"],
                    AgentTradeIntent.status == "prepared",
                )
            )
            if count >= 20:
                raise TradingError("TOO_MANY_TRADE_PREVIEWS")
            row = AgentTradeIntent(**values)
            session.add(row)
            add_event(session, row, "requested")
            await session.commit()
            return row

    async def _check_binding(self, session, values):
        binding = await session.get(
            AgentTradeBinding, values["conversation_id"]
        )
        if (
            not binding
            or binding.version != values["binding_version"]
            or binding.account_id != values["account_id"]
            or binding.environment != values["environment"]
            or binding.account_mode != "live"
        ):
            raise TradingError("BINDING_CHANGED")

    async def transition(
        self, intent_id, expected_version, status, changes=None
    ):
        """状态与对应事件原子提交；先 CAS 再允许外部副作用。"""
        return await self._change(
            intent_id, expected_version, status, changes or {}
        )

    async def touch(self, intent_id, expected_version, changes):
        """核对元数据也 CAS 增版本，但不产生无意义状态事件。"""
        return await self._change(intent_id, expected_version, None, changes)

    async def _change(self, intent_id, expected_version, status, changes):
        async with self.session_factory() as session:
            row = await session.get(AgentTradeIntent, intent_id)
            if not row or row.version != expected_version:
                raise TradingError("INTENT_VERSION_CONFLICT")
            target = status or row.status
            _validate_changes(row, target, changes)
            updates = dict(
                changes,
                status=target,
                version=expected_version + 1,
                updated_ts=int(time.time()),
            )
            result = await session.execute(
                update(AgentTradeIntent)
                .where(
                    AgentTradeIntent.id == intent_id,
                    AgentTradeIntent.version == expected_version,
                    AgentTradeIntent.status == row.status,
                )
                .values(**updates),
                execution_options={"synchronize_session": False},
            )
            if result.rowcount != 1:
                raise TradingError("INTENT_VERSION_CONFLICT")
            await session.refresh(row)
            if status is not None:
                add_event(
                    session,
                    row,
                    "dispatch_started"
                    if target == "submitting"
                    else target
                    if target in TERMINAL
                    else "reconciled",
                )
            await session.commit()
            return row

    async def list_unsettled(self):
        """返回全部未结行；数据库异常显式向准入层传播。"""
        async with self.session_factory() as session:
            return list(
                (
                    await session.scalars(
                        select(AgentTradeIntent)
                        .where(AgentTradeIntent.status.in_(UNSETTLED))
                        .order_by(
                            AgentTradeIntent.created_ts, AgentTradeIntent.id
                        )
                    )
                ).all()
            )

    async def list_intents(self, conversation_id=None, cursor=None, limit=20):
        """createdTs/id 稳定倒序游标；无会话时仅返回全局未结。"""
        query = select(AgentTradeIntent)
        if conversation_id is None:
            query = query.where(AgentTradeIntent.status.in_(UNSETTLED))
        else:
            query = query.where(
                AgentTradeIntent.conversation_id == conversation_id
            )
        if cursor:
            anchor = await self.get_intent(cursor, conversation_id)
            if not anchor:
                raise TradingError("INVALID_TRADING_REQUEST")
            query = query.where(
                (AgentTradeIntent.created_ts < anchor.created_ts)
                | (
                    (AgentTradeIntent.created_ts == anchor.created_ts)
                    & (AgentTradeIntent.id < anchor.id)
                )
            )
        async with self.session_factory() as session:
            rows = list(
                (
                    await session.scalars(
                        query.order_by(
                            AgentTradeIntent.created_ts.desc(),
                            AgentTradeIntent.id.desc(),
                        ).limit(limit + 1)
                    )
                ).all()
            )
        return rows[:limit], rows[limit - 1].id if len(rows) > limit else None

    async def recover_submitting(self):
        """启动时持久提交一律变未知；绝不恢复发送任务。"""
        count = 0
        for row in await self.list_unsettled():
            if row.status == "submitting":
                await self.transition(row.id, row.version, "unknown")
                count += 1
        return count
