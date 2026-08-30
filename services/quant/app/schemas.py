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


AiTradeHorizonProfileId = Literal[
    "hourly_swing", "intraday_swing", "short_swing", "trend_hold", "custom"]


class AiTradeHorizonIn(BaseModel):
    """交易周期模式入参（ai-trade-holding-horizon-modes §4.2）：一次选择、全局生效的结构化契约。

    它一次性决定执行周期建议、唤醒节奏建议、成本与先验的前瞻窗口，以及三条由系统确定性执行的换手
    约束（最短主动平仓窗口 / 离场连续确认 / 换手迟滞）。**字段整体缺省 ⇒ 服务端按 settings 装配默认
    模式**（D-2，与 baseRules / profitGuard 的「缺省即开启」惯例一致）；`enabled=false` 是用户显式
    关闭本层的唯一途径。

    校验只在本层做（422），服务端 `_resolve_horizon` **只做缺省回填、不复夹**（沿用
    `_resolve_position_step` 的 `[R-2]` 惯例）——值域的唯一真源就是下面这些 `Field` 约束。
    """

    enabled: bool = True
    profile: AiTradeHorizonProfileId | None = None   # 缺省 → settings.ai_trade_horizon_default_profile
    minHoldSeconds: float | None = Field(default=None, ge=0, le=604800)   # 7 天硬顶
    exitConfirmRounds: int | None = Field(default=None, ge=1, le=10)
    turnoverHysteresisK: float | None = Field(default=None, ge=0, le=10)
    # [P1-9] 这两项**不是纯展示字段**：`targetHoldMaxSeconds` 是 `expectedHoldBars` 的唯一派生源，
    # 覆盖它会连带改写成本模型的资金费期数与信号先验的前瞻窗口；`targetHoldMinSeconds` 同时是
    # `confirmWindowSeconds`（G2 墙钟上界）与 `minHoldSeconds` 默认值的派生源。回执会同时镜像这两个
    # 派生量，使「我到底改了什么」可被逐字段核对。
    targetHoldMinSeconds: float | None = Field(default=None, gt=0, le=2592000)   # 30 天
    targetHoldMaxSeconds: float | None = Field(default=None, gt=0, le=2592000)
    # true ⇒ 把 executor 的 `max_hold_bars` 设为 `expectedHoldBars`（默认关，D-6）。它与 §2.3.3 的
    # `max_hold_bars` 豁免构成闭环：上限已到时本层整体解除，绝不出现「系统要强平、本层却拦着 Agent
    # 先走一步」的对冲态。
    enforceMaxHold: bool = False
    # ---- 开仓侧换手纪律的两个可选覆盖（ai-trade-turnover-budget-and-backstop §4.2）----
    # `null` ⇒ 按契约派生；`0` ⇒ **显式关闭**该闸（不是「无限长 / 无限多」）。
    # 速率没有第三个旋钮：容量恒由速率派生（`max(2, ceil(perDay × burstFrac))`），否则弹窗
    # 承诺的那个数与运行期真正执行的数可以分叉（D-1）。
    reentryCooldownSeconds: float | None = Field(default=None, ge=0, le=604800)
    turnoverBudgetPerDay: float | None = Field(default=None, ge=0, le=1000)
    # 交易所侧兜底自愈模式（ai-trade-backstop-repair-and-net-backtest §4.3）。
    # `null` / 缺省 ⇒ 服务端按 `ai_trade_backstop_repair_default_mode` 回填（只回填、不复夹）；
    # 值域在本层 422——`arm` 是本仓库唯一一处「允许系统在你不在场时向交易所发出委托」的授权点，
    # 它必须是一次显式选择，不能靠一个拼错的字符串滑进去。
    backstopRepair: Literal["plan", "arm"] | None = None

    @model_validator(mode="after")
    def _check_target_range(self) -> "AiTradeHorizonIn":
        """边界一次性校验（进入内部后信任）：目标区间不可倒置，违反即 422。"""
        lo, hi = self.targetHoldMinSeconds, self.targetHoldMaxSeconds
        if lo is not None and hi is not None and lo > hi:
            raise ValueError("目标持仓下界不可大于上界")
        return self


class AiTradeAcceptanceRowOut(BaseModel):
    """周期验收卡的一行判据（horizon-acceptance §2.4.4）。

    `value` 可为 `None`（该行「算不出」）——与 `0` 是两个结论，故**绝不**给默认值 0.0：
    前者是「没算成」，后者是一个真实读数，UI 的文案与颜色都不同。
    """

    key: str
    label: str
    verdict: str                       # pass | fail | insufficient
    value: float | None = None
    unit: str                          # seconds | ratio | count | usd
    display: str
    target: str
    note: str | None = None
    inPositionValue: float | None = None   # 第 3 行的在场口径对照值
    basis: str | None = None               # 第 5 行的资金费口径


class AiTradeExitSourceRowOut(BaseModel):
    """退出来源分布的一行；`winRate` 在样本 < 5 时为 `None`（绝不用 2 笔冒充统计结论）。"""

    source: str
    count: int
    share: float | None = None
    netUsd: float
    avgHoldSeconds: float | None = None
    winRate: float | None = None


class AiTradeExitMixOut(BaseModel):
    """退出来源分布（**顶层键**，[P0-4]：`acceptance` 为 null 时它仍可非空——关掉周期模式跑
    对照臂是 L1 的必要臂，数据记了却看不到等于没记）。"""

    totalExits: int
    bySource: list[AiTradeExitSourceRowOut] = Field(default_factory=list)


class AiTradeFundingOut(BaseModel):
    """资金费账本读数（horizon-acceptance §2.2.2）。

    `fundingCostUsd` 为 `None` 与为 `0` 是两个结论：前者是「没算成」（口径不可得），后者是「真的
    没跨过结算点」。`intervalS` 在 `intervalKnown=false` 时恒 `None`——绝不把上游的 8h 兜底值当
    读数展示（[P0-1]）。
    """

    enabled: bool
    basis: str                          # settled+accrued | accrued | unavailable
    accruedClosedUsd: float = 0.0
    accruedOpenUsd: float | None = None
    settledUsd: float | None = None
    fundingCostUsd: float | None = None
    crossings: int = 0
    intervalKnown: bool = False
    intervalS: float | None = None
    lastRateBps: float | None = None
    staleTicks: int = 0
    unknownIntervalTicks: int = 0
    capHits: int = 0
    settledTrusted: bool = False
    note: str = ""


