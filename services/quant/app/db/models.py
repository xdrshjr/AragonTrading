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
    # 自动交易分组索引（auto-trading-earnings-persistence §5.2）：按 group_id 聚合「一次 AI 交易」历史。
    __table_args__ = (Index("ix_strategy_runs_group", "group_id"),)

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
    # 自动交易分组归属（auto-trading-earnings-persistence §5.2，全部可空）：一次 live-auto start_group
    # 派生的 N 个成员 run 共享同一 group_id / group_started_ts / group_total_budget_usd；非 live-auto /
    # 老行为 NULL（历史列表对 NULL group_id 按单成员会话展示，不伪造分组）。老库经
    # _ensure_live_auto_group_columns 幂等补列（create_all 不给既有表补列）。
    group_id: Mapped[str | None] = mapped_column(String(48), nullable=True)
    group_started_ts: Mapped[int | None] = mapped_column(Integer, nullable=True)
    group_total_budget_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
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
    # 自动订单按成员最终预算折算的仓位比例（fraction，旧行 / 回测行为 NULL）。
    position_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    # 本持仓周期的峰值浮盈 USD（ai-trade-profit-protection-adaptive §2.4）：随**平仓**成交落一列，
    # 供「利润捕获率 = Σ已实现 / Σ峰值浮盈」聚合。保护层未装配 / 快照一路不可用 / 老行为 NULL——
    # **绝不填 0**（0 会被聚合当成「峰值就是 0」，把捕获率算成 0 或 ∞）。
    peak_pnl: Mapped[float | None] = mapped_column(Float, nullable=True)
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
    # [{factorKey,params,weight,direction,name?}]：``name`` 仅挖掘表达式因子（factorKey="expr"）
    # 携带，格式 ``expr:<hash8>``，由 ``factors/specs.py::validate_members`` 强制并透传，使
    # ``member_names`` 与表达式绑定而非退化为位置序（strategy-factor-discovery §2.2 / P0-3）。纯注释，零迁移。
    members_json: Mapped[str] = mapped_column(Text, default="[]")
    method: Mapped[str] = mapped_column(String(24), default="linear")  # equal|ic_weight|linear|random_forest|xgboost|extra_trees|mlp|kernel_ridge|knn|mutual_info|elastic_net（最长 kernel_ridge=12≤24，无迁移）
    config_json: Mapped[str] = mapped_column(Text, default="{}")      # 训练配置 + thresholds
    model_json: Mapped[str] = mapped_column(Text, default="{}")       # weights/importances/normStats/trainerVersion
    metrics_json: Mapped[str | None] = mapped_column(Text, nullable=True)  # 复合得分样本内外 IC
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)


class WeightModel(Base):
    """权重模型（权重训练与组合的一等实体）（weight-model-workspace §6.1）。

    承载「成员快照 + 方法 + 配置 + 当前最佳产物指针」，与 ``FactorSet`` **解耦但可弱关联**
    （``factor_set_id`` 仅溯源，不加外键）。``latest_run_id`` 用普通整数不加外键（规避 model↔run
    循环依赖，仿 ``LiveOrderAudit.account_id``）；剪枝 / 删除指向记录前须先 ``repoint_model`` 重算
    指针与 ``best_valid_ic``，绝不留悬挂指针（R-05）。``create_all`` 加法建表、零数据迁移。
    所有时间秒级 UTC 经 created/updated 落 ``DateTime``，与全系统统一（C2）。
    """

    __tablename__ = "weight_models"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128))
    inst_id: Mapped[str] = mapped_column(String(32))                  # 训练标的
    bar: Mapped[str] = mapped_column(String(8))                       # 周期
    members_json: Mapped[str] = mapped_column(Text, default="[]")     # 自包含成员快照 [{factorKey,params,weight,direction}]
    method: Mapped[str] = mapped_column(String(24), default="linear")  # equal|ic_weight|linear|random_forest|xgboost|extra_trees|mlp|kernel_ridge|knn|mutual_info|elastic_net（最长 kernel_ridge=12≤24，无迁移）
    config_json: Mapped[str] = mapped_column(Text, default="{}")      # {normWindow,clip,split,horizon,thresholds?...}
    horizon: Mapped[int] = mapped_column(Integer, default=6)          # 前瞻收益根数
    source: Mapped[str] = mapped_column(String(16), default="manual")  # builder | manual（来源标识）
    factor_set_id: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 弱关联来源组合 id（仅溯源，不加外键）
    latest_run_id: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 最近成功训练记录 id（普通整数、不加外键）
    best_valid_ic: Mapped[float | None] = mapped_column(Float, nullable=True)  # 反范式冗余：历史最佳验证 IC（列表排序用）
    notes: Mapped[str] = mapped_column(String(256), default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)


