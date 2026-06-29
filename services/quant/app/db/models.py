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
    stop_loss_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    take_profit_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_hold_bars: Mapped[int | None] = mapped_column(Integer, nullable=True)
    stop_loss_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    take_profit_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    # 序列化的 TriggerExplain（auto-trade-explainability §5.1）：实盘自动交易逐笔触发解释，
    # 历史行 / 非自动交易行为 NULL。绝不含凭证。老库经 _ensure_trade_risk_columns 幂等补列。
    explain_json: Mapped[str | None] = mapped_column(Text, nullable=True)

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


class AgentProvider(Base):
    """AI 模型服务商配置（设置中心 · AI 模型服务）。

    每行是一个可被 Agent 使用的 LLM 接入目标。官方项（openai/anthropic/gemini/kimi）
    `kind="official"` 不可删但可编辑；自定义项 `kind="custom"` 可增删改。全表至多一行
    `is_default=True`（`set_default` 事务保证）。`api_key` 仅后端存，绝不出接口 / 不进日志（C7）。
    """

    __tablename__ = "agent_providers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    slug: Mapped[str] = mapped_column(String(64), unique=True, index=True)  # 官方为定值，自定义为 uuid
    kind: Mapped[str] = mapped_column(String(16))           # official | custom（deletable=custom）
    label: Mapped[str] = mapped_column(String(128))
    api_standard: Mapped[str] = mapped_column(String(16))   # anthropic | openai（决定走哪个 client）
    base_url: Mapped[str] = mapped_column(String(256))
    # APIKEY —— 仅后端存：列表 / 详情仅回 hasKey/keyPreview，绝不返回原文，绝不进日志（C7）。
    api_key: Mapped[str] = mapped_column(Text, default="")
    model: Mapped[str] = mapped_column(String(128))
    user_agent: Mapped[str] = mapped_column(String(128), default="claude-code/1.0")
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)


class AgentToolState(Base):
    """Agent 工具启用状态覆盖项（设置中心 · Agent 工具管理）。

    稀疏存储：仅落用户显式改过的工具；表中无该 name 即视为「启用」（默认放行）。
    name 与 ToolRegistry 工具名一一对应；非真源——工具是否存在以注册表为准（C6）。
    """

    __tablename__ = "agent_tool_states"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, index=True)  # = Tool.name
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)


class OkxAccount(Base):
    """实盘 OKX 账户凭证（设置中心 · 实盘账户）。

    每行一个真实 OKX API 凭证；全表至多一行 is_default=True（set_default 事务保证）。
    secret_key / passphrase 仅后端存，绝不出接口 / 不进日志（C7）；api_key 列表态仅回末 4 位掩码。
    """

    __tablename__ = "okx_accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    label: Mapped[str] = mapped_column(String(128))                 # 备注名，如 Dev-JR-US
    api_key: Mapped[str] = mapped_column(Text, default="")          # 列表回掩码
    secret_key: Mapped[str] = mapped_column(Text, default="")       # 绝不出接口
    passphrase: Mapped[str] = mapped_column(Text, default="")       # 绝不出接口
    ip_whitelist: Mapped[str] = mapped_column(String(512), default="")  # informational，逗号分隔
    permissions: Mapped[str] = mapped_column(String(128), default="")   # informational，如 读取/交易
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)


class LiveOrderAudit(Base):
    """实盘下单审计留痕（实盘交易模式 §5.1）。

    订单唯一真源在 OKX，本表仅记操作痕迹（对账 / 复盘 / 安全审计）。**绝不**存任何
    secret/passphrase/api_key，message 字段不落原始凭证（沿用 ``_redact`` 单点收口，C7）。
    所有时间秒级 UTC（C2）。``account_id`` 指向 ``okx_accounts.id`` 但不加外键——账户可删，留痕保留。
    """

    __tablename__ = "live_order_audit"
    __table_args__ = (Index("ix_live_audit_account", "account_id", "id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    account_id: Mapped[int] = mapped_column(Integer, index=True)
    inst_id: Mapped[str] = mapped_column(String(32))
    direction: Mapped[str] = mapped_column(String(8))    # long | short
    ord_type: Mapped[str] = mapped_column(String(8))     # limit | market
    td_mode: Mapped[str] = mapped_column(String(8))      # cross | isolated
    px: Mapped[float | None] = mapped_column(Float, nullable=True)
    sz: Mapped[float] = mapped_column(Float)
    lever: Mapped[float | None] = mapped_column(Float, nullable=True)
    tp_trigger_px: Mapped[float | None] = mapped_column(Float, nullable=True)
    sl_trigger_px: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(16))      # accepted | rejected | error
    cl_ord_id: Mapped[str] = mapped_column(String(32), default="")  # 本地生成唯一客户端单号（C1-2）
    ord_id: Mapped[str] = mapped_column(String(64), default="")     # 成功填 OKX ordId，否则空
    error_code: Mapped[str] = mapped_column(String(64), default="")  # 失败填归一码，否则空
    created_ts: Mapped[int] = mapped_column(Integer, default=0)


class Factor(Base):
    """单个因子定义实例（因子层 §5.1）。

    ``factor_key`` 指向 ``FACTOR_LIBRARY``（非真源——库才是真源 C6）；``params_json`` 覆盖库默认参数。
    对应需求「看到所有挖掘出的因子、编辑、激活取消」：``enabled`` 即激活态，``source`` 区分内置 /
    挖掘 / 自定义，``eval_json`` 缓存最近一次评估摘要。所有时间秒级 UTC（C2）。
    """

    __tablename__ = "factors"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    factor_key: Mapped[str] = mapped_column(String(64), index=True)   # 指向 FACTOR_LIBRARY
    name: Mapped[str] = mapped_column(String(128))
    params_json: Mapped[str] = mapped_column(Text, default="{}")      # 覆盖库默认参数
    direction: Mapped[str] = mapped_column(String(8), default="auto")  # long | short | auto
    category: Mapped[str] = mapped_column(String(32), default="")     # 冗余存，便于列表过滤
    source: Mapped[str] = mapped_column(String(16), default="custom")  # builtin | mined | custom
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)      # 激活 / 取消（需求显式）
    eval_json: Mapped[str | None] = mapped_column(Text, nullable=True)  # 最近评估摘要缓存
    notes: Mapped[str] = mapped_column(String(256), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)


class FactorSet(Base):
    """命名的因子组合（含训练出的权重 / 重要度 + 归一化统计 + 复合 IC）（因子层 §5.1）。

    ``members_json`` 采用**自包含快照**（与 ``ComboMemberIn`` 同决策：不引用 ``factors.id``，避免因子被删 /
    改后组合悬空，R5）。可被 ``FactorScoreStrategy`` 在创建时内联快照进 ``params_json``（引擎零 IO）。
    """

    __tablename__ = "factor_sets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128))
    members_json: Mapped[str] = mapped_column(Text, default="[]")     # [{factorKey,params,weight,direction}]
    method: Mapped[str] = mapped_column(String(24), default="linear")  # equal|ic_weight|linear|random_forest|xgboost
    config_json: Mapped[str] = mapped_column(Text, default="{}")      # 训练配置 + thresholds
    model_json: Mapped[str] = mapped_column(Text, default="{}")       # weights/importances/normStats/trainerVersion
    metrics_json: Mapped[str | None] = mapped_column(Text, nullable=True)  # 复合得分样本内外 IC
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)


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
