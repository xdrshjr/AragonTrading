"""持久绑定及每轮冻结身份，凭据只在服务内短暂使用。"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass

from sqlalchemy import delete, select, text

from ..config import get_settings
from ..db.agent_trade_models import AgentTradeBinding, AgentTradeIntent
from ..db.agent_trade_repositories import add_event
from ..db.models import AgentMessage, OkxAccount
from ..db.session import get_sessionmaker
from ..okx.accounts import AccountCredentials
from .errors import TradingError
from .schemas import AgentCallContext, BindingRequest, parse_request


def current_environment():
    """环境与 OKX 客户端共用同一启动配置。"""
    return "demo" if get_settings().okx_account_demo else "live"


@dataclass(frozen=True)
class AccountIdentity:
    """内部账户快照；凭据和指纹禁止传到模型或浏览器。"""

    account_id: int
    label: str
    environment: str
    credential_fingerprint: str
    credentials: AccountCredentials


async def resolve_account(account_id, session_factory=None):
    """解析指定账户；不自动回退默认账户。"""
    factory = session_factory or get_sessionmaker()
    async with factory() as session:
        row = await session.get(OkxAccount, account_id)
    if not row:
        raise TradingError("ACCOUNT_NOT_FOUND")
    parts = (row.api_key, row.secret_key, row.passphrase)
    if not all(part and part.strip() for part in parts):
        raise TradingError("ACCOUNT_NOT_CONFIGURED")
    fingerprint = hashlib.sha256("\x00".join(parts).encode()).hexdigest()
    return AccountIdentity(
        row.id,
        row.label,
        current_environment(),
        fingerprint,
        AccountCredentials(*parts),
    )


def binding_view(row):
    """绑定的固定公开白名单，不包含账户秘密。"""
    if row is None:
        return None
    return {
        "conversationId": row.conversation_id,
        "accountId": row.account_id,
        "environment": row.environment,
        "accountMode": row.account_mode,
        "version": row.version,
        "updatedTs": row.updated_ts,
    }


async def _reject_submitting(session, cid):
    row = await session.scalar(
        select(AgentTradeIntent.id)
        .where(
            AgentTradeIntent.conversation_id == cid,
            AgentTradeIntent.status == "submitting",
        )
        .limit(1)
    )
    if row:
        raise TradingError("TRADE_INTENT_IN_FLIGHT")


async def _dismiss_prepared(session, cid):
    rows = await session.scalars(
        select(AgentTradeIntent).where(
            AgentTradeIntent.conversation_id == cid,
            AgentTradeIntent.status == "prepared",
        )
    )
    for row in rows.all():
        row.status = "dismissed"
        row.version += 1
        row.updated_ts = int(time.time())
        add_event(session, row, "dismissed")


class BindingService:
    """绑定更新与发送共用准入锁，防止校验后切换身份。"""

    def __init__(self, session_factory=None):
        self.session_factory = session_factory or get_sessionmaker()

    async def get(self, conversation_id):
        """返回绑定行或 null。"""
        async with self.session_factory() as session:
            return await session.get(AgentTradeBinding, conversation_id)

    async def put(self, conversation_id, params):
        """expectedVersion CAS；相同值不增加版本。"""
        from ..ai_auto.admission import get_admission_lock

        request = parse_request(BindingRequest, params)
        if request.accountId is not None:
            await resolve_account(request.accountId, self.session_factory)
        async with get_admission_lock():
            async with self.session_factory() as session:
                await session.execute(text("BEGIN IMMEDIATE"))
                await _reject_submitting(session, conversation_id)
                row = await session.get(AgentTradeBinding, conversation_id)
                if (row.version if row else 0) != request.expectedVersion:
                    raise TradingError("BINDING_CHANGED")
                target = (
                    request.accountId,
                    request.accountMode,
                    current_environment(),
                )
                if (
                    row
                    and (row.account_id, row.account_mode, row.environment)
                    == target
                ):
                    return row
                if row is None:
                    row = AgentTradeBinding(
                        conversation_id=conversation_id, version=0
                    )
                    session.add(row)
                row.account_id, row.account_mode, row.environment = target
                row.version += 1
                row.updated_ts = int(time.time())
                await _dismiss_prepared(session, conversation_id)
                await session.commit()
                return row


async def freeze_call(session_factory, cid, message_id, account_mode):
    """在消息接收时冻结；没有绑定的轮次不会后来自动获得绑定。"""
    row = await BindingService(session_factory).get(cid)
    identity = None
    if row and row.account_id:
        try:
            identity = await resolve_account(row.account_id, session_factory)
        except TradingError:
            # 普通对话不依赖账户可用；账户工具仍会按冻结标识明确拒绝。
            pass
    return AgentCallContext(
        conversation_id=cid,
        client_message_id=message_id,
        account_id=row.account_id if row else None,
        environment=row.environment if row else None,
        binding_version=row.version if row else 0,
        account_mode=account_mode
        if row and account_mode == row.account_mode
        else None,
        credential_fingerprint=identity.credential_fingerprint
        if identity
        else None,
    )


async def validate_call(call, session_factory=None):
    """重新比较冻结版本、环境和凭据，失败绝不换用新账户。"""
    if call is None or call.account_id is None:
        raise TradingError("ACCOUNT_REQUIRED")
    row = await BindingService(session_factory).get(call.conversation_id)
    if (
        not row
        or row.version != call.binding_version
        or row.account_id != call.account_id
        or row.environment != call.environment
        or (
            call.account_mode is not None
            and row.account_mode != call.account_mode
        )
    ):
        raise TradingError("BINDING_CHANGED")
    if current_environment() != call.environment:
        raise TradingError("ENVIRONMENT_CHANGED")
    identity = await resolve_account(call.account_id, session_factory)
    if identity.credential_fingerprint != call.credential_fingerprint:
        raise TradingError("ACCOUNT_CREDENTIALS_CHANGED")
    return identity


async def clear_conversation(session_factory, cid):
    """同锁同事务清消息、提高绑定版本及废弃预览；保留全部交易审计。"""
    from ..ai_auto.admission import get_admission_lock

    async with get_admission_lock():
        async with session_factory() as session:
            await session.execute(text("BEGIN IMMEDIATE"))
            await _reject_submitting(session, cid)
            row = await session.get(AgentTradeBinding, cid)
            if row is None:
                row = AgentTradeBinding(
                    conversation_id=cid,
                    version=0,
                    account_id=None,
                    environment=current_environment(),
                    account_mode="sim",
                )
                session.add(row)
            row.version += 1
            row.updated_ts = int(time.time())
            await _dismiss_prepared(session, cid)
            deleted = await session.execute(
                delete(AgentMessage).where(AgentMessage.conversation_id == cid)
            )
            await session.commit()
            return deleted.rowcount
