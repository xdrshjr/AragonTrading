"""Pydantic 请求 / 响应模型 (spec §9)。字段用 camelCase 与前端 TS 类型对齐。"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from .db.models import (
    AgentProvider,
    EquityPoint,
    Factor,
    FactorSet,
    OkxAccount,
    StrategyRun,
    Trade,
    WeightModel,
    WeightTrainBatch,
    WeightTrainRun,
)
from .engine.metrics import _empty_metrics
from .paper.session import PAPER_WINDOW_MAX


# ---------------- Requests ----------------
class CreateStrategyReq(BaseModel):
    templateKey: str
    name: str
    params: dict[str, Any] = Field(default_factory=dict)


class UpdateStrategyReq(BaseModel):
    name: str | None = None
    params: dict[str, Any] | None = None
    enabled: bool | None = None


class BacktestReq(BaseModel):
    instId: str
    bar: str = "1H"
    # （v2·P1-1）不再用作覆盖上限：全区间分页保证 [startTs,endTs] 全覆盖；若显式给出仅作
    # max_bars 安全上限覆盖（仍被 settings.backtest_max_bars 二次夹取）。默认 None、解除 1000 上界。
    limit: int | None = Field(default=None, ge=10, le=200000)
    startTs: int = Field(ge=0)
    endTs: int = Field(ge=0)
    initialCapital: float | None = None
    fees: float | None = None        # taker_fee_bps
    slippage: float | None = None    # slippage_bps
    leverage: float | None = None
    # 止盈止损一次性覆盖（backtest-stop-loss-take-profit §4.1）：小数契约（0.03=3%），留空=不覆盖
    # 策略自带风控。边界与前端 validateConfig 百分数边界互为镜像（SL (0,100)%⇔(0,1)；TP (0,1000)%⇔(0,10)）。
    stopLossPct: float | None = Field(default=None, gt=0, lt=1)
    takeProfitPct: float | None = Field(default=None, gt=0, lt=10)

    @model_validator(mode="after")
    def validate_time_range(self) -> "BacktestReq":
        if self.startTs >= self.endTs:
            raise ValueError("startTs must be less than endTs")
        return self


class CompareReq(BaseModel):
    # 2..12 个策略横向对比（去重后逐一回测，复用同一批 candles，R1）
    strategyIds: list[int] = Field(min_length=2, max_length=12)
    instId: str
    bar: str = "1H"
    limit: int | None = Field(default=None, ge=10, le=200000)   # 对齐 BacktestReq（全区间覆盖，v2·P1-1）
    startTs: int = Field(ge=0)
    endTs: int = Field(ge=0)
    initialCapital: float | None = None
    fees: float | None = None        # taker_fee_bps
    slippage: float | None = None    # slippage_bps
    leverage: float | None = None

    @model_validator(mode="after")
    def validate_time_range(self) -> "CompareReq":
        if self.startTs >= self.endTs:
            raise ValueError("startTs must be less than endTs")
        return self


class MultiSampleBacktestReq(BaseModel):
    # 单策略 × 多标的（多样本回测）：1..20 个标的，服务端去重后逐一复用 run_single_backtest，
    # per-symbol 错误隔离。字段用字面 camelCase 对齐 BacktestReq/CompareReq（不加 alias_generator）。
    instIds: list[str] = Field(min_length=1, max_length=20)
    bar: str = "1H"
    limit: int | None = Field(default=None, ge=10, le=200000)   # 同 BacktestReq（仅作 max_bars 安全上限）
    startTs: int = Field(ge=0)
    endTs: int = Field(ge=0)
    initialCapital: float | None = None
    fees: float | None = None        # taker_fee_bps
    slippage: float | None = None    # slippage_bps
    leverage: float | None = None

    @model_validator(mode="after")
    def validate_time_range(self) -> "MultiSampleBacktestReq":
        if self.startTs >= self.endTs:
            raise ValueError("startTs must be less than endTs")
        return self


class ComboMemberIn(BaseModel):
    """组合成员入参：自包含快照 {templateKey, params}（不引用其它策略行主键）。"""

    templateKey: str
    params: dict[str, Any] = Field(default_factory=dict)
    name: str | None = None


class ComboPolicyIn(BaseModel):
    """轮动策略入参（camelCase 与前端对齐；缺省即模板默认）。"""

    rebalanceBars: int = Field(default=24, ge=4, le=240)
    lookbackBars: int = Field(default=168, ge=24, le=1000)
    selectionMetric: Literal["return", "sharpe", "calmar"] = "sharpe"
    minDwellBars: int = Field(default=24, ge=1, le=240)
    switchMarginPct: float = Field(default=0.10, ge=0.0, le=1.0)
    regimeGating: bool = True
    turnoverBps: float = Field(default=5.0, ge=0.0, le=50.0)


class ComboPreviewReq(BaseModel):
    """POST /strategies/combo/preview：即时回测一个（未保存）组合。"""

    members: list[ComboMemberIn] = Field(min_length=2, max_length=8)  # 2..8 个成员
    policy: ComboPolicyIn | None = None                               # 缺省用模板默认
    instId: str
    bar: str = "1H"
    limit: int | None = Field(default=None, ge=10, le=200000)   # 同 BacktestReq（全区间覆盖，v2·P1-1）
    startTs: int = Field(ge=0)
    endTs: int = Field(ge=0)
    initialCapital: float | None = None
    fees: float | None = None        # taker_fee_bps
    slippage: float | None = None    # slippage_bps
    leverage: float | None = None

    @model_validator(mode="after")
    def validate_time_range(self) -> "ComboPreviewReq":
        if self.startTs >= self.endTs:
            raise ValueError("startTs must be less than endTs")
        return self


class WalkForwardReq(BaseModel):
    """POST /strategies/{id}/walk-forward：滑动窗口（Walk-forward）高级回测入参。

    原子策略和保存后的组合策略 id 均使用同一请求 / 响应形状。`windowBars` 下界 100
    （v2·P1-3：须显著大于策略 warmup，防整窗口零成交）；`stepBars <= windowBars`
    （避免出现未覆盖空档）。
    """

    instId: str
    bar: str = "1H"
    startTs: int = Field(ge=0)
    endTs: int = Field(ge=0)
    windowBars: int = Field(ge=100, le=5000)   # 窗口长度（根数），下界 50→100（v2·P1-3）
    stepBars: int = Field(ge=10, le=5000)      # 滑动步长（根数）
    resetCapital: bool = True                  # 每个窗口是否重置本金，默认 true（鲁棒性测试语义）
    initialCapital: float | None = None
    fees: float | None = None        # taker_fee_bps
    slippage: float | None = None    # slippage_bps
    leverage: float | None = None
    maxBars: int | None = Field(default=None, ge=100, le=200000)  # 覆盖 backtest_max_bars（仍被其二次夹取）

    @model_validator(mode="after")
    def validate_walk_forward(self) -> "WalkForwardReq":
        if self.startTs >= self.endTs:
            raise ValueError("startTs must be less than endTs")
        if self.stepBars > self.windowBars:
            raise ValueError("stepBars must be <= windowBars")
        return self


class PaperStartReq(BaseModel):
    strategyId: int
    instId: str
    bar: str = "1H"
    initialCapital: float | None = None
    # 数据来源：live=实时 WS；replay=真实历史加速回放（C2，"模拟快速交易"可演示）
    source: Literal["live", "replay"] = "live"
    speed: float = Field(default=1.0, gt=0, le=64)        # 回放倍速；gt=0 杜绝除零
    replayBars: int = Field(default=300, ge=10, le=PAPER_WINDOW_MAX)   # 回放根数
    # 预热回放（warm-start priming，B1）：live 启动时是否对近端真实历史做因果微回测建仓。
    priming: bool = True
    primingBars: int = Field(default=300, ge=10, le=PAPER_WINDOW_MAX)  # 预热回看根数


class PaperBatchStartReq(BaseModel):
    strategyIds: list[int] = Field(min_length=1, max_length=12)
    instId: str
    bar: str = "1H"
    initialCapital: float | None = None
    source: Literal["live", "replay"] = "live"
    speed: float = Field(default=1.0, gt=0, le=64)
    replayBars: int = Field(default=300, ge=10, le=PAPER_WINDOW_MAX)
    priming: bool = True
    primingBars: int = Field(default=300, ge=10, le=PAPER_WINDOW_MAX)


class PaperBatchSessionOut(BaseModel):
    strategyId: int
    name: str
    strategyKind: Literal["atomic", "combo"] | None = None
    sessionId: str
    runId: int
    state: str


class PaperBatchStartOut(BaseModel):
    groupId: str
    instId: str
    bar: str
    sessions: list[PaperBatchSessionOut]


class PaperBatchStopRunOut(BaseModel):
    strategyId: int
    sessionId: str
    run: dict[str, Any]


class PaperBatchStopOut(BaseModel):
    groupId: str
    runs: list[PaperBatchStopRunOut]


# ---------------- 实时自动交易（live-auto-trading §5.1，camelCase wire）----------------
class LiveAutoStartIn(BaseModel):
    """启动实时自动交易请求（全部金额字段可选，留空→服务端保守默认，§4.1）。"""

    strategyId: int
    instId: str = Field(min_length=1, max_length=32)
    bar: str = "1H"
    accountId: int | None = None                              # 省略=默认实盘账户
    capitalUsd: float | None = Field(default=None, gt=0)      # 预算（USDT），留空→ availUsdt×默认 frac
    leverage: float | None = Field(default=None, gt=0, le=125)
    takeProfitUsd: float | None = Field(default=None, gt=0)   # 会话级止盈金额阈值；留空→不触发
    stopLossUsd: float | None = Field(default=None, gt=0)     # 会话级止损金额阈值；留空→不触发
    priming: bool = True
    primingBars: int = Field(default=300, ge=10, le=PAPER_WINDOW_MAX)


class LiveAutoStartOut(BaseModel):
    sessionId: str
    runId: int
    strategyKind: str
    instId: str
    bar: str
    budgetUsd: float           # 夹紧后的最终生效预算（可能 < 用户填值，R4）
    leverage: float


class LiveAutoSessionRow(BaseModel):
    sessionId: str
    runId: int
    state: str
    strategyId: int
    strategyName: str
    instId: str
    bar: str
    budgetUsd: float
    leverage: float
    takeProfitUsd: float | None
    stopLossUsd: float | None
    positionSide: str
    sessionPnlUsd: float
    startedTs: int
    # 加固增量（live-auto-trading-hardening H2/H3/H4）：供重载后即时呈现降级 / 平仓失败态（可选，向后兼容）。
    degraded: bool | None = None
    flattenFailed: bool | None = None


class LiveAutoStatusOut(BaseModel):
    session: LiveAutoSessionRow | None
    liveAutoTradingEnabled: bool


# ---------------- 自选自动交易分组（watchlist-auto-trade-group §五，camelCase wire）----------------
class LiveAutoGroupMemberIn(BaseModel):
    """分组成员入参：标的 + 该标的可用仓位百分比（总仓位之下，§2.3）。"""

    instId: str = Field(min_length=1, max_length=32)
    allocationPct: float = Field(gt=0, le=100)   # (0, 100]，整组 Σ ≤ 100（validator 校验）


class LiveAutoGroupStartIn(BaseModel):
    """启动多标的自动交易分组请求（金额字段可留空→服务端保守默认，§4.1 / §4.2）。"""

    strategyId: int
    bar: str = "1H"
    members: list[LiveAutoGroupMemberIn]
    accountId: int | None = None                              # 省略=默认实盘账户
    # 留空→服务端按 DB maxBudgetPct 派生默认（默认 100% = 全部可用余额，不随成员数缩放）。
    totalCapitalUsd: float | None = Field(default=None, gt=0)
    leverage: float | None = Field(default=None, gt=0, le=125)
    takeProfitUsd: float | None = Field(default=None, gt=0)   # 分组总止盈；按 pct/Σpct 归一分摊（P1-2）
    stopLossUsd: float | None = Field(default=None, gt=0)     # 分组总止损；同上
    # 分配模式（auto-trade-budget-and-allocation-settings §2.3）：even=份额（Σ≤100）；
    # max_ratio=各标的对总预算的独立上限（Σ 可超 100）。省略=「even」（向后兼容）。
    allocationMode: Literal["even", "max_ratio"] = "even"
    priming: bool = True
    primingBars: int = Field(default=300, ge=10, le=PAPER_WINDOW_MAX)

    @field_validator("members")
    @classmethod
    def _check_members(cls, members: list[LiveAutoGroupMemberIn]) -> list[LiveAutoGroupMemberIn]:
        """边界校验（§5.1）：非空、instId 唯一（Σ 约束按 allocationMode 在 model_validator 决定）。"""
        if not members:
            raise ValueError("members 不可为空")
        inst_ids = [m.instId for m in members]
        if len(set(inst_ids)) != len(inst_ids):
            raise ValueError("members 含重复 instId")
        return members

    @model_validator(mode="after")
    def _check_allocation_sum(self) -> "LiveAutoGroupStartIn":
        """even 强制 Σ allocationPct ≤ 100；max_ratio 跳过 Σ 约束（各标的独立上限，可超 100%）。"""
        if (self.allocationMode == "even"
                and sum(m.allocationPct for m in self.members) > 100 + 1e-9):
            raise ValueError("各标的可用仓位百分比之和不可超过 100%")
        return self


class AutoTradeSettingsIn(BaseModel):
    """PUT /api/settings/auto-trade：总预算上限百分比（0<pct≤100，越界 422）。"""

    maxBudgetPct: float = Field(gt=0, le=100)


class AutoTradeSettingsOut(BaseModel):
    """GET/PUT /api/settings/auto-trade 响应（§4.1）。

    ``effectiveMaxBudgetPct`` 由路由按 ``settings.live_auto_group_max_capital_frac`` 计算注入
    （= min(maxBudgetPct, 天花板×100)，RN-2 预览口径），不入库。
    """

    maxBudgetPct: float
    effectiveMaxBudgetPct: float


# ---------------- AI 自动交易（ai-auto-trading §4 / §5，camelCase wire）----------------
AiExecutionBar = Literal["1m", "5m", "15m", "30m", "1H", "4H", "1D"]


class AiTradeStartIn(BaseModel):
    """启动 AI 自动交易请求（金额/杠杆字段可留空→服务端保守默认，§4.1）。"""

    instId: str = Field(min_length=1, max_length=32)
    bar: AiExecutionBar = "1H"
    strategyIds: list[int] = Field(min_length=1)         # 多选；上限按 ai_trade_max_strategies 服务端校验
    accountId: int | None = None                         # 省略=默认实盘账户
    capitalUsd: float | None = Field(default=None, gt=0)  # 预算(USDT)，留空→ availUsdt×默认 frac
    leverage: float | None = Field(default=None, gt=0, le=125)
    takeProfitUsd: float | None = Field(default=None, gt=0)  # 会话级止盈金额阈值；留空→不触发
    stopLossUsd: float | None = Field(default=None, gt=0)    # 会话级止损金额阈值；留空→不触发
    stopLossPct: float | None = Field(default=None, gt=0)    # E4：单仓 OCO 止损%（服务端夹紧到 max）
    takeProfitPct: float | None = Field(default=None, gt=0)  # E4：单仓 OCO 止盈%（服务端夹紧到 max）
    contextRefreshRounds: int = Field(default=10, ge=1)   # 精确区间在服务端按 min/max 校验
    priming: bool = True
    primingBars: int = Field(default=300, ge=10, le=PAPER_WINDOW_MAX)


class AiTradeStartOut(BaseModel):
    sessionId: str
    runId: int
    instId: str
    bar: str
    strategyIds: list[int]
    strategyNames: list[str]
    budgetUsd: float           # 夹紧后的最终生效预算（可能 < 用户填值）
    leverage: float
    contextRefreshRounds: int
    stopLossPct: float | None = None    # E4：executor 生效止损%（服务端夹紧后回执）
    takeProfitPct: float | None = None  # E4：executor 生效止盈%


class StrategySignalViewOut(BaseModel):
    strategyId: int
    name: str
    stance: str                # long|short|flat（影子 broker 持久立场，P0-1）
    latestSignal: str          # open_long|open_short|close|none（本根边沿）
    strength: float
    reason: str


class AiTradeRestingOrderOut(BaseModel):
    """当前挂着的限价委托（ai-trade-limit-orders §4.2；无挂单时整段为 None）。"""

    intent: str                  # open | close
    side: str                    # long | short
    px: float                    # 委托价（USDT）
    sz: float                    # 张数
    ageS: int                    # 已挂秒数
    expiresInS: int              # 距 TTL 自动撤销的剩余秒数
    filledSz: float = 0.0        # 已成交（已采纳）张数；未成交为 0


class AiTradeDecisionOut(BaseModel):
    ts: int
    round: int
    action: str                # open_long|open_short|close|hold|cancel_order
    sizeFraction: float | None = None
    confidence: float | None = None
    orderType: str | None = None    # market|limit（ai-trade-limit-orders §2.2；总闸关时恒 market）
    limitPrice: float | None = None  # 限价委托价；市价决策为 None
    # 本轮结束时的挂单断面（§4.2）；无挂单 / 总闸关闭时恒 None，前端整行省略。
    restingOrder: AiTradeRestingOrderOut | None = None
    reason: str
    signalBoard: list[StrategySignalViewOut] = Field(default_factory=list)
    executed: dict | None = None   # {ok, skipped?, side?, qty?}
    # E1/E2/E3 增量（可选，向后兼容）——注：decision WS 事件由 session._emit_decision 手工构 dict、不经
    # 本模型序列化，故此三键仅为契约/文档镜像，真正上线以 _emit_decision 为准（R-P1-1）。
    marketContext: dict | None = None   # E1：{primary,volatility,event,confidence,labels} 或 None
    consensus: dict | None = None       # E2：{longVotes,shortVotes,flatVotes,total,netStance,agreement}
    degradeReason: str | None = None    # E3：安全回落/共识门禁原因（low_consensus(k/n)/decision_timeout/…）
    # R1 思维链研判增量（ai-trade-reasoning-chain，可选、向后兼容）：
    analysis: str | None = None         # 研判思维链的有界摘录（≤400 字，供左栏决策卡首段；完整全文走 agentTraces）
    marketDigest: dict | None = None    # {priceAction,volume,factors} 价格走势/成交量/因子证据摘要，或 None
    multiTimeframeContext: dict | None = None


class AiTradeRoundTraceOut(BaseModel):
    """快照 agentTraces 的单轮已装配决策轨迹（ai-auto-trading-agent-trace §5.4）。

    契约 / 文档镜像：真正 WS `agent_trace` 事件与 `snapshot.agentTraces` 由 session 手工构 dict、
    不经本模型序列化（沿用 `AiTradeDecisionOut` 既有「文档镜像」范式）。
    """
    round: int
    ts: int
    lastPx: float | None = None
    position: dict | None = None
    signalBoard: list[StrategySignalViewOut] = Field(default_factory=list)
    marketContext: dict | None = None
    consensus: dict | None = None
    marketDigest: dict | None = None    # R1：round_start/trace 承载的价格走势/成交量/因子证据摘要，或 None（RC-9）
    multiTimeframeContext: dict | None = None
    thinking: str = ""
    analysis: str = ""
    toolCall: dict | None = None
    executed: dict | None = None
    degradeReason: str | None = None
    summary: str | None = None
    state: str = "deciding"     # deciding|done|skipped|error


class AiTradeSessionRow(BaseModel):
    sessionId: str
    runId: int
    state: str
    instId: str
    bar: str
    strategyIds: list[int]
    strategyNames: list[str]
    budgetUsd: float
    leverage: float
    takeProfitUsd: float | None = None
    stopLossUsd: float | None = None
    contextRefreshRounds: int
    roundsCompleted: int
    roundsSinceSummary: int
    positionSide: str
    sessionPnlUsd: float
    startedTs: int
    lastDecision: AiTradeDecisionOut | None = None
    contextSummary: str | None = None
    degraded: bool | None = None
    wakeIntervalMinutes: float | None = None   # Agent 墙钟定时唤醒周期（分钟；null=关闭），契约镜像


# ---- AI 自动交易「分组」（ai-trade-multi-symbol-and-timeframe §6.2，镜像 live-auto 分组）----
# 路由手搓 dict、不设 response_model；schema 仅作契约 / TS 对齐镜像。
class AiTradeGroupMemberIn(BaseModel):
    """分组成员入参：标的 + 该标的可用仓位百分比（(0,100]，even 模式整组 Σ ≤ 100）。"""

    instId: str = Field(min_length=1, max_length=32)
    allocationPct: float = Field(gt=0, le=100)


class AiTradeMaBandRuleIn(BaseModel):
    """【基础策略】MA 通道开单约束入参（ai-trade-base-strategy-ma-band §4.1）。

    各数值字段留空 ⇒ 由服务端按 settings 回填（唯一默认真源在 config，不在此处硬编码）；越界即 422。
    `enabled=false` 是用户在启动弹窗**手动关闭**该铁律的唯一途径。
    """

    enabled: bool = True
    period: int | None = Field(default=None, ge=2, le=200)      # 中轨 SMA 周期
    stdMult: float | None = Field(default=None, gt=0, le=5)     # 上下轨标准差倍数
    # %B 锚区容差；0.25 = 三锚区首尾相接 ⇒ 退化为「仅限带内」，故锁死上界，不接受更大值。
    tolerance: float | None = Field(default=None, gt=0, le=0.25)
    directional: bool | None = None                             # 做多不追顶 / 做空不杀底（默认关）


class AiTradeBaseRulesIn(BaseModel):
    """【基础策略】铁律集合入参：本期只有一条预置规则；整个字段缺省 ⇒ 服务端按 settings 装配为开启态。"""

    maBandEntry: AiTradeMaBandRuleIn | None = None


class AiTradeGivebackTierIn(BaseModel):
    """利润阶梯的一档（ai-trade-profit-protection-adaptive §4.1）：峰值收益越过阈值即收紧回吐比例。

    档间的**严格递增 / 单调不增**约束由 `AiTradeProfitGuardIn` 的 model_validator 统一校验
    （单档看不出单调性），此处只兜单档值域。
    """

    peakReturnPct: float = Field(gt=0, le=50)     # 峰值保证金收益率阈值（首档必须 > 0）
    givebackPct: float = Field(gt=0, le=1)        # 该档允许的自峰值回吐比例


class AiTradeProfitGuardIn(BaseModel):
    """利润保护层入参（ai-trade-profit-protection §4.1）：移动止盈 + 保本 + 可选硬止盈。

    各数值字段留空 ⇒ 由服务端按 settings 回填（唯一默认真源在 config，不在此处硬编码）；越界即 422。
    全部收益率为**保证金收益率口径**（诊断根因 ②）。`enabled=false` 是用户在启动弹窗手动关闭本层的
    唯一途径——字段整体缺省表示「默认开启」，与显式关闭严格区分。
    """

    enabled: bool = True
    armReturnPct: float | None = Field(default=None, gt=0, le=5)        # 武装移动止盈的峰值收益率
    givebackPct: float | None = Field(default=None, gt=0, le=1)         # 自峰值允许回吐比例
    breakevenArmReturnPct: float | None = Field(default=None, ge=0, le=5)   # 0 = 关闭保本
    breakevenOffsetReturnPct: float | None = Field(default=None, ge=0, le=1)  # 保本线正向偏移
    minHoldSeconds: float | None = Field(default=None, ge=0, le=3600)
    # null 与 0 等价，均表关闭硬止盈（归一点在 resolve_profit_guard_rule，此后不可再分辨）。
    hardTakeProfitReturnPct: float | None = Field(default=None, ge=0, le=20)
    # ---- 自适应（ai-trade-profit-protection-adaptive §4.1）：留空 ⇒ settings 回填；`[]` ⇒ 显式关闭阶梯 ----
    givebackTiers: list[AiTradeGivebackTierIn] | None = None
    atrFloorMult: float | None = Field(default=None, ge=0, le=10)      # 0 = 关闭下界
    atrCapMult: float | None = Field(default=None, ge=0, le=50)        # 0 = 关闭上界
    cooldownSeconds: float | None = Field(default=None, ge=0, le=86400)  # 0 = 关闭冷却
    cooldownScope: Literal["same_side", "both"] | None = None

    @model_validator(mode="after")
    def _check_adaptive(self) -> "AiTradeProfitGuardIn":
        """边界一次性校验（进入内部后信任）：阶梯单调性 + ATR 上下界不可倒置，违反即 422。

        阶梯校验**复用后端唯一解析点** `profit_guard.parse_giveback_tiers`——env 串与请求体两条入口
        共用同一套约束（R-2：写反的阶梯等于把本轮要修的 bug 写进配置）。
        """
        from .ai_auto.profit_guard import parse_giveback_tiers

        if self.givebackTiers is not None:
            parse_giveback_tiers([t.model_dump() for t in self.givebackTiers])
        floor_mult, cap_mult = self.atrFloorMult, self.atrCapMult
        if (floor_mult is not None and cap_mult is not None
                and floor_mult > 0 and cap_mult > 0 and floor_mult > cap_mult):
            raise ValueError("ATR 下界倍数不可大于上界倍数")
        return self


class AiTradeGroupStartIn(BaseModel):
    """启动 AI 自动交易分组请求（金额/杠杆字段可留空→服务端保守默认，§4.1）。

    预算总额天花板由服务端解析 min(maxBudgetPct/100, ai_trade_max_capital_frac(默认 1.0=不约束),
    live_auto_group_max_capital_frac)（= 前端 effectiveMaxBudgetPct/100，maxBudgetPct 与运营天花板共同主导），
    前端任何「上限」入参一概不接受；capitalUsd 为分组**总**预算，按各成员 allocationPct 切分（非倍增）。
    """

    members: list[AiTradeGroupMemberIn]
    bar: AiExecutionBar = "1H"
    strategyIds: list[int] = Field(min_length=1)         # 多选；上限按 ai_trade_max_strategies 服务端校验
    accountId: int | None = None                         # 省略=默认实盘账户
    allocationMode: Literal["even", "max_ratio"] = "even"
    capitalUsd: float | None = Field(default=None, gt=0)  # 分组总预算，留空→ availUsdt×默认 frac(1%)
    leverage: float | None = Field(default=None, gt=0, le=125)
    takeProfitUsd: float | None = Field(default=None, gt=0)  # 分组总止盈；按 pct/Σpct 归一分摊
    stopLossUsd: float | None = Field(default=None, gt=0)    # 分组总止损；同上
    stopLossPct: float | None = Field(default=None, gt=0)    # E4：单仓 OCO 止损%（各成员同值，服务端夹紧）
    takeProfitPct: float | None = Field(default=None, gt=0)  # E4：单仓 OCO 止盈%（各成员同值）
    contextRefreshRounds: int = Field(default=10, ge=1)   # 精确区间在服务端按 min/max 校验
    # Agent 墙钟定时唤醒周期（分钟）：null/缺省→服务端默认(2)；0→关闭定时唤醒；>0→服务端夹到 [floor_s, ceil_min]。
    # 入口仅校验 ge=0（0 合法=关闭）；越界不拒绝、由 _resolve_wake_interval_s 防御性夹紧（UX 友好）。
    wakeIntervalMinutes: float | None = Field(default=None, ge=0)
    priming: bool = True
    primingBars: int = Field(default=300, ge=10, le=PAPER_WINDOW_MAX)
    # 仓位操作档位步长（fraction）：null/缺省→服务端默认；0→连续（仅 API/后端退出口，UI 不露出）。
    # 越界（>1 / <0）在入口即 422（与既有 leverage 同惯例）；服务端仅 None→默认回填，不复夹（[R-2]）。
    positionStepPct: float | None = Field(default=None, ge=0, le=1)
    # 【基础策略】铁律（默认开启）：**字段缺省 ⇒ 开启**（老客户端不发该字段也默认开启，语义在 API 层
    # `_resolve_base_rules` 单点解析）；`{"maBandEntry":{"enabled":false}}` ⇒ 该会话不装配。
    baseRules: AiTradeBaseRulesIn | None = None
    # 利润保护层（默认开启，同上「字段缺省 ⇒ 开启」语义，解析点 `_resolve_profit_guard`）；
    # `{"enabled":false}` ⇒ 该会话不装配。**实盘与回测两处启动体都接该字段**（adaptive §7.2a I-4：
    # 第 1 轮把回测列为非目标，第 2 轮 F3 用 bar 内 OHLC 极值 + 保守成交序取代了收盘价近似）。
    profitGuard: AiTradeProfitGuardIn | None = None

    @field_validator("members")
    @classmethod
    def _check_members(cls, members: list[AiTradeGroupMemberIn]) -> list[AiTradeGroupMemberIn]:
        """边界校验：非空、instId 唯一（Σ 约束按 allocationMode 在 model_validator 决定）。"""
        if not members:
            raise ValueError("members 不可为空")
        inst_ids = [m.instId for m in members]
        if len(set(inst_ids)) != len(inst_ids):
            raise ValueError("members 含重复 instId")
        return members

    @model_validator(mode="after")
    def _check_allocation_sum(self) -> "AiTradeGroupStartIn":
        """even 强制 Σ allocationPct ≤ 100；max_ratio 跳过 Σ 约束（各标的独立上限，可超 100%）。"""
        if (self.allocationMode == "even"
                and sum(m.allocationPct for m in self.members) > 100 + 1e-9):
            raise ValueError("各标的可用仓位百分比之和不可超过 100%")
        return self


# ---- 信号先验快照（ai-trade-signal-prior-and-ev-gate §4.1）----
class AiTradePriorRebuildIn(BaseModel):
    """重建某 `(instId, bar)` 的先验快照。`strategyIds` 为空数组 = 只建 `signal_edge` 侧。

    `lookbackBars` 的硬上限由 `ai_trade_prior_max_bars` 在**取数前**判定（413 快速失败，与
    `factor_train_max_bars` 同款姿态）——绝不无界拉 K 线。
    """

    instId: str
    bar: str
    strategyIds: list[int] = Field(default_factory=list)
    lookbackBars: int | None = Field(default=None, ge=100)
    segments: int | None = Field(default=None, ge=2, le=20)


class AiTradePriorEntryOut(BaseModel):
    """快照单条目（§5.1）。`avgLossBps` 为**正数**（幅度）；`usable=false` 的条目不参与在线聚合。"""

    source: str
    event: str
    signalKey: str | None = None
    strategyId: int | None = None
    strategyName: str = ""
    strategyFingerprint: str | None = None
    direction: str
    regime: str
    winRate: float
    meanRetBps: float
    avgWinBps: float
    avgLossBps: float
    sampleN: int
    lossN: int
    segments: int
    runN: int
    ir: float | None = None
    tStat: float | None = None
    usable: bool
    unusableReason: str | None = None
    costBasis: str


class AiTradePriorSnapshotOut(BaseModel):
    """快照摘要（rebuild 回执与 GET 共用；`entries` 即落库原文的逐条镜像）。"""

    instId: str
    bar: str
    builtAtTs: int
    asOfTs: int
    horizonBars: int
    costBasis: str
    bars: int
    segments: int
    purgeBars: int
    embargoBars: int
    entryCount: int
    usableCount: int
    coverage: dict[str, int] = Field(default_factory=dict)
    entries: list[AiTradePriorEntryOut] = Field(default_factory=list)


class AiTradeGroupMemberRow(AiTradeSessionRow):   # 复用单会话行全字段 + 分配占比
    allocationPct: float


class AiTradeGroupRow(BaseModel):
    groupId: str
    bar: str
    strategyIds: list[int]
    strategyNames: list[str]
    totalBudgetUsd: float
    leverage: float
    contextRefreshRounds: int
    createdTs: int
    members: list[AiTradeGroupMemberRow]
    wakeIntervalMinutes: float | None = None   # Agent 墙钟定时唤醒周期（分钟；null=关闭），契约镜像
    baseRules: dict | None = None              # 【基础策略】铁律回执镜像（null=未装配）
    profitGuard: dict | None = None            # 利润保护层回执镜像（null=未装配）


class AiTradeGroupStartOut(AiTradeGroupRow):
    """start 回执与 status 视图同构（totalBudgetUsd 为夹紧后最终生效值，可能 < 用户填值）。"""


class AiTradeStatusOut(BaseModel):     # 改：session → group（分组化，ai-trade-multi-symbol §6.2）
    group: AiTradeGroupRow | None = None
    aiTradeEnabled: bool


# ---------------- AI 自动交易 · 回测模式（ai-auto-trading-backtest §5.1，camelCase wire）----------------
class AiBacktestStartIn(BaseModel):
    """启动 AI 回测请求（金额/杠杆/OCO 可留空→服务端保守默认，§4.1）。"""

    instId: str = Field(min_length=1, max_length=32)
    bar: AiExecutionBar = "1H"
    strategyIds: list[int] = Field(min_length=1)          # 上限按 ai_trade_max_strategies 服务端校验
    startTs: int = Field(gt=0)                             # 回测区间起（UTC 秒）
    endTs: int = Field(gt=0)                               # 回测区间止（UTC 秒）
    decisionEveryBars: int | None = Field(default=None, ge=1, le=1000)  # 留空→config 默认
    initialCapital: float | None = Field(default=None, gt=0)  # 留空→settings.initial_capital
    leverage: float | None = Field(default=None, gt=0, le=125)
    stopLossPct: float | None = Field(default=None, gt=0)     # 单仓 OCO 止损%（服务端夹紧到 max）
    takeProfitPct: float | None = Field(default=None, gt=0)   # 单仓 OCO 止盈%（服务端夹紧到 max）
    takeProfitUsd: float | None = Field(default=None, gt=0)   # 会话级止盈金额阈值；留空→不触发
    stopLossUsd: float | None = Field(default=None, gt=0)     # 会话级止损金额阈值；留空→不触发
    contextRefreshRounds: int = Field(default=10, ge=1)       # 精确区间在服务端按 min/max 校验
    minConsensus: int | None = Field(default=None, ge=0)      # 共识门槛；留空→ai_trade_min_consensus
    # 仓位操作档位步长（fraction）：null/缺省→服务端默认；0→连续。越界即 422，服务端仅缺省回填（[R-2]）。
    positionStepPct: float | None = Field(default=None, ge=0, le=1)
    # 【基础策略】铁律：与实盘同字段同语义、同一 `_resolve_base_rules` 解析——**实盘与回测的默认必须
    # 一致**，否则用户用回测调出的参数在实盘直接失真（P1-4）。
    baseRules: AiTradeBaseRulesIn | None = None
    # 利润保护层：与实盘同字段同语义、同一 `_resolve_profit_guard` 解析（adaptive §2.3 / §7.2a I-1）。
    # 回测按 bar 内 OHLC 极值 + 保守成交序近似，运维总闸 `ai_trade_profit_guard_backtest_enabled=false`
    # 可一键回到第 1 轮的「回测无保护层」行为。
    profitGuard: AiTradeProfitGuardIn | None = None


class AiBacktestStartOut(BaseModel):
    jobId: str
    instId: str
    bar: str
    strategyIds: list[int]
    startTs: int
    endTs: int
    decisionEveryBars: int
    plannedRounds: int
    initialCapital: float
    leverage: float


class AiBacktestAiStatsOut(BaseModel):
    decisionRounds: int
    actionCounts: dict                 # {open_long,open_short,close,hold}
    holdRate: float
    avgConfidence: float
    degradedRounds: int
    timeoutRounds: int
    summaryCount: int
    finalSummary: str | None = None
    decisionEveryBars: int
    plannedRounds: int
    reachedRoundCap: bool


class AiBacktestResultOut(BaseModel):
    runId: int | None = None
    metrics: dict                      # compute_metrics 七项
    aiStats: AiBacktestAiStatsOut
    equity: list[dict] = Field(default_factory=list)          # [{ts,equity,drawdown}]
    trades: list[dict] = Field(default_factory=list)          # camelCase TradeRecord
    decisionsDigest: list[dict] = Field(default_factory=list)  # 近 N 条决策（时间线复看）
    summaries: list[dict] = Field(default_factory=list)       # [{round,ts,summary}]
    startTs: int
    endTs: int
    barsProcessed: int
    notes: list[str] = Field(default_factory=list)
    extendedMetrics: dict = Field(default_factory=dict)   # B1 扩展绩效指标（旧版结果为 {}，向后兼容）
    benchmark: dict = Field(default_factory=dict)          # B2 买入持有基准 + alpha（无法计算为 {}）


class AiBacktestStatusOut(BaseModel):
    jobId: str
    state: str                         # running|completed|cancelled|failed
    phase: str                         # loading|priming|running|finalizing
    progress: float
    barIndex: int
    totalBars: int
    decisionRound: int
    plannedRounds: int
    instId: str
    bar: str
    strategyIds: list[int]
    startTs: int
    endTs: int
    startedTs: int
    runId: int | None = None
    result: AiBacktestResultOut | None = None


# ---------------- Workspace 偏好持久化（watchlist-auto-trade-persistence §3 / §4）----------------
class AutoTradeMemberIo(BaseModel):
    """workspace 偏好里的自动交易成员（仅**类型**校验，P1-A）。

    刻意**不**照搬 ``LiveAutoGroupMemberIn`` 的 ``Field(gt=0, le=100)`` 区间约束——区间夹取 / 去重 /
    ``members ⊆ watchlist`` 全部交给 repo sanitizer，否则越界值会先抛 422 使夹取分支不可达。
    """

    instId: str
    allocationPct: float


class SymbolGroupIo(BaseModel):
    """workspace 偏好里的标的组（仅**类型**校验，factor-lab-symbol-groups §3.2）。

    刻意**不**加长度 / 数量约束——去重 / 截断 / 丢空组 / 组数与组容量上限全部交给 repo sanitizer，
    否则越界值会先抛 422 使夹取分支不可达（与 ``AutoTradeMemberIo`` 同 P1-A 职责切分）。``symbols`` 成员**有序**
    （决定焦点 / 方向锚点，见 §3.1）。``createdTs`` / ``updatedTs`` 缺省为 0（sanitizer 兜底非负整数）。
    """

    id: str
    name: str
    symbols: list[str]
    createdTs: int = 0
    updatedTs: int = 0


class WorkspaceWatchlistIn(BaseModel):
    """PUT /api/workspace/watchlist 入参：自选 + 自动交易成员 + 分配模式 + 标的组（仅类型校验，P1-A）。

    422 仅留给类型错误（如 ``allocationPct`` 是字符串、``watchlist`` 不是数组）；越界但类型正确的值
    （``allocationPct=150``、超长 ``watchlist``、超量标的组）一律由 repo sanitizer 夹取 / 截断，不在此拒绝。
    """

    watchlist: list[str]
    autoTradeMembers: list[AutoTradeMemberIo]
    autoTradeAllocationMode: str
    symbolGroups: list[SymbolGroupIo] = Field(default_factory=list)


class WorkspaceWatchlistOut(BaseModel):
    """GET/PUT /api/workspace/watchlist 响应：``present`` 标志 + 夹取后的 workspace 偏好文档。

    ``present`` 区分「从未保存」（前端 seed）与「保存为空」（前端 respect），故用显式标志而非「列表为空」。
    """

    present: bool
    watchlist: list[str]
    autoTradeMembers: list[AutoTradeMemberIo]
    autoTradeAllocationMode: str
    symbolGroups: list[SymbolGroupIo] = Field(default_factory=list)


class LiveAutoGroupMemberRow(BaseModel):
    instId: str
    sessionId: str
    runId: int
    allocationPct: float
    budgetUsd: float
    state: str
    positionSide: str | None = None
    sessionPnlUsd: float | None = None
    degraded: bool | None = None
    flattenFailed: bool | None = None


class LiveAutoGroupStartOut(BaseModel):
    groupId: str
    strategyId: int
    strategyName: str
    strategyKind: str
    bar: str
    totalBudgetUsd: float       # 夹紧后最终生效的分组总预算（可能 < 用户填值，R4）
    leverage: float
    members: list[LiveAutoGroupMemberRow]


class LiveAutoGroupRow(BaseModel):
    groupId: str
    strategyId: int
    strategyName: str
    strategyKind: str
    bar: str
    totalBudgetUsd: float
    leverage: float
    createdTs: int
    members: list[LiveAutoGroupMemberRow]


class LiveAutoGroupStatusOut(BaseModel):
    liveAutoTradingEnabled: bool
    group: LiveAutoGroupRow | None


# ---------------- AI 自动交易统计历史（ai-trade-stats-dashboard §3.6，camelCase wire）----------------
class AiTradeHistoryEntry(BaseModel):
    """历史会话列表单行（GET /ai-trade/history）：一个 group = 一次 AI 自动交易的 KPI 概览。

    汇总由后端按 group 逐成员配对得出（realizedPnlUsd=Σ 平仓已实现盈亏、winRate、best/worst；
    returnPct=realizedPnlUsd/totalBudgetUsd，分母 = 原始投入预算，口径同 _finalize_run）。
    """

    groupId: str
    strategyName: str
    bar: str
    instIds: list[str]
    startedTs: int
    endedTs: int
    status: str
    totalBudgetUsd: float
    realizedPnlUsd: float
    returnPct: float
    tradeCount: int
    winRate: float
    bestUsd: float
    worstUsd: float


class AiTradeHistoryListOut(BaseModel):
    """GET /ai-trade/history 响应：分页历史会话列表（按 startedTs DESC，排除当前运行组）。"""

    items: list[AiTradeHistoryEntry]
    total: int
    limit: int
    offset: int


class AiTradeHistorySummary(BaseModel):
    """单会话聚合 KPI（与列表行汇总同口径；前端亦可据 members[].trades 复算校验）。"""

    realizedPnlUsd: float
    returnPct: float
    tradeCount: int
    winRate: float
    bestUsd: float
    worstUsd: float
    # 利润捕获率五键（adaptive §2.4.3）：分母 peakPnlUsd **不含**手续费、分子 capturedPnlUsd **含**，
    # 故小额往返会系统性偏低；capturePairs / noPeakCount 是样本诚实性（peak ≤0 或缺失不进分母）。
    # 老会话（老库缺列 / 保护层未装配）全为 0，前端据 capturePairs 显示「样本不足」。
    peakPnlUsd: float = 0.0
    capturedPnlUsd: float = 0.0
    captureRatio: float = 0.0
    capturePairs: int = 0
    noPeakCount: int = 0


class AiTradeHistoryMember(BaseModel):
    """单会话内一个成员（标的）run 明细：杠杆 / 预算 / 占比 / 策略名 / 状态 / metrics + 该成员成交行。

    ``trades`` 用 ``list[dict]`` 透传 ``trade_out`` 全字段（含 explain）；``metrics`` 用 ``dict`` 透传
    解析后的 metrics_json（含可选 equityDigest/decisionsDigest/aiStats/finalSummary/reconciled），供详情页
    画权益图 + AI 决策概况，缺失即优雅降级。
    """

    runId: int
    instId: str
    leverage: float
    budgetUsd: float
    allocationPct: float
    strategyNames: list[str] = Field(default_factory=list)
    status: str
    metrics: dict[str, Any] = Field(default_factory=dict)
    trades: list[dict[str, Any]] = Field(default_factory=list)


class AiTradeHistoryDetailOut(BaseModel):
    """GET /ai-trade/history/{groupId} 响应：会话头 + 逐成员成交 / metrics + 后端聚合 summary。"""

    groupId: str
    strategyName: str
    bar: str
    startedTs: int
    endedTs: int
    status: str
    totalBudgetUsd: float
    members: list[AiTradeHistoryMember]
    summary: AiTradeHistorySummary


# ---------------- Response serializers ----------------
def _parse_explain(raw: str | None) -> dict | None:
    """explain_json → dict（缺列 / 空 / 坏 JSON 安全回 None，与既有 run_out 容错风格一致）。"""
    if not raw:
        return None
    import json
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) and parsed.get("kind") else None


def trade_out(t: Trade) -> dict:
    return {
        "id": t.id, "runId": t.run_id, "ts": t.ts, "side": t.side, "price": t.price,
        "qty": t.qty, "fee": t.fee, "pnl": t.pnl, "positionAfter": t.position_after,
        "reason": t.reason, "stopLossPct": t.stop_loss_pct,
        "takeProfitPct": t.take_profit_pct, "maxHoldBars": t.max_hold_bars,
        "stopLossPrice": t.stop_loss_price, "takeProfitPrice": t.take_profit_price,
        "positionPct": getattr(t, "position_pct", None),
        # F4：本持仓周期的峰值浮盈（保护层未装配 / 快照一路不可用 / 老库缺列 → None，**绝不填 0**）。
        # `getattr` 兜底：幂等补列前的进程 / 老快照读到 None 而不是 AttributeError（G-6）。
        "peakPnlUsd": getattr(t, "peak_pnl", None),
        # explain 键恒在（缺列 / 坏 JSON → None），与既有恒含全键风格一致（§5.2 / P2-6）。
        "explain": _parse_explain(getattr(t, "explain_json", None)),
    }


def equity_out(e: EquityPoint) -> dict:
    return {"ts": e.ts, "equity": e.equity, "drawdown": e.drawdown}


def run_out(run: StrategyRun) -> dict:
    import json
    metrics = _empty_metrics()
    if run.metrics_json:
        try:
            metrics = json.loads(run.metrics_json)
        except json.JSONDecodeError:
            pass
    return {
        "id": run.id, "strategyId": run.strategy_id, "instId": run.inst_id, "bar": run.bar,
        "mode": run.mode, "status": run.status, "startTs": run.start_ts, "endTs": run.end_ts,
        "metrics": metrics,
    }


def walk_forward_out(run: StrategyRun) -> dict:
    """从 parent run 的 config_json 还原 {runId, summary, windows}（GET /runs/{id}/walk-forward）。

    与 POST 响应同构；原子策略和组合策略都用该形状。config 缺字段时回落空 summary /
    空 windows（历史 run 损坏的稳妥兜底）。
    """
    import json
    try:
        cfg = json.loads(run.config_json or "{}")
    except json.JSONDecodeError:
        cfg = {}
    return {"runId": run.id, "summary": cfg.get("summary", {}), "windows": cfg.get("windows", [])}


def _combo_segment_out(seg) -> dict:
    """ComboSegment → 前端 dict（camelCase）。"""
    return {
        "startTs": seg.start_ts, "endTs": seg.end_ts, "memberKey": seg.member_key,
        "memberName": seg.member_name, "regime": seg.regime, "bars": seg.bars,
        "contribution": round(seg.contribution, 6),
    }


def combo_out(result, notes: list[str] | None = None) -> dict:
    """ComboBacktestResult 的 `combo` 子对象序列化（annualized + 时间线 + 分体制贡献 + notes）。"""
    return {
        "annualizedReturn": round(result.annualized_return, 6),
        "timeline": [_combo_segment_out(s) for s in result.segments],
        "breakdown": list(result.breakdown),
        "notes": notes if notes is not None else list(result.notes),
    }


class AgentConfigOut(BaseModel):
    """GET /agent/config 响应：是否已配置密钥 + 当前生效服务（绝不含 key，R3）。"""

    configured: bool
    provider: str
    model: str
    providerId: int | None = None      # 当前默认服务库内 id（env 回落 / 未配置为 None）
    label: str = ""                    # 当前默认服务展示名（追加字段，向后兼容）


# ---------------- AI 模型服务商（设置中心） ----------------
def _key_preview(api_key: str) -> str:
    """密钥脱敏预览：仅末 4 位，前缀点号；无 key 返回空串（绝不外泄明文，R-Sec）。"""
    key = (api_key or "").strip()
    return f"····{key[-4:]}" if key else ""


def provider_out(row: AgentProvider) -> dict:
    """AgentProvider 行 → 前端 dict（camelCase）。**绝不含 raw key**，仅 hasKey/keyPreview。"""
    has_key = bool((row.api_key or "").strip())
    return {
        "id": row.id, "slug": row.slug, "kind": row.kind, "label": row.label,
        "apiStandard": row.api_standard, "baseUrl": row.base_url, "model": row.model,
        "userAgent": row.user_agent, "hasKey": has_key, "keyPreview": _key_preview(row.api_key),
        "isDefault": bool(row.is_default), "deletable": row.kind == "custom",
        "createdTs": int(row.created_at.timestamp()) if row.created_at else 0,
        "updatedTs": int(row.updated_at.timestamp()) if row.updated_at else 0,
    }


class AgentProviderIn(BaseModel):
    """POST /agent/providers：新增自定义服务（kind 固定 custom）。

    写入字段补 max_length（SQLite 不强制 VARCHAR 长度，须在边界一次性校验，C5/H3）。
    """

    label: str = Field(min_length=1, max_length=128)
    apiStandard: Literal["anthropic", "openai"]
    baseUrl: str = Field(min_length=1, max_length=512)
    apiKey: str = Field(default="", max_length=512)
    model: str = Field(min_length=1, max_length=128)
    userAgent: str | None = Field(default=None, max_length=128)


class AgentProviderUpdate(BaseModel):
    """PUT /agent/providers/{id}：编辑（apiKey 缺/空 = 保留原 key；官方项锁 slug/kind/标准）。

    可空字段「缺省=不改」语义不变（None 不触发 min_length）；但「显式给出」时校验非空 +
    上限——堵住 PUT 把 label/model 置空串的隐患（H3）。
    """

    label: str | None = Field(default=None, min_length=1, max_length=128)
    apiStandard: Literal["anthropic", "openai"] | None = None
    baseUrl: str | None = Field(default=None, min_length=1, max_length=512)
    apiKey: str | None = Field(default=None, max_length=512)
    model: str | None = Field(default=None, min_length=1, max_length=128)
    userAgent: str | None = Field(default=None, max_length=128)


class ProviderTestReq(BaseModel):
    """POST /agent/providers/test：测草稿连通性（首存前，须带显式 apiKey）。"""

    apiStandard: Literal["anthropic", "openai"]
    baseUrl: str = Field(min_length=1, max_length=512)
    apiKey: str = Field(default="", max_length=512)
    model: str = Field(min_length=1, max_length=128)
    userAgent: str | None = Field(default=None, max_length=128)


class ProviderTestResult(BaseModel):
    """连通性测试结果：ok + 时延 / 错误码（code: AGENT_AUTH_ERROR|CONNECTION_FAILED|…）。"""

    ok: bool
    latencyMs: float | None = None
    model: str | None = None
    code: str | None = None
    message: str | None = None


class ProviderActiveOut(BaseModel):
    configured: bool
    providerId: int | None = None
    label: str = ""
    provider: str
    model: str


class ProvidersListOut(BaseModel):
    """GET /agent/providers：全量服务商（脱敏）+ 当前默认 id + 解析后的当前生效服务摘要。"""

    providers: list[dict]
    defaultId: int | None = None
    active: ProviderActiveOut


# ---------------- Agent 工具管理（设置中心 · Agent 工具管理） ----------------
class AgentToolOut(BaseModel):
    """单个工具的展示项（GET 列表 / PUT 回包）：元信息来自注册表 + 合并后的启用态。"""

    name: str
    description: str
    category: str
    isUiAction: bool
    enabled: bool
    paramsSchema: dict


class AgentToolsListOut(BaseModel):
    """GET /agent/tools、POST /agent/tools/bulk 响应：全量工具 + 启用计数概览。"""

    tools: list[dict]
    enabledCount: int
    totalCount: int


class AgentToolUpdate(BaseModel):
    """PUT /agent/tools/{name}：设置单工具启用态。"""

    enabled: bool


class AgentToolBulkUpdate(BaseModel):
    """POST /agent/tools/bulk：批量启用 / 禁用全部工具。"""

    enabled: bool


def tool_out(meta: dict, enabled: bool) -> dict:
    """注册表元信息 + 启用态 → 前端 dict（camelCase；工具是否存在以注册表为准，C6）。"""
    return {"name": meta["name"], "description": meta["description"],
            "category": meta["category"], "isUiAction": meta["isUiAction"],
            "enabled": enabled, "paramsSchema": meta["paramsSchema"]}


# ---------------- 实盘账户（设置中心 · 实盘 OKX 账户凭证） ----------------
def account_out(row: OkxAccount) -> dict:
    """OkxAccount 行 → 前端 dict（camelCase）。**绝不含 raw secret/passphrase / 完整 api_key**。

    掩码复用 `_key_preview`（P2-Naming：字段名取 `apiKeyPreview` 而非 `keyPreview`，多密钥实体下更清晰）；
    时间照 `provider_out` 输出秒级 UTC 整数（P2-Time，全系统时间口径一致）。
    """
    return {
        "id": row.id, "label": row.label,
        "apiKeyPreview": _key_preview(row.api_key),
        "hasSecret": bool((row.secret_key or "").strip()),
        "hasPassphrase": bool((row.passphrase or "").strip()),
        "ipWhitelist": row.ip_whitelist, "permissions": row.permissions,
        "isDefault": bool(row.is_default),
        "createdTs": int(row.created_at.timestamp()) if row.created_at else 0,
        "updatedTs": int(row.updated_at.timestamp()) if row.updated_at else 0,
    }


class OkxAccountIn(BaseModel):
    """POST /accounts：新增账户。label/apiKey/secretKey/passphrase 必填。

    写入字段补 max_length（SQLite 不强制 VARCHAR 长度，须在边界一次性校验，C5/H3）。
    """

    label: str = Field(min_length=1, max_length=128)
    apiKey: str = Field(min_length=1, max_length=128)
    secretKey: str = Field(min_length=1, max_length=128)
    passphrase: str = Field(min_length=1, max_length=128)
    ipWhitelist: str = Field(default="", max_length=512)
    permissions: str = Field(default="", max_length=128)


class OkxAccountUpdate(BaseModel):
    """PUT /accounts/{id}：编辑（None/空=不改；secretKey/passphrase 空=保留原值）。"""

    label: str | None = Field(default=None, min_length=1, max_length=128)
    apiKey: str | None = Field(default=None, min_length=1, max_length=128)
    secretKey: str | None = Field(default=None, max_length=128)
    passphrase: str | None = Field(default=None, max_length=128)
    ipWhitelist: str | None = Field(default=None, max_length=512)
    permissions: str | None = Field(default=None, max_length=128)


class OkxAccountTestReq(BaseModel):
    """POST /accounts/test：测草稿连通性（首存前，须带显式 apiKey/secretKey/passphrase）。"""

    apiKey: str = Field(min_length=1, max_length=128)
    secretKey: str = Field(min_length=1, max_length=128)
    passphrase: str = Field(min_length=1, max_length=128)
    ipWhitelist: str = Field(default="", max_length=512)


class OkxAccountTestResult(BaseModel):
    """连通性测试结果：ok + 时延 / 语义错误码（OKX_AUTH_*/OKX_IP_DENIED/CONNECTION_FAILED/…）。"""

    ok: bool
    latencyMs: float | None = None
    code: str | None = None
    message: str | None = None


class OkxAccountBalanceRow(BaseModel):
    ccy: str
    eq: float | None = None
    availBal: float | None = None
    frozenBal: float | None = None
    eqUsd: float | None = None


class OkxAccountPosition(BaseModel):
    instId: str
    instType: str
    posSide: str
    pos: float | None = None
    avgPx: float | None = None
    last: float | None = None
    upl: float | None = None
    uplRatio: float | None = None
    lever: float | None = None
    marginMode: str


class OkxAccountInfo(BaseModel):
    uid: str
    acctLevel: str
    acctLevelLabel: str
    permissions: list[str]


class OkxAccountBalance(BaseModel):
    totalEqUsd: float | None = None
    details: list[OkxAccountBalanceRow]


class OkxAccountDetailOut(BaseModel):
    """GET /accounts/{id}/detail：信息 + 资产 + 持仓（真实 OKX，鉴权失败→502，绝不假数据）。"""

    info: OkxAccountInfo
    balance: OkxAccountBalance
    positions: list[OkxAccountPosition]


class OkxAccountsListOut(BaseModel):
    """GET /accounts：全量账户（脱敏）+ 当前默认 id。"""

    accounts: list[dict]
    defaultId: int | None = None


# ---------------- 实盘交易（Phase 2 · /api/trading/*） ----------------
class InstMetaOut(BaseModel):
    """合约元数据（来源 GET /api/v5/public/instruments，C1-1）；下单数学全依赖之。"""

    ctVal: float
    ctValCcy: str
    lotSz: float
    minSz: float
    tickSz: float
    maxLever: float


class TradingConfigOut(BaseModel):
    """GET /trading/config：下单面板上下文（鉴权失败→502，绝不假数据，C1）。"""

    accountId: int
    instId: str
    posMode: str                      # long_short_mode | net_mode
    tdMode: str                       # cross | isolated
    availUsdt: float
    instMeta: InstMetaOut
    lastPx: float | None = None
    instrumentSupported: bool         # settleCcy=USDT 且 SWAP/FUTURES 才可下单（C1-4）
    liveTradingEnabled: bool


class PlaceOrderIn(BaseModel):
    """POST /trading/order：px 仅 limit 必填；tp/sl 可选；lever 提供则先设杠杆。clOrdId 由后端生成。"""

    accountId: int | None = None
    instId: str = Field(min_length=1, max_length=32)
    direction: Literal["long", "short"]
    ordType: Literal["limit", "market"]
    tdMode: Literal["cross", "isolated"] = "cross"
    px: float | None = Field(default=None, gt=0)
    sz: float = Field(gt=0)
    lever: float | None = Field(default=None, gt=0, le=125)
    tpTriggerPx: float | None = Field(default=None, gt=0)
    slTriggerPx: float | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def validate_limit_price(self) -> "PlaceOrderIn":
        if self.ordType == "limit" and self.px is None:
            raise ValueError("limit order requires px")
        return self


class PlaceOrderOut(BaseModel):
    """POST /trading/order 响应（clOrdId 为后端生成的唯一值，C1-2）。"""

    ok: bool
    ordId: str | None = None
    clOrdId: str | None = None
    code: str | None = None
    message: str | None = None


class SetLeverageIn(BaseModel):
    """POST /trading/leverage：posSide 仅 isolated + long_short_mode 时传，cross/net_mode 省略（C2-1）。"""

    accountId: int | None = None
    instId: str = Field(min_length=1, max_length=32)
    lever: float = Field(gt=0, le=125)
    mgnMode: Literal["cross", "isolated"] = "cross"
    posSide: Literal["long", "short"] | None = None


class SetLeverageOut(BaseModel):
    ok: bool
    instId: str
    lever: float
    mgnMode: str
    posSide: str | None = None


class LiveFill(BaseModel):
    """实盘成交明细单行（GET /trading/fills）。

    ``clOrdId`` / ``tradeId`` / ``explain`` 为 live-fills-explainability 增量字段（向后兼容、
    camelCase）。注意：未声明的字段会被 ``response_model`` 序列化丢弃，故服务端富化挂上的
    ``explain`` / ``clOrdId`` / ``tradeId`` 必须在此声明（§三）。
    """

    ts: int                # 秒级 UTC
    instId: str
    side: str              # buy | sell
    fillPx: float
    fillSz: float
    fee: float
    ordId: str
    tradeId: str | None = None       # OKX 每笔成交唯一 id（前端行 key / 展开态身份，RV2）
    clOrdId: str | None = None       # OKX 回带（自动交易为 aragon/aragonc 前缀；手动/外部可能为空）
    explain: dict | None = None      # 命中自动交易触发解释时存在（TriggerExplain，§五）
    positionPct: float | None = None # 该真实 fill 分摊到的自动订单仓位比例（fraction）


class LiveFillsOut(BaseModel):
    fills: list[LiveFill]


class LiveOpenOrder(BaseModel):
    """未结委托单行（普通挂单 + 策略委托归一；GET /trading/open-orders）。

    ``kind`` 由服务层 ``_classify_pending_kind`` / ``_normalize_algo_order`` 派生：
    ``limit_close`` 限价平仓、``tp`` 止盈、``sl`` 止损、``limit_open`` 限价开仓（接口诚实返回，
    前端按 kind 过滤展示）。``px`` 为限价委托价（tp/sl 触发后市价时为 ``None``），``triggerPx`` 为策略
    委托触发价（普通挂单为 ``None``）。
    """

    kind: Literal["limit_close", "tp", "sl", "limit_open"]
    instId: str
    posSide: str                 # long | short | net
    side: str                    # buy | sell
    px: float | None = None
    triggerPx: float | None = None
    sz: float | None = None
    state: str                   # live | partially_filled ...
    reduceOnly: bool
    ordId: str | None = None
    clOrdId: str | None = None   # 我方自定义订单 ID（挂单归属判定的前提；策略委托无此值）
    algoId: str | None = None
    createdTs: int               # 秒级 UTC
    # 随单 OCO 触发价（ai-trade-limit-orders-fix F-4，加法式）：仅普通挂单可能非 None——随单
    # OCO 在挂单成交前不出现在 orders-algo-pending，这是未成交挂单止盈止损的唯一可见来源。
    attachTpTriggerPx: float | None = None
    attachSlTriggerPx: float | None = None


class LiveOpenOrdersOut(BaseModel):
    orders: list[LiveOpenOrder]


class LivePositionsOut(BaseModel):
    """GET /trading/positions：真实持仓（行复用 OkxAccountPosition）。"""

    positions: list[OkxAccountPosition]


class ClosePositionIn(BaseModel):
    """POST /trading/close：单仓平仓（limit 挂单平仓 / market 整仓市价平）。clOrdId 由后端生成。

    Pydantic 层仅做形态校验（px>0、sz>0）；``sz≥minSz`` / ``sz≤|pos|`` 等依赖合约元数据与实时持仓的
    边界由服务层 ``validate_close`` 校验（P1-2，本层不掌握 minSz / |pos|）。
    """

    accountId: int | None = None
    instId: str = Field(min_length=1, max_length=32)
    posSide: Literal["long", "short", "net"]
    pos: float                        # 带符号张数（net 判向参考；sz 上限以服务层实时持仓为准）
    mgnMode: Literal["cross", "isolated"]
    ordType: Literal["limit", "market"]
    px: float | None = Field(default=None, gt=0)   # 限价必填、>0；市价忽略
    sz: float | None = Field(default=None, gt=0)    # 限价部分平仓张数，缺省=全仓 |pos|；市价忽略

    @model_validator(mode="after")
    def validate_limit_price(self) -> "ClosePositionIn":
        if self.ordType == "limit" and self.px is None:
            raise ValueError("limit close requires px")
        return self


class ClosePositionOut(BaseModel):
    """POST /trading/close 响应（市价单 OKX 不回 ordId，closedSz 回填本地 |pos|）。"""

    ok: bool
    mode: str                         # limit | market
    ordId: str | None = None
    clOrdId: str | None = None
    closedSz: float | None = None
    code: str | None = None
    message: str | None = None


class CloseAllIn(BaseModel):
    """POST /trading/close-all：一键市价全平（accountId 可选，缺省回落默认账户）。"""

    accountId: int | None = None


class CloseAllResultRow(BaseModel):
    """一键全平逐仓结果行（单仓失败不致整体失败，R10）。"""

    instId: str
    posSide: str
    ok: bool
    clOrdId: str | None = None
    code: str | None = None
    message: str | None = None


class CloseAllOut(BaseModel):
    """POST /trading/close-all 响应（诚实汇总：ok 仅在全部成功为真）。"""

    ok: bool
    closedCount: int
    failedCount: int
    results: list[CloseAllResultRow]


# ---------------- 因子层（因子计算 / 评估 / 挖掘 / 训练 / 组合，spec §4） ----------------
def _load_json(raw: str | None, fallback: Any) -> Any:
    import json
    try:
        return json.loads(raw) if raw else fallback
    except (json.JSONDecodeError, TypeError):
        return fallback


def factor_out(row: Factor) -> dict:
    """Factor 行 → 前端 dict（camelCase）。``enabled`` 即激活态；``builtin`` = source==builtin。"""
    return {
        "id": row.id, "factorKey": row.factor_key, "name": row.name,
        "params": _load_json(row.params_json, {}), "direction": row.direction,
        "category": row.category, "source": row.source, "enabled": bool(row.enabled),
        "builtin": row.source == "builtin", "notes": row.notes,
        "eval": _load_json(row.eval_json, None),
        "createdTs": int(row.created_at.timestamp()) if row.created_at else 0,
        "updatedTs": int(row.updated_at.timestamp()) if row.updated_at else 0,
    }


def factor_set_out(row: FactorSet) -> dict:
    """FactorSet 行 → 前端 dict（camelCase；含成员快照 + 训练产物 + 复合 IC）。

    保持**纯同步、不增 IO**：伴生交易策略 ``strategyId`` 不写入此处，而由读路径（GET 列表/单条与
    create/update 编排）经 ``repo.map_companion_strategy_ids`` 批量反查后在包装层附加，缺省 ``None``
    （评审 R1/R5，避免 N+1 与刷新后恒 null）。
    """
    return {
        "id": row.id, "name": row.name, "members": _load_json(row.members_json, []),
        "method": row.method, "config": _load_json(row.config_json, {}),
        "model": _load_json(row.model_json, {}), "metrics": _load_json(row.metrics_json, None),
        "enabled": bool(row.enabled),
        "createdTs": int(row.created_at.timestamp()) if row.created_at else 0,
        "updatedTs": int(row.updated_at.timestamp()) if row.updated_at else 0,
    }


class _FactorRange(BaseModel):
    instId: str = Field(min_length=1, max_length=32)
    bar: str = "1H"
    startTs: int = Field(ge=0)
    endTs: int = Field(ge=0)

    @model_validator(mode="after")
    def _validate_range(self) -> "_FactorRange":
        if self.startTs >= self.endTs:
            raise ValueError("startTs must be less than endTs")
        return self


class FactorMemberIn(BaseModel):
    """组合成员入参：自包含快照，方向必须冻结为 long/short（不接受 auto，评审 C-P1-1）。"""

    factorKey: str = Field(min_length=1, max_length=64)
    params: dict[str, Any] = Field(default_factory=dict)
    weight: float = 0.0
    direction: Literal["long", "short"]


class FactorCreateReq(BaseModel):
    factorKey: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=128)
    params: dict[str, Any] = Field(default_factory=dict)
    direction: Literal["long", "short", "auto"] = "auto"
    source: Literal["custom", "mined"] = "custom"
    notes: str = Field(default="", max_length=256)


class FactorUpdateReq(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    params: dict[str, Any] | None = None
    direction: Literal["long", "short", "auto"] | None = None
    enabled: bool | None = None
    notes: str | None = Field(default=None, max_length=256)


class FactorComputeReq(_FactorRange):
    factorKey: str = Field(min_length=1, max_length=64)
    params: dict[str, Any] = Field(default_factory=dict)
    maxBars: int | None = Field(default=None, ge=10, le=200000)


class FactorEvaluateReq(_FactorRange):
    factorKey: str | None = Field(default=None, max_length=64)
    factorId: int | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    direction: Literal["long", "short", "auto"] = "auto"
    horizons: list[int] | None = Field(default=None, max_length=8)
    quantiles: int = Field(default=5, ge=2, le=10)
    maxBars: int | None = Field(default=None, ge=10, le=200000)
    # 标的组多标的评估（factor-lab-symbol-groups §3.3）：缺省 / 单元素回落 instId 焦点路径（行为不变）；
    # 标的数上限由 service 层 max_eval_symbols 400 护栏在取数前判定（此处仅类型 + 元素长度上界，绝不静默扇出）。
    symbols: list[str] | None = Field(default=None, max_length=100)


class FactorMineReq(_FactorRange):
    categories: list[str] | None = None
    horizons: list[int] | None = Field(default=None, max_length=8)
    topN: int = Field(default=20, ge=1, le=100)
    persist: bool = False
    maxBars: int | None = Field(default=None, ge=10, le=200000)
    # 标的组多标的挖掘（factor-lab-symbol-groups §3.4）：扇出由 413 工作量护栏（计入标的数）在取数前拦截。
    symbols: list[str] | None = Field(default=None, max_length=100)


# 权重训练方法单源（weight-model-nonlinear-trainers §四）：上移集中维护，所有请求体 method 校验共用；
# 追加 6 款顶级模型（5 非线性 extra_trees/mlp/kernel_ridge/knn/mutual_info + 1 稀疏线性 elastic_net）。
# 后端训练器双后端恒可用（库缺失走内置 numpy 兜底），故此处 Literal 放开即被接受，不再 422。
_WEIGHT_METHODS = Literal[
    "equal", "ic_weight", "linear", "random_forest", "xgboost",
    "extra_trees", "mlp", "kernel_ridge", "knn", "mutual_info", "elastic_net",
]


class FactorTrainReq(_FactorRange):
    members: list[FactorMemberIn] = Field(min_length=1, max_length=12)
    method: _WEIGHT_METHODS = "linear"
    config: dict[str, Any] = Field(default_factory=dict)
    horizon: int = Field(default=6, ge=1, le=100)
    maxBars: int | None = Field(default=None, ge=10, le=200000)


class FactorSetCreateReq(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    members: list[FactorMemberIn] = Field(min_length=1, max_length=12)
    method: _WEIGHT_METHODS = "linear"
    modelJson: dict[str, Any] = Field(default_factory=dict)
    metricsJson: dict[str, Any] | None = None
    config: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True


class FactorSetUpdateReq(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    members: list[FactorMemberIn] | None = None
    method: _WEIGHT_METHODS | None = None
    modelJson: dict[str, Any] | None = None
    metricsJson: dict[str, Any] | None = None
    config: dict[str, Any] | None = None
    enabled: bool | None = None


class FactorScoreReq(_FactorRange):
    maxBars: int | None = Field(default=None, ge=10, le=200000)


class FactorScoreInlineReq(_FactorRange):
    members: list[FactorMemberIn] = Field(min_length=1, max_length=12)
    thresholds: dict[str, Any] = Field(default_factory=dict)
    maxBars: int | None = Field(default=None, ge=10, le=200000)


# ---------------- 权重模型工作区（weight-model-workspace §五，流式训练 + 模型管理） ----------------
# 注：``_WEIGHT_METHODS`` 单源已上移至 FactorTrainReq 之前集中定义（weight-model-nonlinear-trainers §四）。


def weight_train_run_out(row: WeightTrainRun, *, with_curve: bool = True) -> dict:
    """WeightTrainRun 行 → 前端 dict（camelCase）。列表态 ``with_curve=False`` 仅回曲线点数，详情回完整 ``curve``。"""
    curve = _load_json(row.curve_json, [])
    curve = curve if isinstance(curve, list) else []
    out = {
        "id": row.id, "modelId": row.model_id, "status": row.status, "method": row.method,
        "instId": row.inst_id, "bar": row.bar, "startTs": row.start_ts, "endTs": row.end_ts,
        "horizon": row.horizon, "config": _load_json(row.config_json, {}),
        "weights": _load_json(row.weights_json, {}), "normStats": _load_json(row.norm_stats_json, {}),
        "metrics": _load_json(row.metrics_json, None), "provenance": _load_json(row.provenance_json, {}),
        "trainerVersion": row.trainer_version, "note": row.note,
        "errorCode": row.error_code or None, "errorMessage": row.error_message or None,
        "curveCount": len(curve), "startedTs": row.started_ts, "finishedTs": row.finished_ts,
        # 多模型 / 探索批次候选（weight-multi-model-training §5.2 / factor-combo-exploration §2.5）：变体标签
        # （探索批次 = 组合 id C0/C1…）与候选自有组合成员（探索候选=池子集；非探索候选为 None）。均加法向后兼容。
        "variantLabel": row.variant_label or "",
        "members": _load_json(row.members_json, None),
    }
    out["curve"] = curve if with_curve else None
    return out


def weight_model_out(row: WeightModel, *, latest_run: WeightTrainRun | None = None) -> dict:
    """WeightModel 行 → 前端 dict（camelCase；含可选最近成功训练记录概要）。

    标的组训练模型的组成员与来源组从 ``config_json`` 派生（``symbols`` / ``symbolGroupId``，零 DB 迁移，
    factor-lab-symbol-groups §3.5）；单标的 / 旧模型 ``symbols`` 回落 ``[inst_id]``、``symbolGroupId`` 为 None，
    缺省安全。
    """
    config = _load_json(row.config_json, {})
    config = config if isinstance(config, dict) else {}
    raw_symbols = config.get("symbols")
    symbols = [str(s) for s in raw_symbols] if isinstance(raw_symbols, list) and raw_symbols else [row.inst_id]
    group_id = config.get("symbolGroupId")
    return {
        "id": row.id, "name": row.name, "instId": row.inst_id, "bar": row.bar,
        "members": _load_json(row.members_json, []), "method": row.method,
        "config": config, "horizon": row.horizon, "source": row.source,
        "factorSetId": row.factor_set_id, "latestRunId": row.latest_run_id,
        "bestValidIc": row.best_valid_ic, "notes": row.notes, "enabled": bool(row.enabled),
        "symbols": symbols, "symbolGroupId": group_id if isinstance(group_id, str) else None,
        "latestRun": weight_train_run_out(latest_run, with_curve=False) if latest_run is not None else None,
        "createdTs": int(row.created_at.timestamp()) if row.created_at else 0,
        "updatedTs": int(row.updated_at.timestamp()) if row.updated_at else 0,
    }


def _bt_candle_out(candle) -> dict:
    """内存态 Candle → 前端 ``{ts,o,h,l,c,vol}``（现网无命名 candle_out 助手，本轮新建，weight-model-backtest-replay P1-3）。"""
    return {"ts": candle.ts, "o": candle.o, "h": candle.h, "l": candle.l, "c": candle.c, "vol": candle.vol}


def _bt_trade_out(trade) -> dict:
    """内存态 ``TradeRecord`` → ``Trade`` 打包体（镜像 ``trade_out`` 去掉 ORM 独有 ``id``/``run_id``/``explain_json``，P1-3）。"""
    return {
        "ts": trade.ts, "side": trade.side, "price": trade.price, "qty": trade.qty,
        "fee": trade.fee, "pnl": trade.pnl, "positionAfter": trade.position_after,
        "reason": trade.reason, "stopLossPct": trade.stop_loss_pct,
        "takeProfitPct": trade.take_profit_pct, "maxHoldBars": trade.max_hold_bars,
        "stopLossPrice": trade.stop_loss_price, "takeProfitPrice": trade.take_profit_price,
    }


def weight_backtest_out(candles: list, result, *, meta: dict) -> dict:
    """权重模型一次性回测打包体：``candles/trades/equity/metrics/meta``（camelCase，不落库）。

    ``equity`` 复用 ``equity_out``（内存态 ``EquityRecord`` 与 ORM ``EquityPoint`` 字段同名，P1-3）；``metrics``
    透传 ``compute_metrics`` 的 7 键（含 ``avgPnl``，即便 ``MetricsCards`` 只显示 6，P2-6）。
    """
    return {
        "candles": [_bt_candle_out(c) for c in candles],
        "trades": [_bt_trade_out(t) for t in result.trades],
        "equity": [equity_out(e) for e in result.equity_points],
        "metrics": result.metrics,
        "meta": meta,
    }


class WeightModelCreateReq(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    instId: str = Field(min_length=1, max_length=32)
    bar: str = "1H"
    members: list[FactorMemberIn] = Field(min_length=1, max_length=12)
    method: _WEIGHT_METHODS = "linear"
    config: dict[str, Any] = Field(default_factory=dict)
    horizon: int = Field(default=6, ge=1, le=100)
    notes: str = Field(default="", max_length=256)
    factorSetId: int | None = None
    enabled: bool = True
    # 标的组训练（factor-lab-symbol-groups §3.5）：缺省回落 [instId] 单标的；组成员落 config_json（零 DB 迁移）。
    symbols: list[str] | None = Field(default=None, max_length=100)
    symbolGroupId: str | None = Field(default=None, max_length=64)


class WeightModelUpdateReq(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    instId: str | None = Field(default=None, min_length=1, max_length=32)
    bar: str | None = None
    members: list[FactorMemberIn] | None = None
    method: _WEIGHT_METHODS | None = None
    config: dict[str, Any] | None = None
    horizon: int | None = Field(default=None, ge=1, le=100)
    notes: str | None = Field(default=None, max_length=256)
    enabled: bool | None = None
    symbols: list[str] | None = Field(default=None, max_length=100)
    symbolGroupId: str | None = Field(default=None, max_length=64)


class WeightTrainStartReq(_FactorRange):
    """启动流式训练入参（继承 ``_FactorRange`` 区间校验，weight-model-workspace §5.3）。"""

    members: list[FactorMemberIn] = Field(min_length=1, max_length=12)
    method: _WEIGHT_METHODS = "linear"
    config: dict[str, Any] = Field(default_factory=dict)
    horizon: int = Field(default=6, ge=1, le=100)
    modelId: int | None = None
    factorSetId: int | None = None
    name: str | None = Field(default=None, min_length=1, max_length=128)
    maxBars: int | None = Field(default=None, ge=10, le=200000)
    # 标的组训练（factor-lab-symbol-groups §3.5）：缺省回落 [instId] 单标的（等价性门禁不受影响）；
    # 标的数上限由 weight_model_service 层 max_train_symbols 400 护栏在取数前判定。
    symbols: list[str] | None = Field(default=None, max_length=100)
    symbolGroupId: str | None = Field(default=None, max_length=64)


class WeightBacktestReq(BaseModel):
    """权重模型某条成功训练记录的一次性回测入参（全部可选，weight-model-backtest-replay §四）。

    ``startTs``/``endTs`` 须同现同缺（both-or-neither）且 ``startTs < endTs``，缺省回落训练区间；成本缺省
    回落 ``settings``。``maxBars`` 超限由 service 静默钳制到 ``settings.backtest_max_bars``（默认 20000），
    **不报 413**、不挪用训练码 ``FACTOR_TRAIN_TOO_LARGE``（语义不符，P2-5）。
    """

    startTs: int | None = Field(default=None, ge=0)
    endTs: int | None = Field(default=None, ge=0)
    initialCapital: float | None = Field(default=None, gt=0)
    fees: float | None = Field(default=None, ge=0)
    slippage: float | None = Field(default=None, ge=0)
    leverage: float | None = Field(default=None, gt=0)
    maxBars: int | None = Field(default=None, ge=10, le=200000)

    @model_validator(mode="after")
    def _validate_range(self) -> "WeightBacktestReq":
        if (self.startTs is None) != (self.endTs is None):
            raise ValueError("startTs and endTs must be provided together")
        if self.startTs is not None and self.endTs is not None and self.startTs >= self.endTs:
            raise ValueError("startTs must be less than endTs")
        return self


# ---------------- 多模型训练竞技场（weight-multi-model-training §四/五，批次并行训练 + 择优） ----------------
def _run_metric(row: WeightTrainRun, key: str) -> float | None:
    """取某 run 的 ``metrics_json`` 内数值键（icOut/icIn/nTrain）；缺 / 坏 JSON / 非数值 → ``None``（R7 桥接）。"""
    import json
    try:
        metrics = json.loads(row.metrics_json or "")
    except (json.JSONDecodeError, TypeError):
        return None
    value = metrics.get(key) if isinstance(metrics, dict) else None
    return float(value) if isinstance(value, (int, float)) else None


def weight_batch_ranking(runs: list[WeightTrainRun]) -> list[dict]:
    """派生排行榜（weight-multi-model-training §2.5）：succeeded 按 icOut 降序（tie: icIn→nTrain→runId 升序），
    非成功候选（running/queued/failed/cancelled/无 icOut）殿后；仅首个 succeeded 置 ``isBest``。

    无成功候选时全无 ``isBest``（前端择优 / 应用最佳按此禁用，§4.1）。
    """
    entries = [{
        "runId": r.id, "method": r.method, "variantLabel": r.variant_label or "",
        "status": r.status, "icOut": _run_metric(r, "icOut"), "icIn": _run_metric(r, "icIn"),
        "nTrain": _run_metric(r, "nTrain"),
        # 探索批次排行榜显示组合名（factor-combo-exploration §2.5）：候选自有组合成员（非探索候选为 None）。
        "members": _load_json(r.members_json, None),
    } for r in runs]
    scored = [e for e in entries if e["status"] == "succeeded" and e["icOut"] is not None]
    scored.sort(key=lambda e: (
        e["icOut"], e["icIn"] if e["icIn"] is not None else float("-inf"),
        e["nTrain"] if e["nTrain"] is not None else 0.0, -e["runId"]), reverse=True)
    scored_ids = {e["runId"] for e in scored}
    rest = [e for e in entries if e["runId"] not in scored_ids]
    ranking = scored + rest
    for idx, entry in enumerate(ranking):
        entry["isBest"] = idx == 0 and entry["runId"] in scored_ids
    return ranking


def weight_batch_status(runs: list[WeightTrainRun]) -> str:
    """批次派生状态（weight-multi-model-training §4.1，不单独落库）：存在 queued/running → running；
    全终态且 ≥1 succeeded → succeeded；无 succeeded 且有 cancelled → cancelled；否则（全 failed）→ failed。"""
    statuses = [r.status for r in runs]
    if any(s in ("queued", "running") for s in statuses):
        return "running"
    if any(s == "succeeded" for s in statuses):
        return "succeeded"
    if any(s == "cancelled" for s in statuses):
        return "cancelled"
    return "failed"


def weight_batch_out(batch: WeightTrainBatch, runs: list[WeightTrainRun]) -> dict:
    """WeightTrainBatch 行 + 候选 run 行 → 前端详情 dict（camelCase）。候选复用 ``weight_train_run_out``（不含 curve）。"""
    return {
        "batchId": batch.id, "label": batch.label, "instId": batch.inst_id, "bar": batch.bar,
        "startTs": batch.start_ts, "endTs": batch.end_ts, "horizon": batch.horizon,
        "members": _load_json(batch.members_json, []), "config": _load_json(batch.config_json, {}),
        "parentModelId": batch.parent_model_id, "factorSetId": batch.factor_set_id,
        "origin": batch.origin, "selectedRunId": batch.selected_run_id,
        # 探索批次标识 + 计划快照（factor-combo-exploration §2.5）：多方法批次 kind="multi_method"、explorePlan=None。
        "kind": getattr(batch, "kind", "multi_method") or "multi_method",
        "explorePlan": _load_json(getattr(batch, "explore_plan_json", None), None) or None,
        "status": weight_batch_status(runs),
        "candidates": [weight_train_run_out(r, with_curve=False) for r in runs],
        "ranking": weight_batch_ranking(runs),
        "createdTs": int(batch.created_at.timestamp()) if batch.created_at else 0,
        "updatedTs": int(batch.updated_at.timestamp()) if batch.updated_at else 0,
    }


def weight_batch_summary_out(batch: WeightTrainBatch, runs: list[WeightTrainRun]) -> dict:
    """WeightTrainBatch 行 → 前端列表概要 dict（camelCase；含候选计数 + 最优候选摘要，不含逐候选详情）。

    探索批次（factor-combo-exploration §2.5）额外回 ``kind`` 与 ``comboCount``（组合数，供列表「M 组合 × N 方法」
    概要）；多方法批次 ``kind="multi_method"``、``comboCount=None``。
    """
    ranking = weight_batch_ranking(runs)
    best = next((e for e in ranking if e.get("isBest")), None)
    plan = _load_json(getattr(batch, "explore_plan_json", None), None)
    combos = plan.get("combos") if isinstance(plan, dict) else None
    methods = plan.get("methods") if isinstance(plan, dict) else None
    return {
        "batchId": batch.id, "label": batch.label, "instId": batch.inst_id, "bar": batch.bar,
        "startTs": batch.start_ts, "endTs": batch.end_ts, "horizon": batch.horizon,
        "parentModelId": batch.parent_model_id, "factorSetId": batch.factor_set_id,
        "origin": batch.origin, "selectedRunId": batch.selected_run_id,
        "kind": getattr(batch, "kind", "multi_method") or "multi_method",
        "comboCount": len(combos) if isinstance(combos, list) else None,
        "methodCount": len(methods) if isinstance(methods, list) else None,
        "status": weight_batch_status(runs), "candidateCount": len(runs),
        "succeededCount": sum(1 for r in runs if r.status == "succeeded"), "best": best,
        "createdTs": int(batch.created_at.timestamp()) if batch.created_at else 0,
        "updatedTs": int(batch.updated_at.timestamp()) if batch.updated_at else 0,
    }


class WeightBatchCandidateIn(BaseModel):
    """一个批次候选：方法 + 可选超参变体（config 覆盖共享配置）+ 显示标签。"""

    method: _WEIGHT_METHODS
    config: dict[str, Any] = Field(default_factory=dict)
    variantLabel: str = Field(default="", max_length=64)


class WeightBatchStartReq(_FactorRange):
    """启动多模型训练批次入参（继承 ``_FactorRange`` 区间校验，weight-multi-model-training §4.1）。

    共享规格（members/instId/bar/区间/horizon/config）+ 候选数组（2..max_weight_batch_candidates，方法可重复
    以承载同方法多变体）。``origin`` 决定是否可回流组合（仅 builder），``factorSetId`` 弱溯源。
    """

    members: list[FactorMemberIn] = Field(min_length=1, max_length=12)
    config: dict[str, Any] = Field(default_factory=dict)
    horizon: int = Field(default=6, ge=1, le=100)
    candidates: list[WeightBatchCandidateIn] = Field(min_length=1, max_length=16)
    factorSetId: int | None = None
    name: str | None = Field(default=None, min_length=1, max_length=128)
    origin: Literal["builder", "model"] = "model"
    maxBars: int | None = Field(default=None, ge=10, le=200000)


class WeightBatchSelectReq(BaseModel):
    """择优入参：把某成功候选记为批次显式最优（写 ``selected_run_id``）。"""

    runId: int


# ---------------- 因子组合探索训练（factor-combo-exploration §四，组合 × 方法笛卡尔探索） ----------------
_EXPLORE_STRATEGIES = Literal["sizes", "topk_prefix", "leave_one_out", "manual"]


class WeightExploreStartReq(_FactorRange):
    """启动因子组合探索训练入参（继承 ``_FactorRange`` 区间校验，factor-combo-exploration §4.1）。

    在因子池上按 ``strategy``（子集大小穷举 / IC 排序前缀 / 留一法 / 手动指定）展开组合，与 ``methods`` 做笛卡尔
    积得候选集，复用批次运行时并行训练。``methods`` 按 ``_WEIGHT_METHODS`` Literal 在解析期校验合法性
    （P2-1：``_WEIGHT_METHODS`` 是 typing.Literal 非运行时集合，服务层不做 ``⊆`` 判定）+ 服务层去重。
    """

    pool: list[FactorMemberIn] = Field(min_length=1, max_length=12)
    strategy: _EXPLORE_STRATEGIES
    params: dict[str, Any] = Field(default_factory=dict)
    methods: list[_WEIGHT_METHODS] = Field(min_length=1, max_length=11)
    config: dict[str, Any] = Field(default_factory=dict)
    horizon: int = Field(default=6, ge=1, le=100)
    factorSetId: int | None = None
    origin: Literal["builder", "model"] = "builder"
    maxBars: int | None = Field(default=None, ge=10, le=200000)


def weight_explore_start_out(batch: WeightTrainBatch, plan_json: dict,
                             candidates: list[dict]) -> dict:
    """探索启动响应（factor-combo-exploration §4.1）：批次指针 + 计划快照 + 逐候选 roster（含组合展示名）。"""
    return {
        "batchId": batch.id, "parentModelId": batch.parent_model_id, "label": batch.label,
        "kind": "explore", "origin": batch.origin, "explorePlan": plan_json,
        "candidates": candidates,
    }


# ---------------- 策略与因子挖掘（strategy-factor-discovery §四） ----------------
class DiscoveryStartReq(BaseModel):
    """启动挖掘任务入参。

    只在解析期做**形状与硬边界**校验；语义夹取（间隔 / 候选数 / 窗口根数按 settings 上下界）
    与轴合法性（D-6：未实现的轴一律 400 ``DISCOVERY_AXIS_UNSUPPORTED``）统一在
    ``discovery/service.py::normalize_config`` 收口——单一真源，避免两处各夹一套。
    """

    instId: str = Field(min_length=1, max_length=32)
    bar: str = Field(min_length=1, max_length=8)
    label: str | None = Field(default=None, max_length=128)
    windowBars: int = Field(default=4000, ge=200, le=200000)
    axes: list[str] = Field(default_factory=lambda: ["factor"], min_length=1, max_length=4)
    horizon: int = Field(default=6, ge=1, le=100)
    candidatesPerIteration: int = Field(default=40, ge=1, le=1000)
    iterationIntervalS: float = Field(default=300.0, gt=0)
    dataRefreshS: float = Field(default=21600.0, ge=0)
    windowMode: Literal["rolling", "fixed"] = "rolling"
    isRatio: float = Field(default=0.7, ge=0.5, le=0.9)
    maxIterations: int = Field(default=0, ge=0)
    maxRuntimeHours: float = Field(default=0.0, ge=0)
    maxKept: int = Field(default=200, ge=1, le=2000)
    autoResume: bool = True
    autoPromote: bool = False
    revalidateOnRefresh: bool = True
    seed: int | None = Field(default=None, ge=0)
    llm: dict[str, Any] = Field(default_factory=dict)
    thresholds: dict[str, Any] = Field(default_factory=dict)
    evolution: dict[str, Any] = Field(default_factory=dict)


class DiscoveryBacktestReq(BaseModel):
    """候选一键回测入参（区间必填，与既有回测端点同口径）。"""

    startTs: int = Field(ge=0)
    endTs: int = Field(ge=0)

    @model_validator(mode="after")
    def _check_range(self):
        if self.endTs <= self.startTs:
            raise ValueError("endTs 必须大于 startTs")
        return self


class ErrorBody(BaseModel):
    code: str
    message: str


class ErrorResponse(BaseModel):
    error: ErrorBody