class AiTradeAcceptanceSymbolOut(BaseModel):
    """多标的分组里单个成员的验收摘要（R-10）；`verdict` 为该成员**自己**的结论。"""

    instId: str
    roundTrips: int
    netPnlUsd: float
    verdict: str | None = None


class AiTradeAcceptanceOut(BaseModel):
    """周期验收卡（五行判据 + 退出来源镜像 + 派生诊断）。

    `exitMix` / `funding` 与顶层同名键是**同一个对象**（[P0-4] / D-11）：顶层保证对照臂可见，
    镜像保证 tooltip 与诊断能就近取数；两处各算一份即视为实现缺陷。
    """

    profileId: str
    title: str
    verdict: str                        # pass | fail | insufficient
    sampleFloor: int
    roundTrips: int
    truncatedSamples: int = 0
    sampleNote: str = ""
    rows: list[AiTradeAcceptanceRowOut] = Field(default_factory=list)
    exitMix: AiTradeExitMixOut | None = None
    funding: AiTradeFundingOut | None = None
    diagnostics: list[str] = Field(default_factory=list)
    # R-10：多标的分组的成员级摘要——合并只用于**总判定**，但「一个赚一个亏被平均成还行」是这张
    # 卡最危险的读法。单标的分组 ⇒ 整键缺席（那时它与总判定逐字相同，多挂一行只是噪音）。
    bySymbol: list[AiTradeAcceptanceSymbolOut] | None = None


class AiTradeAcceptanceThresholdsOut(BaseModel):
    """弹窗的「验收门槛预览」（`GET /ai-trade/horizon-profiles` 的加法块）。

    `custom` 条目的派生阈在 `targetHold*` 未填时为 `null` ⇒ 前端显示「按你填的目标区间派生」。
    """

    sampleFloor: int
    minAvgHoldSeconds: float | None = None
    minWithinTargetRate: float | None = None
    maxTurnoverPerDay: float | None = None
    maxFeeToGross: float
    requiresPositiveNetAfterFunding: bool = True


class AiTradeHorizonProfileOut(BaseModel):
    """`GET /ai-trade/horizon-profiles` 的单个预设条目（§4.1）。

    [P1-8] 除 `id` / `title` / `description` 外**全部可空**：`custom` 条目这些格子恒为 `null`
    （§2.2 表里它们是「不联动 / 用户填」）。若声明为非空，前端渲染 custom 卡片时会拿到一组无意义
    数字并据此联动 bar 与唤醒周期。
    """

    id: str
    title: str
    description: str
    targetLabel: str | None = None
    bar: str | None = None
    wakeIntervalMinutes: float | None = None
    minHoldSeconds: float | None = None
    exitConfirmRounds: int | None = None
    turnoverHysteresisK: float | None = None
    targetHoldMinSeconds: float | None = None
    targetHoldMaxSeconds: float | None = None
    # 两个**派生量的预览值**（按本条目自带 bar 算）；实际生效值按用户所选 bar 现算。
    expectedHoldBars: int | None = None
    confirmWindowSeconds: float | None = None
    leverageMaxHint: float | None = None
    # 开仓侧换手纪律的三个**预览**值（turnover-budget §4.1）；`custom` 恒为 `null`。
    reentryCooldownSeconds: float | None = None
    turnoverBudgetPerDay: float | None = None
    turnoverBudgetCapacity: float | None = None
    # 验收门槛预览（horizon-acceptance §4.1）：让用户在**选模式那一刻**就看见「这套配置将按什么
    # 标准被判定」，而不是等跑完一周才发现门槛是什么。全部由后端下发，前端不复制任何一个数。
    acceptance: AiTradeAcceptanceThresholdsOut | None = None


class AiTradeBackstopRepairModeOut(BaseModel):
    """自愈控件的一个选项（`plan` / `arm`）——标签与说明**全部由后端下发**，前端一个字都不硬编。"""

    id: str
    label: str
    detail: str


class AiTradeBackstopRepairOut(BaseModel):
    """`GET /ai-trade/horizon-profiles` 的自愈块（backstop-repair §4.2，加法字段）。

    [P0-3] **本块必须在这里显式开字段**：该端点声明了 `response_model`，`profiles_wire` 多下发的
    键会被 FastAPI **静默剔除**；而前端规则是「整块缺席 ⇒ 不渲染控件」，两条一叠就是「控件永远
    不出现，且没有任何前端用例会红」（手造 wire 的用例守不住这一格，只有真端点用例能）。
    """

    defaultMode: str
    modes: list[AiTradeBackstopRepairModeOut] = Field(default_factory=list)
    note: str
    maxPerEpoch: int
    maxPerSession: int


class AiTradeHorizonProfilesOut(BaseModel):
    """模式预设表回执（只读、不守 AI 总闸——读预设表不下任何单）。

    `estimatedCostBps` 为按当前配置常数估的往返成本（**非实时盘口**，UI 须标「约」）；
    `lossReleaseFrac` / `lossReleaseReturnPctFallback` 下发给前端，使契约摘要第 4 行能按当前表单值
    **现算**解除阈而不是写死一个算例（[P1-10]）。
    """

    enabled: bool
    defaultProfile: str
    estimatedCostBps: float | None = None
    lossReleaseFrac: float
    lossReleaseReturnPctFallback: float
    # 三个只读运维标志（turnover-budget §4.1 / [P2-4]）：UI 据此如实说明两道新闸是否真的开着、
    # 以及多久向交易所核实一次兜底——`backstopPollSeconds` 尤其不能让前端硬抄一个 2 分钟。
    turnoverGateEnabled: bool = True
    backstopCheckEnabled: bool = True
    backstopPollSeconds: float = 120.0
    # 交易所侧兜底自愈块（backstop-repair §4.2）；运维总闸关 ⇒ **整块缺席** ⇒ 前端不渲染控件。
    backstopRepair: AiTradeBackstopRepairOut | None = None
    # 离场优先通道的只读说明块（exit-priority §6.2）；总闸关 ⇒ `None` ⇒ 前端整块不渲染。
    # **形状刻意用 `dict`**：档位标签与那句安全承诺全部由后端下发，用一个逐字段声明的模型反而会在
    # 下一次加字段时静默吞掉新键（同类端点 `/stop`、`/backstop/repair` 返回裸 dict 正是为此）。
    exitPriority: dict | None = None
    profiles: list[AiTradeHorizonProfileOut] = Field(default_factory=list)