class WeightTrainBatch(Base):
    """一次多模型训练批次（多模型训练竞技场，weight-multi-model-training §5.1）。

    承载共享规格（成员 / 标的·周期 / 时间窗 / 前瞻 / 阈值）+ 批次父模型指针 + 用户显式择优记录。
    K 个候选训练记录（``WeightTrainRun.batch_id`` 指向本行、``model_id`` 指向 ``parent_model_id``）在同一份
    规格下并行训练。``parent_model_id`` 用普通整数不加外键（仿 ``WeightModel.latest_run_id``，级联删由
    service 单事务保证，绝不依赖 DB 级联）。``create_all`` 加法建表、零数据迁移。所有时间秒级 UTC（C2）。
    """

    __tablename__ = "weight_train_batches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    label: Mapped[str] = mapped_column(String(128))                   # 批次名（缺省 = 成员摘要 + "多模型对比"）
    inst_id: Mapped[str] = mapped_column(String(32))                  # 共享标的
    bar: Mapped[str] = mapped_column(String(8))                       # 共享周期
    start_ts: Mapped[int] = mapped_column(Integer, default=0)         # 共享训练窗口起
    end_ts: Mapped[int] = mapped_column(Integer, default=0)           # 共享训练窗口止
    horizon: Mapped[int] = mapped_column(Integer, default=6)          # 共享前瞻根数
    members_json: Mapped[str] = mapped_column(Text, default="[]")     # 共享成员快照（JSON）
    config_json: Mapped[str] = mapped_column(Text, default="{}")      # 共享配置（thresholds/normWindow 等，JSON）
    parent_model_id: Mapped[int] = mapped_column(Integer)             # 批次父模型 id（普通整数、不加外键，仿 latest_run_id）
    factor_set_id: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 弱溯源来源组合
    origin: Mapped[str] = mapped_column(String(16), default="model")  # builder|model（回流门禁用，缺省 model）
    selected_run_id: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 用户显式择优的候选 run（缺省 null，读取回落 best）
    # 因子组合探索训练（factor-combo-exploration §2.5/§5.1）：kind 区分多方法批次 / 探索批次；explore_plan_json
    # 存探索计划快照（strategy/params/methods/combos 展示元数据）。**注意**：``weight_train_batches`` 虽是既有表，
    # ``create_all`` 不给已存在的表补列——须靠 ``session.py::_ensure_weight_batch_columns`` 启动时幂等 ALTER 补齐
    # （仿 ``_ensure_weight_run_columns``），否则既有多模型库 INSERT/SELECT 立即 ``no such column``（P0/C-2）。
    kind: Mapped[str] = mapped_column(String(16), default="multi_method")  # multi_method | explore
    explore_plan_json: Mapped[str] = mapped_column(Text, default="{}")  # 探索计划快照（非探索批次为 "{}"）
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)


