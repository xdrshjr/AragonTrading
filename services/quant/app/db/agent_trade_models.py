"""对话交易的新表；仅新增，不依赖全库外键或历史回填。"""

from sqlalchemy import (
    CheckConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from .models import Base


class AgentTradeBinding(Base):
    __tablename__ = "agent_trade_bindings"
    conversation_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    account_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    environment: Mapped[str] = mapped_column(String(8))
    account_mode: Mapped[str] = mapped_column(String(8), default="sim")
    version: Mapped[int] = mapped_column(Integer, default=1)
    updated_ts: Mapped[int] = mapped_column(Integer)
    __table_args__ = (
        CheckConstraint("environment IN ('demo','live')"),
        CheckConstraint("account_mode IN ('sim','live')"),
    )


class AgentTradeIntent(Base):
    __tablename__ = "agent_trade_intents"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(String(64), index=True)
    client_message_id: Mapped[str] = mapped_column(String(36))
    tool_use_id: Mapped[str] = mapped_column(Text)
    account_id: Mapped[int] = mapped_column(Integer)
    environment: Mapped[str] = mapped_column(String(8))
    binding_version: Mapped[int] = mapped_column(Integer)
    credential_fingerprint: Mapped[str] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(32), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    request_hash: Mapped[str] = mapped_column(String(64))
    preview_hash: Mapped[str] = mapped_column(String(64))
    request_json: Mapped[str] = mapped_column(Text)
    preview_json: Mapped[str] = mapped_column(Text)
    result_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    cl_ord_id: Mapped[str | None] = mapped_column(String(32), unique=True)
    target_ord_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_ts: Mapped[int] = mapped_column(Integer)
    expires_ts: Mapped[int] = mapped_column(Integer)
    updated_ts: Mapped[int] = mapped_column(Integer)
    next_check_ts: Mapped[int | None] = mapped_column(Integer, nullable=True)
    check_attempts: Mapped[int] = mapped_column(Integer, default=0)
    __table_args__ = (
        UniqueConstraint(
            "conversation_id", "client_message_id", "request_hash"
        ),
        Index(
            "ix_agent_intent_conversation",
            "conversation_id",
            "created_ts",
            "id",
        ),
        Index("ix_agent_intent_due", "status", "next_check_ts"),
        CheckConstraint("action IN ('open','close','cancel')"),
        CheckConstraint("environment IN ('demo','live')"),
        CheckConstraint(
            "status IN ('prepared','submitting','unknown','accepted',"
            "'partially_filled','filled','canceled','rejected',"
            "'expired','dismissed')"
        ),
    )


class AgentTradeEvent(Base):
    __tablename__ = "agent_trade_events"
    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )
    intent_id: Mapped[str] = mapped_column(String(36), index=True)
    intent_version: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(32))
    detail_json: Mapped[str] = mapped_column(Text, default="{}")
    created_ts: Mapped[int] = mapped_column(Integer)
    __table_args__ = (UniqueConstraint("intent_id", "intent_version"),)