class AiTradeExitOut(BaseModel):
    """`POST /ai-trade/{gid}/member/{inst}/exit` 的回执形状（exit-priority §6.2）。

    ⚠️ **只作文档 schema，端点返回裸 `dict`、不绑 `response_model`**（【R:P2-3】）：同类端点
    `/ai-trade/{gid}/member/{inst}/stop` 与 `.../backstop/repair` 的返回标注都是裸 `dict`，正是
    为绕开「`response_model` 静默吞掉新 wire 键」这个本仓库出过事的形态。
    """

    ok: bool
    instId: str
    groupId: str = ""
    flattened: dict | None = None
    positionAfter: str = "flat"
    restingCancelled: bool = False
    message: str = ""


class AiTradeExperienceIn(BaseModel):
    """经验模式入参（ai-trade-experience-context-graph §五）。

    **字段整键缺省 ⇒ 按 `ai_trade_experience_default_on`（默认 True）装配**——沿用
    `baseRules` / `profitGuard` / `tradingHorizon` / `discipline` 的成文惯例：「老客户端没发这个
    字段」与「用户亲手取消了勾选」是两件不同的事，把它们合成一个判据用户就再也关不掉这个功能，
    而界面上那个复选框看起来是生效的。解析点**单一**：`experience/rules.resolve_experience`。

    **★ E-15（评审 P1-9）· `enabled:false` = 既不读也不写**（关就是全关）：`ExperienceRuntime.writing`
    依赖 `rule.enabled`，关掉读的同时也关掉了采集。这是一个被明确决定并写在 UI 上的语义——
    只想「用但不沉淀」的用户走 `write:false`（只读模式）。**没有**「不读但写」的第三态（§十一.10）。
    """

    enabled: bool = True
    scope: Literal["symbol", "global"] = "symbol"
    write: bool | None = None       # None = 按服务端写闸（`ai_trade_experience_write_enabled`）


class AiTradeDirectionIn(BaseModel):
    """方向对称性入参（ai-trade-directional-symmetry §7.1）。

    **字段整键缺省 ⇒ 服务端按 settings 装配开启态**——沿用 `baseRules` / `profitGuard` /
    `tradingHorizon` / `discipline` / `experience` 的成文惯例：「老客户端没发这个字段」与
    「用户亲手取消了勾选」是两件不同的事。`{"contrarianEnabled": false}` 是显式关闭的唯一途径。

    值域**一次性在本层校验**（越界 422）；服务端 `_resolve_direction` 只做缺省回填、不复夹
    （沿用 `_resolve_position_step` 的 [R-2] 惯例）。

    ⚠️ **作用域只有 `steady`(2) 与 `balanced`(1)**：`aggressive` 与 `off` 的 `minConsensus`
    都是 0，共识门禁根本不触发，逆势通路在那两档是死配置。
    """

    contrarianEnabled: bool = True
    contrarianMinConfidence: float | None = Field(default=None, gt=0, le=1)
    contrarianMaxOpposingVotes: int | None = Field(default=None, ge=0, le=8)
    contrarianMinEvidenceChars: int | None = Field(default=None, ge=0, le=1000)
    contrarianMaxPerSession: int | None = Field(default=None, ge=0, le=50)
    contrarianSizeMult: float | None = Field(default=None, gt=0, le=1)

    def overrides(self) -> dict:
        """非空覆盖字段（camelCase）。键集与 `api/ai_auto._DIRECTION_TO_SETTING` 一一对应。"""
        return {key: value for key, value in self.model_dump().items()
                if key != "contrarianEnabled" and value is not None}


class AiTradeDisciplineIn(BaseModel):
    """交易纪律预设入参（ai-trade-discipline-preset-and-integrity §5.1）。

    **字段整键缺省 ⇒ 服务端按 `ai_trade_discipline_default_preset`（默认 `balanced`）装配**——沿用
    `baseRules` / `profitGuard` / `tradingHorizon` 的成文惯例，老客户端 / 桌面旧包同样受纪律保护。
    `{"enabled": false}` 或 `{"preset": "off"}` 是用户显式关闭的唯一途径。

    覆盖字段的值域**一次性在本层校验**（越界 422）；服务端 `_resolve_discipline` 只做缺省回填、
    不复夹（沿用 `_resolve_position_step` 的 [R-2] 惯例）。`0` 一律表示「显式关闭该闸」，
    `null` 表示「按预设」。

    [P1-5] `enabled=false` / `preset="off"` 与任一覆盖字段**同时出现即 422**（`AI_TRADE_DISCIPLINE_
    CONFLICT`，在 `api/ai_auto._resolve_discipline` 抛）：静默丢弃用户显式填的数是本仓库明令禁止的
    形状。校验放在 API 层而不是这里，是为了让错误体能点名「哪个字段与哪个开关矛盾」。
    """

    enabled: bool = True
    preset: Literal["steady", "balanced", "aggressive", "off"] | None = None
    edgeGateK: float | None = Field(default=None, ge=0, le=10)
    minConsensus: int | None = Field(default=None, ge=0, le=8)
    minConfidence: float | None = Field(default=None, ge=0, le=1)
    volTargetAnnual: float | None = Field(default=None, ge=0, le=3)
    stopAtrMult: float | None = Field(default=None, ge=0, le=20)      # 0=显式关闭 ATR 止损覆盖
    brakeSlowDdPct: float | None = Field(default=None, ge=0, le=1)
    brakeHaltDdPct: float | None = Field(default=None, ge=0, le=1)
    brakeLossStreak: int | None = Field(default=None, ge=0, le=50)    # 0=显式关闭

    def overrides(self) -> dict:
        """非空覆盖字段（camelCase）。键集与 `discipline.OVERRIDE_TO_SETTING` 一一对应。"""
        return {key: value for key, value in self.model_dump().items()
                if key not in ("enabled", "preset") and value is not None}