class WeightTrainRun(Base):
    """一次权重训练记录（状态机：queued→running→succeeded|failed|cancelled）（weight-model-workspace §6.2）。

    归属某 ``WeightModel``（``model_id`` 真外键，cascade delete 由 service 单事务保证）。承载训练窗口
    快照 + loss/IC 学习曲线 + 归一化权重 + 指标 + 溯源 + 失败 / 取消信息。每条 run 必达终态之一，含
    lifespan 启动对账（悬挂 ``running``/``queued`` → ``failed``，error_code=WEIGHT_TRAIN_INTERRUPTED，R-04）。

    多模型训练竞技场扩展（weight-multi-model-training §5.2）：``batch_id`` 归属批次（独立训练为 NULL、
    零影响既有行为），``queued`` = 已落库、等待名额、尚未启动作业。**注意**：``batch_id`` / ``variant_label``
    是**既有**表的新增列，``create_all`` 不给已存在的表补列——须靠 ``session.py::_ensure_weight_run_columns``
    启动时 ``ALTER TABLE ADD COLUMN`` 幂等补齐（仿 ``_ensure_trade_risk_columns``），否则老库 INSERT 立即
    ``OperationalError: no such column``（R1/P0）。
    """

    __tablename__ = "weight_train_runs"
    __table_args__ = (
        Index("ix_weight_run_model", "model_id", "id"),
        Index("ix_weight_run_batch", "batch_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    model_id: Mapped[int] = mapped_column(ForeignKey("weight_models.id"), index=True)
    batch_id: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 归属批次；独立训练为 NULL（既有行为零影响）
    # 候选自有组合成员快照（factor-combo-exploration §2.5/§5.1）：探索候选（成员=池子集）写各自组合；
    # NULL = 沿用父模型成员（既有单次训练 / 多方法批次全部为 NULL，向后兼容）。既有表新增列，须靠
    # ``_ensure_weight_run_columns`` 幂等 ALTER 补齐（否则老库 INSERT ``no such column``，P0/C-2）。
    members_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="running")  # queued|running|succeeded|failed|cancelled
    method: Mapped[str] = mapped_column(String(24))  # equal|ic_weight|linear|random_forest|xgboost|extra_trees|mlp|kernel_ridge|knn|mutual_info|elastic_net（最长 kernel_ridge=12≤24，无迁移）
    variant_label: Mapped[str] = mapped_column(String(64), default="")   # 同方法多超参变体的显示标签
    inst_id: Mapped[str] = mapped_column(String(32))
    bar: Mapped[str] = mapped_column(String(8))
    start_ts: Mapped[int] = mapped_column(Integer, default=0)
    end_ts: Mapped[int] = mapped_column(Integer, default=0)
    horizon: Mapped[int] = mapped_column(Integer, default=6)
    config_json: Mapped[str] = mapped_column(Text, default="{}")          # 训练配置快照
    curve_json: Mapped[str] = mapped_column(Text, default="[]")           # TrainCurvePoint[]（loss/IC 学习曲线）
    weights_json: Mapped[str] = mapped_column(Text, default="{}")         # 归一化权重 {name:weight}
    norm_stats_json: Mapped[str] = mapped_column(Text, default="{}")      # {normWindow,clip}（不存固定 mean/std）
    metrics_json: Mapped[str | None] = mapped_column(Text, nullable=True)  # {icIn,icOut,r2In,r2Out,nTrain,nValid}
    provenance_json: Mapped[str] = mapped_column(Text, default="{}")      # {instId,bar,startTs,endTs,horizon}
    trainer_version: Mapped[str | None] = mapped_column(String(64), nullable=True)  # 库版本 / builtin-* / None
    note: Mapped[str] = mapped_column(String(256), default="")
    error_code: Mapped[str] = mapped_column(String(64), default="")       # 失败时填，成功为空
    error_message: Mapped[str] = mapped_column(String(256), default="")
    started_ts: Mapped[int] = mapped_column(Integer, default=0)
    finished_ts: Mapped[int | None] = mapped_column(Integer, nullable=True)


class AppSetting(Base):
    """设置中心通用键值持久化（auto-trade-budget-and-allocation-settings §5.1）。

    单行一个标量 / 对象设置（``value`` 为 JSON 编码），如 ``auto_trade.max_budget_pct``。
    稀疏存储：无记录即默认（读取方负责回落默认值）。``create_all`` 自动建表、老库首次启动即补，
    无数据迁移。为设置中心后续扩展（分配模式默认、最大比例默认等）留口。
    """

    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)   # e.g. "auto_trade.max_budget_pct"
    value: Mapped[str] = mapped_column(Text, default="")             # JSON 编码标量 / 对象
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)


