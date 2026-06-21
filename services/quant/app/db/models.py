"""SQLAlchemy ORM 模型 (spec §10)。所有 ts/start_ts/end_ts 为秒级 UTC 整数 (C2)。"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Strategy(Base):
    """策略实例（只存"实例化覆盖项"，meta 的唯一真源在注册表，C6）。"""

    __tablename__ = "strategies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    template_key: Mapped[str] = mapped_column(String(64), index=True)  # 指向注册表 key
    name: Mapped[str] = mapped_column(String(128))
    params_json: Mapped[str] = mapped_column(Text, default="{}")        # 覆盖 default_params
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    builtin: Mapped[bool] = mapped_column(Boolean, default=False)       # 内置模板=只读，不可删
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)


class StrategyRun(Base):
    """一次回测 / 实时模拟运行。"""

    __tablename__ = "strategy_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    strategy_id: Mapped[int] = mapped_column(ForeignKey("strategies.id"), index=True)
    inst_id: Mapped[str] = mapped_column(String(32))
    bar: Mapped[str] = mapped_column(String(8))
    mode: Mapped[str] = mapped_column(String(16))      # backtest | paper
    status: Mapped[str] = mapped_column(String(16), default="running")  # running|done|stopped|error
    start_ts: Mapped[int] = mapped_column(Integer, default=0)
    end_ts: Mapped[int | None] = mapped_column(Integer, nullable=True)
    config_json: Mapped[str] = mapped_column(Text, default="{}")
    metrics_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    trades: Mapped[list["Trade"]] = relationship(back_populates="run", cascade="all, delete-orphan")
    equity_points: Mapped[list["EquityPoint"]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )


class Trade(Base):
    __tablename__ = "trades"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("strategy_runs.id"), index=True)
    ts: Mapped[int] = mapped_column(Integer)
    side: Mapped[str] = mapped_column(String(8))       # long | short | close
    price: Mapped[float] = mapped_column(Float)
    qty: Mapped[float] = mapped_column(Float)
    fee: Mapped[float] = mapped_column(Float)
    pnl: Mapped[float] = mapped_column(Float, default=0.0)   # 平仓净实现盈亏，开仓为 0
    position_after: Mapped[float] = mapped_column(Float, default=0.0)
    reason: Mapped[str] = mapped_column(String(64), default="")

    run: Mapped["StrategyRun"] = relationship(back_populates="trades")


class EquityPoint(Base):
    __tablename__ = "equity_points"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("strategy_runs.id"), index=True)
    ts: Mapped[int] = mapped_column(Integer)
    equity: Mapped[float] = mapped_column(Float)
    drawdown: Mapped[float] = mapped_column(Float, default=0.0)   # ≤ 0

    run: Mapped["StrategyRun"] = relationship(back_populates="equity_points")


class AgentMessage(Base):
    """对话式 AI 助手的持久化消息（可重开恢复会话）(Agent Trading Copilot §5.1)。

    `content_json` 存该条消息的归一化 block 序列（text / tool_use / tool_result），
    保留 `tool_use.id ↔ tool_result.toolUseId` 关联，使 resume / history 重放无损（P1-B）。
    仅落对话可读历史，不落 API Key、不落原始大体量 payload。
    """

    __tablename__ = "agent_messages"
    __table_args__ = (Index("ix_agent_conv", "conversation_id", "id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    conversation_id: Mapped[str] = mapped_column(String(64), index=True)
    role: Mapped[str] = mapped_column(String(16))           # user | assistant | tool
    content_json: Mapped[str] = mapped_column(Text, default="[]")
    created_ts: Mapped[int] = mapped_column(Integer, default=0)  # 秒级 UTC（全系统统一，C2）


class CandleCache(Base):
    """OKX K 线本地缓存（降低调用、加速回测）。"""

    __tablename__ = "candles_cache"
    __table_args__ = (
        UniqueConstraint("inst_id", "bar", "ts", name="uq_candle"),
        Index("ix_candle_lookup", "inst_id", "bar", "ts"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    inst_id: Mapped[str] = mapped_column(String(32))
    bar: Mapped[str] = mapped_column(String(8))
    ts: Mapped[int] = mapped_column(Integer)
    o: Mapped[float] = mapped_column(Float)
    h: Mapped[float] = mapped_column(Float)
    l: Mapped[float] = mapped_column(Float)
    c: Mapped[float] = mapped_column(Float)
    vol: Mapped[float] = mapped_column(Float)