class AiTradeDisciplineRowOut(BaseModel):
    """生效纪律自检的一行（轴 B；`status.disciplines` 与 `group.discipline.rows` 同构）。

    `threshold` / `firedCount` **算不出一律 `None`**，绝不填 0 冒充读数——只报「装配了」而不报
    「拦了多少」的面板，在「闸门把每一单都拦了」这个最危险的断面上与「一切正常」长得一模一样。
    """

    key: str
    label: str
    armed: bool
    threshold: str | None = None
    source: str                       # preset | config | rule | runtime
    note: str | None = None           # `armed=false` 时**必填**（为什么没生效、怎么开）
    firedCount: int | None = None     # 本会话累计触发次数；算不出 ⇒ None
    tone: str | None = None           # ok | warn | danger（呈现层用；缺省 ⇒ 按 armed 取色）


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
    # 研判深度档（ai-trade-deep-reasoning-chain §4.4）：留空 ⇒ 用 settings 默认档。越界值直接
    # 422（`Literal` 校验），**不做静默夹紧**——一个拼错的深度档静默回落会让用户以为自己开了
    # 深度档。该字段随 `start_wire` 原样落库，冷续跑重跑同一条解析链即自动同档（用例 S-8）。
    # ⚠️ 仅实盘 tab 下发（回测档走设置页的 AI_TRADE_REASONING_DEPTH_BACKTEST，§3.6 成本护栏）。
    reasoningDepth: Literal["off", "standard", "deep", "exhaustive"] | None = None
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
    # 交易周期模式（默认开启，同上「字段缺省 ⇒ 开启」语义，解析点 `_resolve_horizon`）；
    # `{"enabled":false}` ⇒ 该会话不装配。**实盘与回测两处启动体都接该字段**（同一解析点，
    # 否则用户拿回测调出来的参数在实盘失真——`base_rules` P1-4 / `profit_guard` I-3 的成文教训）。
    tradingHorizon: AiTradeHorizonIn | None = None
    # 交易纪律预设（默认开启，同上「字段缺省 ⇒ 按服务端默认预设装配」语义，解析点
    # `_resolve_discipline`，且它**必须排在所有其它 `_resolve_*` 之前**——预设表里含
    # `ai_trade_profit_guard_hard_tp_return_pct`，派生副本晚一步产生那一行预设就是死字母（[P1-2]）。
    discipline: AiTradeDisciplineIn | None = None
    # 经验模式（默认开启，同上「字段缺省 ⇒ 开启」语义，解析点 `experience/rules.resolve_experience`）；
    # `{"enabled":false}` ⇒ 该会话**既不读取也不沉淀**经验（★ E-15）。`start_wire` 原文含本键，
    # 故冷续跑（`resume.py` 的 S2 重跑 `build_start_request`）自动同配置，无需额外代码。
    experience: AiTradeExperienceIn | None = None
    # 方向对称性 / 逆势通路（默认开启，同上「字段缺省 ⇒ 开启」语义，解析点 `_resolve_direction`）；
    # `{"contrarianEnabled":false}` ⇒ 该会话不装配逆势通路（等价本特性合入前的共识门禁）。
    # **实盘与回测两处启动体都接该字段、走同一解析点**——否则用户拿回测调出来的参数在实盘失真。
    direction: AiTradeDirectionIn | None = None

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


# ---- 暂停 / 继续 / 结束（ai-trade-pause-resume §五）----
# ⚠️ **本组只定义请求体，不定义 response_model**：AI-trade 的三个生命周期端点一律 `-> dict`。
# 理由是本仓库的成文教训——`response_model` 会把回执里的新键**静默吞掉**（`OkxAccountPosition`
# 吞掉 `posId` 就是同一形状），而这三个回执（逐成员 verdict / 兜底覆盖 / orphanPositions）正是
# 用户拿着真钱做决定的唯一依据，少一个键就是少一条事实。
class AiTradePauseIn(BaseModel):
    """暂停请求。两键皆可省：`holdPosition` 缺省取 `ai_trade_pause_hold_position_default`。"""

    holdPosition: bool | None = None      # True=保留持仓暂停（默认）；False=平仓后暂停
    reason: str = "user"                  # 稳定枚举，见 spec §3.0（前端只发 "user"）


class AiTradeResumeIn(BaseModel):
    """继续请求。

    `onDrift` 已在 v2 删除（原值域 abort | flatten）：`size_increased` / `side_flipped` /
    `foreign_position` / `opened_offline` 四格的共同事实是**交易所上那部分名义额里有我们没开过
    的**，替用户平掉它等于未经授权动用他的资金。漂移只有「拒绝」一条路。
    """

    acknowledge: bool = False             # 复核框勾选态；verdict=needs_confirm 时必须为 True


class AiTradeEndIn(BaseModel):
    """结束（归档）请求。

    [P1-12]：`flatten` **没有默认值**。带仓时必填，且选「不平仓」还要
    `acknowledgeOrphanPosition=true`——否则「结束」会造出一个「本地已归档、交易所上仍在」的
    孤儿仓，而本轮的立论正是「今天已经存在孤儿持仓，只是没人告诉用户」。
    """

    flatten: bool | None = None
    acknowledgeOrphanPosition: bool = False


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
    # 前瞻根数覆盖（ai-trade-holding-horizon-modes §2.6 / §4.5）：缺省仍取
    # `settings.ai_trade_cost_expected_hold_bars`。装配了持仓周期模式的会话其 `horizonBars` 会变成
    # 12/18/24，而库里按全局 8 建的快照会被 C-3 判 `horizon_mismatch` 并整份丢弃——那是**正确行为**
    # （拿 8 根前瞻的胜率去给 12 根持仓做 EV 分子是错的），但运维必须有一条按目标模式重建的出路。
    horizonBars: int | None = Field(default=None, ge=1, le=1000)


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
    # 交易周期模式回执镜像（null=未装配）：为**服务端解析后的生效值**，且必须同时带上两个派生量
    # `expectedHoldBars` / `confirmWindowSeconds`（[P1-9]/[P0-3]）——只回显入参等于把派生关系藏起来。
    tradingHorizon: dict | None = None
    # 交易纪律预设回执（[P1-7]：承载体是本模型，**不是** `AiTradeStatusOut`——`/ai-trade/start` 与
    # `/ai-trade/status` 都没有 `response_model`，wire 的真源是 `manager.group_status()`）。
    # 形状：`{preset, presetLabel, adjustments: [{field,from,to,reason}], rows: [...]}`；
    # 整轴未装配 ⇒ `{"preset": "off", ...}`（而不是 null——「关着」也是一个要如实回答的事实）。
    discipline: dict | None = None
    # 方向对称性回执镜像（directional-symmetry §7.1）：为**服务端解析后的生效值**；
    # `null` = 未装配（总闸关 / 用户显式关闭 / 该预设的 `minConsensus<=0` 使门禁本就不触发）。
    direction: dict | None = None


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
    # 交易周期模式：与实盘同字段同语义、同一 `_resolve_horizon` 解析（回测侧另叠一层运维总闸
    # `ai_trade_horizon_backtest_enabled`）。回测按根推进，故最短持仓窗口的分辨率是一根 K 线——
    # 该免责由 `hold_horizon_text.format_backtest_notes` 写进结果 notes 并在 UI 上显示。
    tradingHorizon: AiTradeHorizonIn | None = None
    # 交易纪律预设：与实盘**同一个** `_resolve_discipline`（R-3）。两处各解析一次的后果已有成文
    # 前科（`base_rules` P1-4 / `profit_guard` I-3）：用户拿回测调出来的参数在实盘失真。
    # 优先级写死为「显式请求字段（`minConsensus`）> 预设 > config 默认」，见 `backtest_manager`。
    discipline: AiTradeDisciplineIn | None = None
    # 经验模式：字段接住但 **v1 恒不装配**（§十一.2）——回测的成本口径与实盘不同，把实盘经验喂进
    # 回测会让回测结论无法与历史回测对比；且回测跑得快，几分钟就能生成上万条事件淹没资产。
    # 接住它是为了「前端两个 tab 共用同一张卡片」以及下一轮开放时不必改 wire。
    experience: AiTradeExperienceIn | None = None
    # 方向对称性 / 逆势通路：与实盘**同字段同语义、同一 `_resolve_direction` 解析**——回测正是
    # 唯一能对「开 vs 不开逆势通路」做净额对照的地方，两侧不同源就等于那个对照跑的不是实盘参数。
    direction: AiTradeDirectionIn | None = None


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
    # 成本口径三键（cost-truth-and-net-pnl §5.6，纯加法可选）：realizedPnlUsd 已是**净额**，
    # feeUsd 是同窗口的已付手续费，grossPnlUsd 是毛额还原——三者并列才能让「数字为什么下移」
    # 被逐笔解释；costBasisMixed 说明该组跨 run 出现了两种口径。
    feeUsd: float = 0.0
    grossPnlUsd: float = 0.0
    costBasisMixed: bool = False