class DiscoveryRun(Base):
    """一次「策略与因子挖掘」任务（strategy-factor-discovery §5.1）。

    可 24 小时长跑：``checkpoint_json`` 存**小而有界**的断点状态（迭代数 / 预算 / 动量 /
    FSA 冻结 / 机制覆盖 / 数据窗口，目标 < 64 KB），已测哈希则落
    ``discovery_candidates.genome_hash`` 的唯一索引（D-3：十万级候选下 JSON 大对象既写不动
    也不原子）。``create_all`` 加法建表、**零数据迁移、不动任何既有表**（D-5）。
    所有时间秒级 UTC（C2）。
    """

    __tablename__ = "discovery_runs"
    __table_args__ = (Index("ix_discovery_runs_status", "status", "id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    label: Mapped[str] = mapped_column(String(128), default="")
    inst_id: Mapped[str] = mapped_column(String(32))
    bar: Mapped[str] = mapped_column(String(8))
    symbols_json: Mapped[str] = mapped_column(Text, default="[]")   # 单标的为 [instId]（多标的留口）
    status: Mapped[str] = mapped_column(String(16), default="running")  # running|paused|stopped|succeeded|failed
    # user | process_restart | disabled_by_config | data_unavailable | limit_reached | rebuild_failed
    pause_reason: Mapped[str] = mapped_column(String(64), default="")
    config_json: Mapped[str] = mapped_column(Text, default="{}")    # 归一化后的完整配置快照（回溯真源）
    checkpoint_json: Mapped[str] = mapped_column(Text, default="{}")  # §2.10 小状态（有界）
    counters_json: Mapped[str] = mapped_column(Text, default="{}")  # 冗余计数（列表页免解析大 JSON）
    iteration: Mapped[int] = mapped_column(Integer, default=0)
    kept_count: Mapped[int] = mapped_column(Integer, default=0)     # 反范式冗余（列表排序用）
    tested_count: Mapped[int] = mapped_column(Integer, default=0)
    started_ts: Mapped[int] = mapped_column(Integer, default=0)
    last_iter_ts: Mapped[int | None] = mapped_column(Integer, nullable=True)
    finished_ts: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_code: Mapped[str] = mapped_column(String(64), default="")
    error_message: Mapped[str] = mapped_column(String(256), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)


class DiscoveryIteration(Base):
    """单轮挖掘迭代记录（strategy-factor-discovery §5.2）——回溯「这一轮发生了什么」。

    保留策略：每 run 保留最近 ``discovery_max_iteration_rows`` 条，超出剪最旧，剪枝量记入
    run 的 ``counters_json.prunedIterations``（绝不静默截断）。
    """

    __tablename__ = "discovery_iterations"
    __table_args__ = (Index("ix_discovery_iter_run", "run_id", "idx"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("discovery_runs.id"), index=True)
    idx: Mapped[int] = mapped_column(Integer, default=0)            # 轮序号（自 1 起）
    started_ts: Mapped[int] = mapped_column(Integer, default=0)
    finished_ts: Mapped[int] = mapped_column(Integer, default=0)
    budget_json: Mapped[str] = mapped_column(Text, default="{}")    # 本轮五维预算（自适应后实际值）
    stats_json: Mapped[str] = mapped_column(Text, default="{}")     # 逐段计数 + 拒绝原因分布 + 来源分布
    llm_json: Mapped[str] = mapped_column(Text, default="{}")       # {calls,latencyMs,degraded,...}
    note: Mapped[str] = mapped_column(String(256), default="")      # 人类可读一句话


class DiscoveryCandidate(Base):
    """单个挖掘候选（strategy-factor-discovery §5.3）——已测集合与成果库的物理载体。

    ``UniqueConstraint(run_id, genome_hash)`` 是**去重的物理保证**（D-3）：续跑时一次
    ``SELECT genome_hash WHERE run_id=?`` 即可重建内存去重集。保留策略：全部 ``kept``
    永不剪，``rejected`` 每 run 保留最近 ``discovery_max_candidate_rows`` 条。
    """

    __tablename__ = "discovery_candidates"
    __table_args__ = (
        UniqueConstraint("run_id", "genome_hash", name="uq_discovery_candidate"),
        Index("ix_discovery_cand_run", "run_id", "verdict", "id"),
        Index("ix_discovery_cand_hash", "genome_hash"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("discovery_runs.id"), index=True)
    iteration_idx: Mapped[int] = mapped_column(Integer, default=0)
    kind: Mapped[str] = mapped_column(String(24), default="factor")  # factor|factor_combo|strategy_param|strategy_combo
    genome_hash: Mapped[str] = mapped_column(String(32))
    genome_json: Mapped[str] = mapped_column(Text, default="{}")
    expr_text: Mapped[str] = mapped_column(Text, default="")        # 规范式（因子轴）；策略轴为空
    describe_text: Mapped[str] = mapped_column(String(512), default="")   # 中文渲染（列表与 prompt 共用）
    skeleton: Mapped[str] = mapped_column(String(256), default="")  # FSA 键（因子轴）
    mechanism: Mapped[str] = mapped_column(String(48), default="")
    source: Mapped[str] = mapped_column(String(16), default="seed")  # mutate|crossover|perturb|random|llm|seed
    verdict: Mapped[str] = mapped_column(String(16), default="rejected")  # kept|rejected
    reject_reason: Mapped[str] = mapped_column(String(64), default="")    # 首个未通过条目
    metrics_json: Mapped[str] = mapped_column(Text, default="{}")   # 全量指标（IS/OOS 双份）
    series_json: Mapped[str] = mapped_column(Text, default="{}")    # 有界序列（分段 IC ≤24 / 分位 ≤10）
    trials_at_accept: Mapped[int] = mapped_column(Integer, default=0)     # 入库时累计试验数（多重检验回溯）
    demoted: Mapped[bool] = mapped_column(Boolean, default=False)   # 刷新后复评降级标记
    # 入库后的实体（factor|factor_set|strategy + id）；普通整数不加外键，仿 LiveOrderAudit.account_id
    promoted_kind: Mapped[str] = mapped_column(String(16), default="")
    promoted_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_ts: Mapped[int] = mapped_column(Integer, default=0)


class DiscoveryAsset(Base):
    """跨 run 归一的成果资产（discovery-asset-ledger §5.1）。

    资产 = 「一条被挖出来并通过全部硬闸的成果」的**语义身份**（``asset_key``，与
    ``promote.py`` 的幂等键同源，AL-4），而不是某个 run 的某一行候选。它**自持**
    genome / 冻结方向 / 挖掘窗口口径 / 登记时的完整指标与阈值，因此 run 被删掉之后详情、
    入库与持续体检全都照常可用（AL-11）。

    ``create_all`` 加法建表，**不动任何既有表、不加任何既有列**（D-5 延续）。
    所有 ``*_ts`` 秒级 UTC（C2）。
    """

    __tablename__ = "discovery_assets"
    __table_args__ = (
        Index("ix_discovery_assets_due", "archived", "next_check_ts"),
        Index("ix_discovery_assets_kind", "kind", "health_status", "id"),
        Index("ix_discovery_assets_market", "inst_id", "bar"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    asset_key: Mapped[str] = mapped_column(String(48), unique=True)   # §2.2.1 语义身份
    kind: Mapped[str] = mapped_column(String(24), default="factor")
    inst_id: Mapped[str] = mapped_column(String(32), default="")      # 资产自持（AL-12）
    bar: Mapped[str] = mapped_column(String(8), default="")
    horizon: Mapped[int] = mapped_column(Integer, default=0)          # 因子轴预测周期；策略轴 0
    direction: Mapped[str] = mapped_column(String(8), default="")     # 因子轴冻结方向（AL-1 输入）
    expr_text: Mapped[str] = mapped_column(Text, default="")
    genome_json: Mapped[str] = mapped_column(Text, default="{}")
    describe_text: Mapped[str] = mapped_column(String(512), default="")
    mechanism: Mapped[str] = mapped_column(String(48), default="")
    label: Mapped[str] = mapped_column(String(128), default="")
    first_found_ts: Mapped[int] = mapped_column(Integer, default=0)
    last_found_ts: Mapped[int] = mapped_column(Integer, default=0)
    source_count: Mapped[int] = mapped_column(Integer, default=0)     # 真实总次数（可 > 溯源条数）
    sources_json: Mapped[str] = mapped_column(Text, default="[]")     # ≤20 条（最早 1 + 最近 19）
    variant_count: Mapped[int] = mapped_column(Integer, default=1)    # 被合并的不同 genomeHash 数
    backfilled: Mapped[bool] = mapped_column(Boolean, default=False)  # 上线前一次性回填（§2.11）
    mined_window_json: Mapped[str] = mapped_column(Text, default="{}")
    accept_metrics_json: Mapped[str] = mapped_column(Text, default="{}")   # 判级分母（AL-3）
    accept_thresholds_json: Mapped[str] = mapped_column(Text, default="{}")
    trials_at_accept: Mapped[int] = mapped_column(Integer, default=0)
    promoted_kind: Mapped[str] = mapped_column(String(16), default="")
    promoted_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    companion_strategy_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # fresh|healthy|watch|decayed|unknown|error
    health_status: Mapped[str] = mapped_column(String(16), default="fresh")
    retention: Mapped[float | None] = mapped_column(Float, nullable=True)  # null 是合法值
    consecutive_fail: Mapped[int] = mapped_column(Integer, default=0)      # 迟滞计数（AL-8）
    last_reasons: Mapped[str] = mapped_column(String(64), default="")
    last_check_ts: Mapped[int] = mapped_column(Integer, default=0)
    next_check_ts: Mapped[int] = mapped_column(Integer, default=0)
    checks_count: Mapped[int] = mapped_column(Integer, default=0)
    checks_pruned: Mapped[int] = mapped_column(Integer, default=0)
    archived: Mapped[bool] = mapped_column(Boolean, default=False)    # 用户动作，与健康状态正交
    archived_ts: Mapped[int] = mapped_column(Integer, default=0)
    note: Mapped[str] = mapped_column(String(256), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)


class DiscoveryAssetCheck(Base):
    """一次持续样本外体检的快照（discovery-asset-ledger §5.2）。

    只在**资产自身**可判定或出错时才写一行；取数失败（网络抖动 / OKX 不可达）整组跳过、
    **不写**行——否则每次抖动都会在健康时间线上插一根尖刺，此后再也读不出真实趋势（AL-8）。
    """

    __tablename__ = "discovery_asset_checks"
    __table_args__ = (Index("ix_discovery_asset_checks", "asset_id", "checked_ts"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    asset_id: Mapped[int] = mapped_column(ForeignKey("discovery_assets.id"), index=True)
    checked_ts: Mapped[int] = mapped_column(Integer, default=0)
    window_start_ts: Mapped[int] = mapped_column(Integer, default=0)   # 口径落库（AL-2 可回溯）
    window_end_ts: Mapped[int] = mapped_column(Integer, default=0)
    fresh_start_ts: Mapped[int] = mapped_column(Integer, default=0)
    fresh_bars: Mapped[int] = mapped_column(Integer, default=0)
    warmup_bars: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(16), default="unknown")
    reasons: Mapped[str] = mapped_column(String(64), default="")       # 逗号分隔 H 码 / S0:*
    retention: Mapped[float | None] = mapped_column(Float, nullable=True)
    clip_mode: Mapped[str] = mapped_column(String(16), default="")     # ""|gated|rebased
    metrics_json: Mapped[str] = mapped_column(Text, default="{}")      # {"fresh":…,"history":…}
    error: Mapped[str] = mapped_column(String(256), default="")


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


class AiTradeGroupState(Base):
    """一次 AI 自动交易分组的**可续跑契约**（ai-trade-pause-resume §3.4 / §6.1）。

    为什么是新表而不是给 ``strategy_runs`` 加列：``create_all`` 不给既有表补列（见
    ``db/session.py`` 的四个 ``_ensure_*_columns`` 补丁与其成文教训），加列必须写 ALTER 补丁；
    而 ``create_all`` **会**建缺失的表 ⇒ 新表 = 零迁移代码、老库自动获得、不可能 no such column。

    为什么不复用 ``strategy_runs.config_json``：那份缺 ``accountId`` / ``stopLossPct`` /
    ``takeProfitPct`` / ``primingBars``（spec D-2），照它续跑会静默把用户设的 1% 止损换成服务端
    默认——一个不会报错的风控失真。故续跑的唯一真源是 ``start_wire_json``（启动请求原文）。

    只落**不可推导**的事实（§3.4.3）：派生量（已实现盈亏 / 持仓序号 / 开仓腿费）一律在续跑时
    从 ``trades`` 重算，两份必漂移，而这里漂移的是真实资金的盈亏显示。
    """

    __tablename__ = "ai_trade_group_states"
    __table_args__ = (Index("ix_ai_trade_group_states_status", "status"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    group_id: Mapped[str] = mapped_column(String(48), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(16), default="running")   # running|paused|ended
    account_id: Mapped[int] = mapped_column(Integer, default=0)
    bar: Mapped[str] = mapped_column(String(8), default="")
    start_wire_json: Mapped[str] = mapped_column(Text, default="{}")     # 启动请求原文（续跑唯一真源）
    resolved_echo_json: Mapped[str] = mapped_column(Text, default="{}")  # 落地回执快照（漂移对照）
    members_json: Mapped[str] = mapped_column(Text, default="[]")        # 形状见 spec §6.2
    pause_reason: Mapped[str | None] = mapped_column(String(32), nullable=True)
    paused_ts: Mapped[int | None] = mapped_column(Integer, nullable=True)
    resumed_count: Mapped[int] = mapped_column(Integer, default=0)
    last_seen_ts: Mapped[int] = mapped_column(Integer, default=0)        # 心跳（离线时长分母）
    created_ts: Mapped[int] = mapped_column(Integer, default=0)
    updated_ts: Mapped[int] = mapped_column(Integer, default=0)


class AiExperienceEvent(Base):
    """一笔已结算往返沉淀下来的**经验事件**（ai-trade-experience-context-graph §6.1）。

    这张表是整个特性的**唯一真源**：append-only、绝不改写。图（节点与边）全部由它物化派生，
    随时可以整表重算——图算错了不是数据丢了，重建即可。这一性质是本设计的可修复性来源。

    为什么是新表而不是给 ``trades`` 加列：``create_all`` **会**建缺失的表、**不会**给既有表补列
    （见 ``db/session.py`` 的四个 ``_ensure_*_columns`` 补丁）。新表 = 零迁移代码、老库首次启动
    自动获得、不可能 ``no such column``。

    ``origin`` 在 v1 恒为 ``"live"``（回测侧不写入，见 spec §十一.2）。保留它的理由不是「它已经
    有第二个取值」，而是**事后加列必须写 ALTER 补丁，而现在加它代价为零**：v2 开回测写入或续跑
    补采时免掉一次迁移。聚合层照写 ``WHERE origin='live'``，语义是口径闸。

    五个 bps 类读数从 ``outcome_json`` **提列**：聚合是热路径，逐行解 JSON 会让重建在几万条事件
    上肉眼可见地慢，而这五个量是全部统计模板的输入。
    """

    __tablename__ = "ai_experience_events"
    __table_args__ = (
        UniqueConstraint("group_id", "inst_id", "seq", "exit_ts", name="uq_ai_exp_event"),
        Index("ix_ai_exp_event_scope", "inst_id", "bar", "exit_ts"),
        Index("ix_ai_exp_event_group", "group_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    group_id: Mapped[str] = mapped_column(String(48), index=True, default="")
    run_id: Mapped[int] = mapped_column(Integer, index=True, default=0)
    inst_id: Mapped[str] = mapped_column(String(32), default="")
    bar: Mapped[str] = mapped_column(String(8), default="")
    seq: Mapped[int] = mapped_column(Integer, default=0)
    origin: Mapped[str] = mapped_column(String(8), default="live")   # live|backtest|adopted
    entry_ts: Mapped[int] = mapped_column(Integer, default=0)        # 秒级 UTC（bar 时钟，C2）
    exit_ts: Mapped[int] = mapped_column(Integer, default=0)
    entry_wall_ts: Mapped[int] = mapped_column(Integer, default=0)   # 墙钟（时间衰减用，双时钟不混用）
    exit_wall_ts: Mapped[int] = mapped_column(Integer, default=0)
    side: Mapped[str] = mapped_column(String(8), default="")
    context_json: Mapped[str] = mapped_column(Text, default="{}")    # 开仓瞬刻冻结的九轴签名
    outcome_json: Mapped[str] = mapped_column(Text, default="{}")    # SettledTrip 的 wire
    cost_basis: Mapped[str] = mapped_column(String(8), default="gross")   # net|gross（I-7 过滤依据）
    fee_known: Mapped[bool] = mapped_column(Boolean, default=False)
    net_bps: Mapped[float | None] = mapped_column(Float, nullable=True)
    gross_bps: Mapped[float | None] = mapped_column(Float, nullable=True)
    cost_bps: Mapped[float | None] = mapped_column(Float, nullable=True)
    edge_error_bps: Mapped[float | None] = mapped_column(Float, nullable=True)
    hold_seconds: Mapped[float] = mapped_column(Float, default=0.0)
    exit_source: Mapped[str] = mapped_column(String(16), default="unknown")
    created_ts: Mapped[int] = mapped_column(Integer, default=0)


class AiExperienceNode(Base):
    """物化图节点：语境节点（``axis:value``）或经验节点（``lesson:<12hex>``）。

    **物化是「按 key upsert」而不是「全量替换」**（spec §4.5 评审 P0-2）。六个字段一旦存在即绝不
    覆盖：``pinned`` / ``muted`` / ``mute_reason``（``user`` 来源的）/ ``first_ts`` / ``created_ts``，
    以及随 ``prev_sign`` / ``sign_streak`` 前滚的符号历史。前三个是**用户亲手按下的意志**——而
    ``DELETE`` 对 ``stat`` 节点回 409 时给出的可执行提示恰恰是「改用静音」；删表重建等于产品告诉
    用户的做法产品自己不兑现。后两个是 ``iron`` 档第三条门槛（最近两次物化符号一致）的唯一落点，
    没有它 ``iron`` 在结构上永不可达 ⇒ L0 恒空 ⇒ 整个特性「装配成功、日志全绿、一个字都不喂」。

    ``last_seen_ts`` 是该经验**最近一条支撑事件**的墙钟（与 ``stats_json.lastTs`` 同值同源），
    **不是**最近一次物化的时刻——后者由 ``updated_ts`` 承载。两者若被合成一个字段，检索的时间
    衰减因子会在每次重建后被拨回 now ⇒ 恒为 1 ⇒ 三年前的教训与昨天同权，且不会报错。
    """

    __tablename__ = "ai_experience_nodes"
    __table_args__ = (
        Index("ix_ai_exp_node_pick", "kind", "tier", "muted"),
        Index("ix_ai_exp_node_axis", "axis", "value"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(8), default="lesson")        # context|lesson
    key: Mapped[str] = mapped_column(String(96), unique=True, index=True)
    axis: Mapped[str | None] = mapped_column(String(24), nullable=True)   # 仅 context 节点
    value: Mapped[str | None] = mapped_column(String(64), nullable=True)
    label: Mapped[str] = mapped_column(String(200), default="")
    detail: Mapped[str] = mapped_column(Text, default="")
    source: Mapped[str] = mapped_column(String(12), default="stat")   # stat|narrative|manual|imported|context
    scope: Mapped[str] = mapped_column(String(8), default="symbol")   # symbol|global
    tier: Mapped[str] = mapped_column(String(12), default="candidate")
    pinned: Mapped[bool] = mapped_column(Boolean, default=False)
    muted: Mapped[bool] = mapped_column(Boolean, default=False)
    mute_reason: Mapped[str | None] = mapped_column(String(24), nullable=True)  # user|stale|flipped
    support: Mapped[int] = mapped_column(Integer, default=0)
    weight: Mapped[float] = mapped_column(Float, default=0.0)
    stats_json: Mapped[str] = mapped_column(Text, default="{}")
    signature_json: Mapped[str] = mapped_column(Text, default="{}")
    first_ts: Mapped[int] = mapped_column(Integer, default=0)
    last_seen_ts: Mapped[int] = mapped_column(Integer, default=0)
    prev_sign: Mapped[int] = mapped_column(Integer, default=0)      # -1|0|+1
    sign_streak: Mapped[int] = mapped_column(Integer, default=0)
    created_ts: Mapped[int] = mapped_column(Integer, default=0)
    updated_ts: Mapped[int] = mapped_column(Integer, default=0)


class AiExperienceEdge(Base):
    """物化图边：``applies_in`` / ``co_occurs`` / ``contradicts`` / ``refines``（§6.3）。

    ``co_occurs`` 无向但**存两条有向边**：读侧只做单向索引查找，省掉一次 UNION，代价是一行的
    存储与一次一致的写入（两条边由同一次物化写出，不存在半边状态）。
    """

    __tablename__ = "ai_experience_edges"
    __table_args__ = (
        UniqueConstraint("src_id", "dst_id", "rel", name="uq_ai_exp_edge"),
        Index("ix_ai_exp_edge_src", "src_id", "rel"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    src_id: Mapped[int] = mapped_column(ForeignKey("ai_experience_nodes.id"), index=True)
    dst_id: Mapped[int] = mapped_column(ForeignKey("ai_experience_nodes.id"), index=True)
    rel: Mapped[str] = mapped_column(String(16), default="applies_in")
    weight: Mapped[float] = mapped_column(Float, default=0.0)
    support: Mapped[int] = mapped_column(Integer, default=0)
    updated_ts: Mapped[int] = mapped_column(Integer, default=0)