class AiTradeHistoryListOut(BaseModel):
    """GET /ai-trade/history 响应：分页历史会话列表（按 startedTs DESC，排除当前运行组）。"""

    items: list[AiTradeHistoryEntry]
    total: int
    limit: int
    offset: int


class AiTradeTradabilityOut(BaseModel):
    """GET /ai-trade/tradability 响应（tradability-and-open-funnel §5.1）。

    **判据与文案全部由后端下发，前端一个字都不重写**——本仓库已有过前后端各存一份口径导致静默
    漂移的事故。缺席语义：任一子块算不出即该键为 `null`（**绝不填 0 冒充**）；`level=="unknown"`
    时除 `headline` / `samples` / `source` 外允许全 `null`。

    ⚠️ 本模型用 `dict | None` 承载 `atrBps` / `costBps` 等子块而不是嵌套模型，是**有意的**：
    `response_model` 会静默吞掉未声明的 wire 键（本仓库成文教训），子块用宽类型可保证后端新增
    读数时不会被 FastAPI 悄悄裁掉。
    """

    instId: str
    bar: str
    preset: str = ""
    level: str
    samples: int
    source: str | None = None
    asOfTs: int | None = None
    atrBps: dict | None = None
    costBps: dict | None = None
    costConfidence: dict | None = None
    degradedMultApplied: float | None = None
    requiredBps: dict | None = None
    vetoRate: dict | None = None
    stopBps: dict | None = None
    stopInsideCostRate: dict | None = None
    # [P0-6] 风险单位体检（realized-edge-calibration 轴 E-0）。**必须在此显式声明**——本模型是一张
    # 显式字段清单，未声明的 wire 键会被 `response_model` 静默裁掉，那一轴上线即死键（见上方 ⚠️）。
    # 用 `dict | None` 而不是嵌套模型，理由同上一段。算不出 ⇒ `None`。
    bracket: dict | None = None
    alternatives: list[dict] = Field(default_factory=list)
    headline: str = ""
    advice: str | None = None
    disclaimer: str = ""


class AiTradePresetComparisonGroup(BaseModel):
    """一档纪律预设的历史表现（轴 E）。`conclusive=false` ⇒ 前端必须显式写「样本不足，不下结论」。"""

    preset: str
    samples: int
    conclusive: bool
    netPnlUsd: float
    roundTrips: int
    winRate: float | None = None
    edgeVetoRate: float | None = None
    openConversion: float | None = None


class AiTradePresetComparisonOut(BaseModel):
    """GET /ai-trade/history/preset-comparison 响应：按预设分组的历史对照卡。"""

    groups: list[AiTradePresetComparisonGroup] = Field(default_factory=list)
    minSamples: int


class AiTradeProfitabilityOut(BaseModel):
    """GET /ai-trade/history/profitability 响应（realized-edge-calibration §5.1）。

    五条契约纪律：① 算不出的量一律 `null`，**绝不填 0**；② 每个比例类读数所在的对象必带它自己的
    分母（`trips` / `n` / `coverage` / `rSamples` / `feeTrips`）；③ `verdict.detail` 由后端生成
    （前端不拼数字）；④ 口径推定用 `costBasisAssumedTrips`（**计数**，不是布尔——布尔表达不了
    「89% 的样本靠推定」这件事）；⑤ `medianCostBps` / `travelToCostRatio` 与 `feeCoverage`
    **必须在同一个对象里**下发。

    ⚠️ 子块一律用 `dict` / `list[dict]` 宽类型（同 `AiTradeTradabilityOut` 的成文理由）：
    `response_model` 会静默吞掉未声明的 wire 键，宽类型可保证后端新增读数时不会被悄悄裁掉。
    """

    sampleTrips: int
    minTrips: int
    conclusive: bool
    costBasisAssumedTrips: int = 0
    pooled: dict = Field(default_factory=dict)
    risk: dict = Field(default_factory=dict)
    slices: dict = Field(default_factory=dict)
    calibration: dict = Field(default_factory=dict)
    verdict: dict = Field(default_factory=dict)


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
    # v2（cost-truth P1-3）：分子分母**同为毛额**（均不含手续费），比值维度自洽且不随净额总闸变化；
    # 手续费口径请看同级 feeUsd / realizedPnlUsd（后者已是净额）。
    peakPnlUsd: float = 0.0
    capturedPnlUsd: float = 0.0
    captureRatio: float = 0.0
    capturePairs: int = 0
    noPeakCount: int = 0
    feeUsd: float = 0.0
    grossPnlUsd: float = 0.0
    costBasisMixed: bool = False
    # 周期验收闭环（horizon-acceptance [P1-9]）：**在后端合并各成员 run 的原始计数器后判定一次**，
    # 故这里是一张组级卡而不是 N 张。任一成员缺 `acceptanceInput`（老记录）⇒ 两键缺席 ⇒ 前端整卡
    # 不渲染（绝不用半组数据冒充整组结论）。
    horizonAcceptance: AiTradeAcceptanceOut | None = None
    exits: AiTradeExitMixOut | None = None
    funding: AiTradeFundingOut | None = None


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
    costBasis: str = "gross"        # 该成员 run 的盈亏口径（"net" 已扣双边手续费）
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
    # 口径是否为**推定**（cost-truth P0-3）：老 run 的 config_json 缺 costBasis 时按
    # ai_trade_history_legacy_cost_basis 推定，UI 必须把「推定」与「已知」分开显示。
    costBasisAssumed: bool = False
    members: list[AiTradeHistoryMember]
    summary: AiTradeHistorySummary


# ---------------- Response serializers ----------------
def _explain_envelope(raw: str | None) -> dict | None:
    """explain_json → dict **原样**（含「仅 meta」的内部信封）；缺列 / 空 / 坏 JSON 安全回 None。

    与 `_public_explain` 的区别只有一条：本函数**不**按 `kind` 过滤。`meta` 里搭车的随行键
    （`wallTs` / `clOrdId` / `exitSource` …）在没有 `kind` 的内部信封里同样有效，按 explain 的
    对外口径过滤会把它们一起丢掉。
    """
    if not raw:
        return None
    import json
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _public_explain(envelope: dict | None) -> dict | None:
    """对外的 explain：**仅 meta** 的内部信封（无 `kind`）一律隐藏为 None，与既有 wire 一致。

    收已解析的信封而不是原始字符串，是为了让 `trade_out` 只解析一次——同一段 JSON 解两遍，
    迟早会有一处忘了跟上另一处的口径。
    """
    return envelope if envelope is not None and envelope.get("kind") else None


def trade_out(t: Trade) -> dict:
    envelope = _explain_envelope(getattr(t, "explain_json", None))
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
        # 轴 D（discipline-preset-and-integrity D-3）：`ts` 已是 **bar 对齐**秒（图表标记读它），
        # `wallTs` 才是真实墙钟秒（列表排序 / 悬停 / 导出读它）。`live_trades` 表没有这一列，值由
        # `session._inject_explain_meta` 搭 explain 信封落库，在此提回顶层；老数据缺键 ⇒ None ⇒
        # 前端一律 `wallTs ?? ts` 兜底。
        "wallTs": ((envelope or {}).get("meta") or {}).get("wallTs"),
        # realized-edge-calibration 轴 A：往返行要显示的那一列。与上面 `wallTs` 逐字同款——搭
        # explain 信封落库、读侧在此提回顶层；其余结算键由查询层直接读 `explain.meta`。
        # 老数据 / 总闸关 / 算不出（无止损、ct_val 未知）⇒ None ⇒ 前端整列不渲染，**绝不填 0**。
        "rMultiple": ((envelope or {}).get("meta") or {}).get("rMultiple"),
        # explain 键恒在（缺列 / 坏 JSON → None），与既有恒含全键风格一致（§5.2 / P2-6）。
        "explain": _public_explain(envelope),
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
def key_preview(api_key: str) -> str:
    """密钥脱敏预览：仅末 4 位，前缀点号；无 key 返回空串（绝不外泄明文，R-Sec）。

    **公开名**：`api/app_settings.py` 的情报检索源卡片也要用它。一个下划线开头的名字被两个模块
    共用，等于把它的签名变成公开契约却没有任何地方说明这件事（同 `intel/search.py::_redact`
    那条注释里立下的判据）。
    """
    key = (api_key or "").strip()
    return f"····{key[-4:]}" if key else ""


def provider_out(row: AgentProvider) -> dict:
    """AgentProvider 行 → 前端 dict（camelCase）。**绝不含 raw key**，仅 hasKey/keyPreview。"""
    has_key = bool((row.api_key or "").strip())
    return {
        "id": row.id, "slug": row.slug, "kind": row.kind, "label": row.label,
        "apiStandard": row.api_standard, "baseUrl": row.base_url, "model": row.model,
        "userAgent": row.user_agent, "hasKey": has_key, "keyPreview": key_preview(row.api_key),
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

    掩码复用 `key_preview`（P2-Naming：字段名取 `apiKeyPreview` 而非 `keyPreview`，多密钥实体下更清晰）；
    时间照 `provider_out` 输出秒级 UTC 整数（P2-Time，全系统时间口径一致）。
    """
    return {
        "id": row.id, "label": row.label,
        "apiKeyPreview": key_preview(row.api_key),
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
    maxKeptPerAxis: int | None = Field(default=None, ge=1, le=2000)
    autoResume: bool = True
    autoPromote: bool = False
    revalidateOnRefresh: bool = True
    seed: int | None = Field(default=None, ge=0)
    llm: dict[str, Any] = Field(default_factory=dict)
    thresholds: dict[str, Any] = Field(default_factory=dict)
    evolution: dict[str, Any] = Field(default_factory=dict)
    # 三条策略轴（discovery-strategy-axes §4.1）：全部可选，缺省即沿用后端默认。
    axisWeights: dict[str, Any] = Field(default_factory=dict)
    templates: list[str] = Field(default_factory=list, max_length=32)
    includeLibraryFactors: bool = True
    comboSize: dict[str, Any] = Field(default_factory=dict)
    rotationSize: dict[str, Any] = Field(default_factory=dict)
    strategyThresholds: dict[str, Any] = Field(default_factory=dict)


class DiscoveryBacktestReq(BaseModel):
    """候选一键回测入参（区间必填，与既有回测端点同口径）。"""

    startTs: int = Field(ge=0)
    endTs: int = Field(ge=0)

    @model_validator(mode="after")
    def _check_range(self):
        if self.endTs <= self.startTs:
            raise ValueError("endTs 必须大于 startTs")
        return self


# ============================ 交易经验库（ai-trade-experience-context-graph §五）============================
class ExperienceNodeIn(BaseModel):
    """手工新增一条经验（`source="manual"`，`tier` 恒 `candidate`）。

    `context` 的轴与取值走闭合词表（`taxonomy.is_valid`）；越界轴一律被丢弃，全部越界 ⇒ 422。
    这是不可信输入的第一道闸——一个模型或用户编出来的 `axis="心情"` 在图上永远匹配不到任何东西，
    却会静默占掉一条经验的全部权重（不变式 I-5）。
    """

    statement: str = Field(min_length=1, max_length=120)
    detail: str = Field(default="", max_length=600)
    context: dict[str, str] = Field(min_length=1)
    polarity: Literal["positive", "negative"] = "negative"


class ExperienceNodePatchIn(BaseModel):
    """编辑一条经验的可写子集。

    `tier` 只允许在 `candidate`/`normal`/`iron` 之间调整，且**升 `iron` 只有一条路径——置顶**
    （不变式 I-4）。`muted=true` 落 `mute_reason="user"`，那是 `mark_stale_lessons` 与
    `unmute_reappeared` 判断「这是人类意志、不许自动改写」的唯一依据（★ E-9）。
    """

    statement: str | None = Field(default=None, min_length=1, max_length=120)
    detail: str | None = Field(default=None, max_length=600)
    pinned: bool | None = None
    muted: bool | None = None
    tier: Literal["candidate", "normal", "iron"] | None = None


class ExperiencePreviewIn(BaseModel):
    """检索预演入参（全 11 轴均可手选；空值即该轴不参与种子）。"""

    instId: str = Field(min_length=1, max_length=32)
    bar: str = Field(min_length=1, max_length=8)
    regime: str | None = None
    volatility: str | None = None
    event: str | None = None
    side: str | None = None
    wake: str | None = None
    exit: str | None = None
    horizon: str | None = None
    confidence: str | None = None
    costRegime: str | None = None


class ExperienceRebuildIn(BaseModel):
    """手动重建的范围（ai-trade-experience-compounding §5.2：本轮 `scope` 真的开始有取值了）。

    - `stat`（默认）：由 W-B 的五条判据自选增量还是全量——「智能」那一档。
    - `full`：**强制**全量重建并 `epoch+1`（重写累加器底座）。它是增量算错时的兜底出口，
      也是「正确性预言机」：两条路径的产物必须逐值相等（门禁 G-10）。
    """

    scope: Literal["stat", "full"] = "stat"


class ExperiencePruneIn(BaseModel):
    """清理沉降经验（`POST /api/experience/prune`）。

    `dryRun` 默认 **True**：这是一个删除操作，默认值必须站在「先看一眼」那一侧。UI 也必须先跑
    一次 dryRun 把条数显示给用户再二次确认——**绝不静默批删**（A-34）。
    """

    scope: Literal["stale"] = "stale"
    dryRun: bool = True


class ExperienceImportIn(BaseModel):
    """导入 JSON（**不可信输入**，R-8 / ★ E-10）。

    只导入经验节点：边是物化派生物，事件带着别人的成本口径（I-7 一旦混用整张表偏移一个往返
    成本）。`nodes` 的条数上限由 `ai_trade_experience_import_max_nodes` 在服务端判定——越界回
    422 并给出实际条数与上限，**绝不静默截断**。
    """

    version: int = 1
    nodes: list[dict] = Field(default_factory=list)
    edges: list[dict] = Field(default_factory=list)
    events: list[dict] = Field(default_factory=list)
    mode: Literal["merge", "replace"] = "merge"


class ExperienceSettingsIn(BaseModel):
    """设置中心可写的阈值子集（**不含 `enabled`**）。

    评审 P2-4：`enabled` 是**运维总闸**，在本端点上只读。它可写就会与启动弹窗那个复选框互相
    矛盾——总闸关掉后弹窗仍显示勾选且可勾。总闸只经环境变量 / `.env` 调整，与本仓库其它
    「一键回滚」总闸一致。

    ★ E-7（评审 P1-1）：`l0MaxChars` / `l1MaxChars` 的**下限**由端点复用
    `disclosure.budget_floor(layer)` 判定，**不在这里抄一份数字**——`_check_experience_budgets`
    只在 `Settings` 构造时跑，`PUT` 会完全绕过它，于是用户能从界面上把 L0 配成一个「永远渲染
    不出任何东西」的值而毫无提示。
    """

    defaultOn: bool | None = None
    writeEnabled: bool | None = None
    minSamples: int | None = Field(default=None, ge=1, le=10000)
    ironSamples: int | None = Field(default=None, ge=1, le=10000)
    tThreshold: float | None = Field(default=None, gt=0, le=100)
    halfLifeDays: float | None = Field(default=None, gt=0, le=3650)
    staleDays: float | None = Field(default=None, gt=0, le=3650)
    l0Max: int | None = Field(default=None, ge=0, le=10)
    l0MaxChars: int | None = Field(default=None, ge=0, le=4000)
    l1Max: int | None = Field(default=None, ge=0, le=10)
    l1MaxChars: int | None = Field(default=None, ge=0, le=4000)
    distillEnabled: bool | None = None
    # ★ W-C（ai-trade-experience-compounding §6.3）· 效用闭环的三项可写阈值。
    # 域必须与 `config.py` 的 `Field(...)` 逐字一致：两处不同的那一半会变成「面板收得下、
    # 运行时夹紧到别的值」——用户看到的与系统用的不是一个数，而且不报错。
    utilityEnabled: bool | None = None
    utilityMinSamples: int | None = Field(default=None, ge=1, le=10000)
    utilityTThreshold: float | None = Field(default=None, gt=0, le=100)


class ExperienceSettingsOut(BaseModel):
    """设置面板回执（`enabled` 只读，见 `ExperienceSettingsIn` 的 P2-4）。"""

    enabled: bool
    defaultOn: bool
    writeEnabled: bool
    minSamples: int
    ironSamples: int
    tThreshold: float
    halfLifeDays: float
    staleDays: float
    l0Max: int
    l0MaxChars: int
    l1Max: int
    l1MaxChars: int
    distillEnabled: bool
    utilityEnabled: bool
    utilityMinSamples: int
    utilityTThreshold: float
    l0MinChars: int          # 下限判据如实下发，使 UI 能在提交前就说清楚为什么不合法（I-8）
    l1MinChars: int


# ============================ 标的市场情报（ai-trade-market-intel §4.1）============================
# 本节**只定义入参**（`/intel/*` 一律不挂 `response_model`：它们回的是带嵌套结构的领域对象，
# 未在模型里声明的 wire 键会被静默丢弃，本仓库为此栽过），外加一个 `IntelSettingsOut`——
# `/settings/intel*` 落在 `api/app_settings.py`，而该文件既有的两组设置端点**都挂了**
# `response_model`；在一个百来行的文件里造出两种口径比统一到任一种都差（评审 P2-4）。
class IntelSourceIn(BaseModel):
    """用户手工新增情报时填的一条来源。"""

    url: str = Field(max_length=512)
    title: str = Field(default="", max_length=120)
    publishedTs: int | None = None

    @field_validator("url")
    @classmethod
    def _http_only(cls, value: str) -> str:
        """仅 http(s)。`javascript:` / `file:` / `mailto:` 一律拒——这条链路的终点是一个用户会
        点击的外链，而桌面端的外链走系统浏览器。"""
        raw = (value or "").strip()
        if not (raw.startswith("http://") or raw.startswith("https://")):
            raise ValueError("来源链接必须是 http(s) 绝对地址")
        return raw


class IntelItemIn(BaseModel):
    """手工新增一条情报（`origin="user"`）。"""

    tier: Literal["ultra", "mid", "long"] = "ultra"
    category: Literal["market", "sentiment", "macro", "onchain", "project", "risk"]
    headline: str = Field(min_length=1, max_length=40)
    summary: str = Field(min_length=1, max_length=400)
    stance: Literal["bullish", "bearish", "neutral"] = "neutral"
    impact: Literal["low", "medium", "high"] = "medium"
    eventTs: int | None = None
    sources: list[IntelSourceIn] = Field(default_factory=list, max_length=6)
    pinned: bool = False


class IntelItemPatchIn(BaseModel):
    """编辑 / 移层 / 置顶 / 静音 / 清判定（全部可选；一个非 None 都没有 ⇒ 422）。"""

    tier: Literal["ultra", "mid", "long"] | None = None
    category: Literal["market", "sentiment", "macro", "onchain", "project", "risk"] | None = None
    headline: str | None = Field(default=None, min_length=1, max_length=40)
    summary: str | None = Field(default=None, min_length=1, max_length=400)
    stance: Literal["bullish", "bearish", "neutral"] | None = None
    impact: Literal["low", "medium", "high"] | None = None
    pinned: bool | None = None
    status: Literal["active", "archived", "muted"] | None = None
    # 「我不同意」（closed-loop §4.1 #22）：清 `verdict` / `verdictTs` / `verdictNote`、置
    # `userEdited`，**`verifyCount` 保持不变**。清了它这条目下一轮就会被自动重判回去，撤销出口
    # 会变成一扇旋转门（评审 P0-4）。
    clearVerdict: Literal[True] | None = None

    @model_validator(mode="after")
    def _at_least_one(self) -> "IntelItemPatchIn":
        """空 patch 一律 422：静默地「保存成功但什么都没改」是本仓库明令禁止的形状。"""
        if not self.model_dump(exclude_none=True):
            raise ValueError("至少要给出一个待修改字段")
        return self


class IntelMoveIn(BaseModel):
    """移层（`POST /intel/items/{id}/move`）。"""

    tier: Literal["ultra", "mid", "long"]


class IntelSeenIn(BaseModel):
    """标记已读（缺省取服务端 now；未读徽标的唯一真值写入口，评审 P1-2）。"""

    ts: int | None = None


class IntelSettingsIn(BaseModel):
    """设置中心「情报检索源」卡片的可写字段。

    `apiKey` 的三态语义：``""``（或缺席）= 保持不变；``"__clear__"`` = 清除；其余 = 写入。
    没有这三态，用户就无法在「不重新输入密钥」的前提下改别的字段。
    """

    enabled: bool | None = None
    injectEnabled: bool | None = None
    provider: Literal["none", "tavily", "brave", "serper"] | None = None
    apiKey: str | None = None
    collectIntervalMin: float | None = Field(default=None, ge=5, le=1440)
    curateEveryNCollects: int | None = Field(default=None, ge=1, le=24)
    dailyCap: int | None = Field(default=None, ge=1, le=1000)
    # ---- ai-trade-intel-closed-loop §4.1 #23：三把安全单键 + 五个核查旋钮 ----
    # ⚠️ 这 8 个键的**唯一**落点是 `intel/service.py::INTEL_SETTING_BRIDGE`。只在这里声明、
    # 不扩那张表 ⇒ 请求 200、面板上开关照动、`Settings` 上的值永远不变（评审 P0-5 的假面板）。
    citeEnabled: bool | None = None
    attributionEnabled: bool | None = None
    verifyEnabled: bool | None = None
    verifyEveryNCurates: int | None = Field(default=None, ge=1, le=12)
    verifyMaxItems: int | None = Field(default=None, ge=1, le=10)
    verifyDocsPerItem: int | None = Field(default=None, ge=1, le=12)
    blockedSweepEnabled: bool | None = None
    citeMaxRefs: int | None = Field(default=None, ge=1, le=16)
    # ---- ai-trade-intel-evidence-grounding §4.1 M-3：轴 A / 轴 B 的三把单键 ----
    # ⚠️ 值域**必须**收在这里而不是只在前端：设置端点是公开的，而 `quoteMode` 的一个坏值会经
    # `settings.model_copy(update=…)` 绕过 `Literal`（`model_copy` 不做校验）。第二道保险在
    # `intel/service.py::resolve_intel_settings` 的读回处。
    readerEnabled: bool | None = None
    readerTopK: int | None = Field(default=None, ge=1, le=12)
    quoteMode: Literal["off", "soft", "strict"] | None = None


class IntelProbeIn(BaseModel):
    """测试连接（不传即用已存配置）。"""

    provider: Literal["none", "tavily", "brave", "serper"] | None = None
    apiKey: str | None = None


class IntelCostEstimateOut(BaseModel):
    roundsPerDay: int
    inputTokensPerDay: int


class IntelSettingsOut(BaseModel):
    """`GET/PUT /settings/intel` 的回执。**绝不含 raw key**，仅 hasKey/keyPreview（§3.2.3）。"""

    enabled: bool
    injectEnabled: bool
    provider: str
    hasKey: bool
    keyPreview: str
    collectIntervalMin: float
    curateEveryNCollects: int
    dailyCap: int
    estimate: IntelCostEstimateOut
    # ---- closed-loop：与 `IntelSettingsIn` 的 8 个新键逐个对称。**回执必须带上它们**——
    # `response_model` 会静默丢弃未声明的键，症状是「开关点了、回读还是旧值」（v1 终审教训）。
    citeEnabled: bool
    attributionEnabled: bool
    verifyEnabled: bool
    verifyEveryNCurates: int
    verifyMaxItems: int
    verifyDocsPerItem: int
    blockedSweepEnabled: bool
    citeMaxRefs: int
    # ---- evidence-grounding：与 `IntelSettingsIn` 的三个新键逐个对称。**回执必须带上它们**
    # ——`response_model` 会静默丢弃未声明的键，症状是「开关点了、回读还是旧值」。
    readerEnabled: bool
    readerTopK: int
    quoteMode: str
    # 近 20 轮引文命中率（`None` = 分母 0，「没提过引文」与「提了全没中」是两句不同的话）。
    # 它是把 `quoteMode` 调到 strict 的**唯一**判据在设置卡上的落点。
    quoteHitRate: float | None = None


class ErrorBody(BaseModel):
    code: str
    message: str


class ErrorResponse(BaseModel):
    error: ErrorBody
