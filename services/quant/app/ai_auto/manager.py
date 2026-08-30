"""AiTradeManager —— AI 自动交易「单活跃分组」注册表（ai-trade-multi-symbol-and-timeframe §4.1）。

职责：两段式启动前置门禁（安全关键，绝不假启动）、单活跃分组独占（一次至多一个 group，含 1..N 个
标的会话）、与 live-auto 的**原子互斥**（经共享 `AUTO_TRADE_ADMISSION_LOCK`，P1-4）、lifecycle
（start_group/stop_group/stop_member/group_status/get/shutdown）。真实下单一律经 `LiveExecutor` →
`okx/trading.py` 服务层，本层只做编排与守门。

多标的范式镜像已上线、已验证的 live-auto「分组」：一个分组内每个标的各自是一个**保持不变的**
`AiTradeSession`（各有独立 decider / executor / MarketWindow / WS / 轨迹），总预算按成员 `allocation_pct`
**切分**（非按标的倍增），故**总账户敞口仍受分组总预算天花板约束**——该天花板由账户级 `maxBudgetPct` 与运营
天花板 `live_auto_group_max_capital_frac` 共同主导（与非 AI 分组口径统一，ai-trade-total-budget-allocation
有意放开历史 5% 绝对红线）。`AiTradeSession` 及其下游 decider / consensus / trace / backtest **零改动**。

与 live-auto 的互斥（安全关键）：二者共用同一实盘账户预算，绝不同时下单。AI 侧 `_require_capacity`
在共享准入锁内「检查无活跃 AI 分组 且 无活跃 live-auto 分组 + 注册自己」；live-auto 侧
`LiveAutoManager._require_capacity` 反向读 `get_ai_trade_manager().status()`（= `group_status()`）——两侧
临界区共用同一把锁，check-and-register 原子化，杜绝并发 start 的 TOCTOU 双双通过（live_auto 侧零改动）。
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from functools import partial

from ..agent.llm.factory import make_llm_client_from_config
from ..agent.providers import resolve_active_provider
from ..config import Settings
from ..db import ai_trade_state_repositories as state_repo
from ..db import repositories as repo
from ..db.session import get_sessionmaker
from ..engine.simulator import RiskParams
from ..live_auto.executor import LiveExecutor
from ..live_auto.fee_ledger import build_fee_ledger
from ..okx.accounts import AccountCredentials
from ..okx.order_builder import snap_size
from ..okx.trading import (fetch_live_open_orders, fetch_live_positions, fetch_trade_fee,
                          fetch_trading_config)
from ..okx.types import BAR_SECONDS
from ..paper.strategy_factory import PaperStrategySpec
from .admission import get_admission_lock
from .base_rules import MaBandRule, format_charter, rule_wire
from .cost_runtime import build_charter_text
from .discipline import PRESET_LABELS
from .exposure_book import GroupExposureBook
from .hold_horizon import HorizonRule
from .hold_horizon import rule_wire as horizon_wire
from .horizon_acceptance import input_from_wire, merge_bundle
from .hold_horizon_text import format_charter as format_horizon_charter
from . import lifecycle as lifecycle_core
from .market_window import MarketWindow, WindowOptions
from . import pause as pause_core
from .profit_guard import ProfitGuardRule
from .profit_guard_text import format_profit_guard_charter
from .profit_guard import rule_wire as profit_guard_wire
from .decider import AiTradeDecider
from .experience import ExperienceRule, ExperienceRuntime, SessionGraph
from .experience import scheduler
from .experience.events import EventIdentity
from .intel import runtime as intel_runtime
from .intel import scheduler as intel_scheduler
from .llm_budget import build_ai_trade_llm_clients
from .prompts import build_ai_trade_system_prompt, format_direction_charter
from .prompts_en import SystemPromptOptions, SystemPromptTexts
from .reasoning_protocol import build_deliberation_protocol, resolve_depth_profile
from .session import AiTradeSession
from .signals import consensus_rule_from_settings, contrarian_schema
from .sizing import SizingRule, StopRule, check_window_capacity, parse_sizing_rule, parse_stop_rule
from .sizing_runtime import build_sizing_runtime, sizing_config_wire
from .sizing_text import format_sizing_charter
from .tradability_probe import probe_members
from .wake_runtime import format_tactical_bars_text, tactical_bars_for

logger = logging.getLogger(__name__)


class AiTradePreflightError(Exception):
    """启动前置门禁不通过（携稳定语义码与 HTTP 状态，API 层据此回结构化错误）。"""

    def __init__(self, code: str, message: str, status: int = 400):
        self.code = code
        self.message = message
        self.status = status
        super().__init__(message)


class AiTradeSessionLimitError(AiTradePreflightError):
    """已有活跃 AI 分组或活跃 live-auto 分组（单活跃独占 + 与 live-auto 互斥）。"""


class AiTradeAllocationError(AiTradePreflightError):
    """分组百分比非法（空成员 / 越界 / even 模式和超 100）。"""


class AiTradeGroupTooLargeError(AiTradePreflightError):
    """分组标的数超 ai_trade_max_symbols 硬上限。"""


class AiTradeGroupPreflightError(AiTradePreflightError):
    """整组前置门禁失败（携逐标的 `.members` 明细，分组路由须透传）。"""

    def __init__(self, members: list[dict],
                 message: str = "部分标的无法启动 AI 自动交易", status: int = 409):
        self.members = members
        super().__init__("AI_TRADE_GROUP_PREFLIGHT", message, status)


class AiTradeGroupEnded(AiTradePreflightError):
    """续跑时发现整组会话都已终止（lifecycle-truth §3.6 第一格）。

    L-5 的极性：**拒绝比谎言好**。今天这条路会安静地把分组置 `running` 并回 200，用户得到的是
    「恢复成功但 Agent 不交易」——正是本轮要消灭的那句话。
    """

    def __init__(self, message: str, members: list[dict] | None = None):
        self.members = members or []
        super().__init__("AI_TRADE_GROUP_ENDED", message, 409)


class AiTradeResumePartiallyDead(AiTradePreflightError):
    """部分成员在暂停期自终止（`.members` 逐标的说明谁还活着 / 谁已结束）。

    **不做半组续跑**：预算切分、`GroupExposureBook.unit_notional` 与验收卡口径都是分组级的，
    半组续跑会让这三样同时失真（与 `resume.py` 模块 docstring 里 `[P2-13]` 同一条理由）。
    """

    def __init__(self, members: list[dict],
                 message: str = ("该分组有成员的会话已经结束，无法整组继续。"
                                 "可以「结束并归档」这个分组，或结束后重新启动一个新分组"
                                 "（如交易所上仍有持仓，请先到实盘面板核对）")):
        self.members = members
        super().__init__("AI_TRADE_RESUME_PARTIALLY_DEAD", message, 409)


class AiTradeResumeNotArmed(AiTradePreflightError):
    """续跑后的武装复检未通过，已整组回滚到 `paused`（L-5 / [评审 R0-5]）。

    这一格同时是「点继续 → 几秒后被强平」那条路的**唯一**拦截点：复检判到 `feed_stale` 就
    当场不武装、回滚回暂停态，绝不让一次「继续」变成 `_check_staleness` 的强平触发器。
    """

    def __init__(self, members: list[dict], message: str):
        self.members = members
        super().__init__("AI_TRADE_RESUME_NOT_ARMED", message, 409)


def _creds(account_row: repo.OkxAccount) -> AccountCredentials:
    return AccountCredentials(api_key=account_row.api_key, secret_key=account_row.secret_key,
                             passphrase=account_row.passphrase)


def _clamp(value: float, low: float, high: float) -> float:
    """把 `value` 夹到 `[low, high]`（E4 单仓 pct 越界夹紧）。"""
    return low if value < low else high if value > high else value


@dataclass
class AiTradeGroupMemberInput:
    """启动请求中的单成员：标的 + 可用仓位百分比（(0,100]，even 模式整组 Σ ≤ 100）。"""

    inst_id: str
    allocation_pct: float


@dataclass
class AiTradeGroupStartRequest:
    """`start_group` 聚合形参（避免位置堆参，满足 CLAUDE.md ≤5 形参；镜像 live-auto `GroupStartRequest`）。

    `budget_cap_frac` / `default_budget_frac` 由 `api/ai_auto.py` 预解析后填入：
    `budget_cap_frac = min(maxBudgetPct/100, ai_trade_max_capital_frac(默认 1.0=不约束),
    live_auto_group_max_capital_frac)`（= 前端 `effectiveMaxBudgetPct/100`，maxBudgetPct 与运营天花板共同主导）、
    `default_budget_frac = ai_trade_default_capital_frac=1%`（留空默认，保守）。
    """

    specs: list[PaperStrategySpec]
    members_in: list[AiTradeGroupMemberInput]
    bar: str
    account_row: repo.OkxAccount
    settings: Settings
    context_refresh_rounds: int
    allocation_mode: str = "even"            # even | max_ratio
    total_capital_usd: float | None = None   # 分组总预算（按成员 pct 切分）
    leverage: float | None = None
    take_profit_usd: float | None = None     # 分组总止盈；按 pct/Σpct 归一分摊到各成员
    stop_loss_usd: float | None = None       # 分组总止损；同上
    stop_loss_pct: float | None = None       # E4：单仓交易所侧 OCO 止损%（各成员同值；越界服务端夹紧）
    take_profit_pct: float | None = None     # E4：单仓 OCO 止盈%（各成员同值）
    priming: bool = True
    priming_bars: int = 300
    budget_cap_frac: float = 1.0             # 预算硬上限分数（avail×该值），恒由 api 层显式覆盖（默认 1.0=不约束）
    default_budget_frac: float = 0.01        # 留空默认分数（avail×该值），由 api 解析（默认 1%）
    position_step_pct: float = 0.0           # 仓位操作档位步长（fraction；0=连续，直接构造该 dataclass 的既有测试零回归）
    wake_interval_s: float = 0.0             # 墙钟定时唤醒周期（秒；0=关闭，直接构造该 dataclass 的既有测试零回归）
    # 研判深度档（deep-reasoning-chain §4.4 / P1-1）：`None` ⇒ 用 settings 默认档（直接构造该
    # dataclass 的既有测试零回归）。**深度档必须落在请求对象上**——只读 settings 会让启动请求里
    # 的 `reasoningDepth` 被接收、校验、然后丢弃，且暂停→续跑会静默降档而不报错。
    reasoning_depth: str | None = None
    # 【基础策略】MA 通道开单铁律：默认 **None=不装配**（直接构造该 dataclass 的既有测试零回归，P1-5）。
    # 「缺省即开启」的语义**只在 API 层解析**（`_resolve_base_rules`），绝不在此给开启默认值。
    base_rule: MaBandRule | None = None
    # 利润保护层：同款零回归垫片（默认 None=不装配；「缺省即开启」只在 API 层 `_resolve_profit_guard`
    # 解析）。回测侧的同名规则走 `AiBacktestStartRequest.profit_rule`（同一解析点、另加回测总闸），
    # 见 ai-trade-profit-protection-adaptive §2.3 / §7.2a I-3。
    profit_rule: ProfitGuardRule | None = None
    # 信号先验快照，**按 `instId` 键**（signal-prior-and-ev-gate §4.3 / P1-5）。刻意不镜像
    # `base_rule` 的「分组共享单对象」姿态：铁律是同一条规则，先验快照却是 `(instId, bar)` 粒度的
    # 历史统计——照字面共享会让 BTC 的成员拿 ETH 的统计去开真实仓位，且**不会报错**。
    # 缺键 = 该成员无先验（不是错误，逐字回退 ATR 粗估）。
    prior_snapshots: dict[str, dict] | None = None
    # 实测战绩快照，**按 `instId` 键**（realized-edge-calibration 轴 D）。与 `prior_snapshots` 同款
    # 姿态：BTC 15m 的胜率与 PEPE 1m 的胜率不是同一个东西，分组共享一份等于用别的标的的历史去开
    # 真实仓位且**不会报错**。缺键 = 该成员无战绩（不是错误，payload 整键缺席、prompt 逐字回退）。
    track_records: dict[str, dict] | None = None
    # 交易周期模式契约（ai-trade-holding-horizon-modes §2.4）：同款零回归垫片（默认 None=不装配；
    # 「缺省即开启」只在 API 层 `_resolve_horizon` 解析）。**分组共享同一份契约**——模式是会话级契约
    # 而非标的属性（R-12），与 base_rule / profit_rule 的分组共享姿态一致。
    horizon_rule: HorizonRule | None = None
    # 交易纪律预设的**回执载体**（discipline-preset-and-integrity 轴 A）。阈值本身不在这里——它们
    # 已经在 `settings` 那份**会话私有派生副本**里了，这四个字段只为「让用户能核对服务端到底装了
    # 什么」。默认值 = 未装配（直接构造该 dataclass 的既有测试逐字节零回归）。
    discipline_preset: str = "off"
    discipline_f_floor: float = 1.0          # [P0-4] 最小下单量复核用的保守仓位下界（1.0=不复核）
    discipline_adjustments: list[dict] = field(default_factory=list)
    discipline_thresholds: dict = field(default_factory=dict)
    # 启动请求**原文**（ai-trade-pause-resume D-2）：续跑的唯一真源。`config_json` 缺 accountId /
    # stopLossPct / takeProfitPct / primingBars，照它续跑会静默把用户设的止损换成服务端默认。
    # 只落请求体（`AiTradeGroupStartIn`），**绝不**含 apiKey/secret/passphrase（[P2-11]）。
    start_wire: dict = field(default_factory=dict)
    # 冷续跑时携带的既有身份：`{"groupId":…, "createdTs":…, "runIds":{instId: runId}}`。
    # 非空 ⇒ 复用同一 group_id 与同一批 run（不变式 I-3：续跑绝不新建 run）。
    resume_of: dict | None = None
    # 交易经验上下文图（experience-context-graph W-2）：规则与启动期快照都在 API 层解析 / 取数
    # （`build_start_request`）。**两者都带默认 None** ⇒ 直接构造该 dataclass 的既有测试逐字节
    # 零回归；未装配 ⇒ 会话侧空对象 ⇒ payload 无 `experience` 键（不变式 I-2）。
    experience_rule: ExperienceRule | None = None
    experience_snapshot: SessionGraph | None = None


@dataclass
class AiTradeGroupMember:
    """活跃分组内的单成员（会话 + 落地预算 / 百分比 / Run）。"""

    inst_id: str
    allocation_pct: float
    budget_usd: float
    session: AiTradeSession
    run_id: int


@dataclass
class AiTradeGroup:
    """进程内当前活跃 AI 分组（一次至多一个）。"""

    group_id: str
    bar: str
    strategy_ids: list[int]
    strategy_names: list[str]
    total_budget_usd: float
    leverage: float
    context_refresh_rounds: int
    created_ts: int
    members: list[AiTradeGroupMember]
    wake_interval_s: float = 0.0             # 墙钟定时唤醒周期（秒；0=关闭），group_status 回显换算为分钟
    base_rule: MaBandRule | None = None      # 【基础策略】铁律（None=未装配），group_status 回显 baseRules
    profit_rule: ProfitGuardRule | None = None   # 利润保护层（None=未装配），回显 profitGuard
    sizing_rule: SizingRule | None = None    # 仓位求解规则（总闸①；None=未装配）
    stop_rule: StopRule | None = None        # ATR 止损规则（总闸②；与①独立）
    exposure_book: GroupExposureBook | None = None   # 分组共享敞口簿（总闸③关 ⇒ None）
    horizon_rule: HorizonRule | None = None  # 交易周期模式（None=未装配），回显 tradingHorizon
    discipline_preset: str = "off"           # 交易纪律预设 id（"off"=整轴未装配），回显 discipline
    discipline_adjustments: list[dict] = field(default_factory=list)
    discipline_thresholds: dict = field(default_factory=dict)
    # 轴 A 启动断面（tradability-and-open-funnel §3.1.3）：**逐成员一份**，与各成员会话 status 里
    # 的那一份是**同一个对象**（同一 `asOfTs`）。R-10：分组级刻意不做任何合并——一个「平均否决率」
    # 既没有操作含义，AI-trade 也没有分组级实时通道可以可信地下发它。
    tradability: list[dict] = field(default_factory=list)
    # 分组生命周期（ai-trade-pause-resume §6.5）：running|paused。给默认值以保证既有直接构造它的
    # 单测逐字节零回归。**注意与成员会话的 `state` 分工**：这是分组级的用户可见态。
    state: str = "running"
    pause_reason: str | None = None
    paused_ts: int = 0
    resumed_count: int = 0
    account_id: int = 0                      # 续跑要用哪个 OKX 账户的凭证（config_json 缺这个数）
    start_wire: dict = field(default_factory=dict)      # 启动请求原文（续跑唯一真源）


@dataclass
class PausedGroupRef:
    """冷启后的**轻量**分组占位（ai-trade-pause-resume §3.3）。

    只承载「存在一个可续跑分组」这个事实 + 展示所需的最少字段，刻意**不含** `AiTradeSession`
    —— 建会话要暖机、要 WS、要 LLM 客户端，那是 `resume` 的工作，不是「知道自己有个暂停会话」
    的代价。它存在的第一理由是不变式 I-2：让 `status()` 非空，使 `live_auto._require_capacity`
    **零改动**地恢复互斥（D-9）。
    """

    group_id: str
    bar: str = ""
    account_id: int = 0
    total_budget_usd: float = 0.0
    leverage: float = 0.0
    strategy_names: list[str] = field(default_factory=list)
    paused_ts: int = 0
    pause_reason: str | None = None
    resumed_count: int = 0
    created_ts: int = 0
    members: list[dict] = field(default_factory=list)
    start_wire: dict = field(default_factory=dict)
    resolved_echo: dict = field(default_factory=dict)


@dataclass
class _MemberPlan:
    """通过第一阶段门禁的单成员落地参数（待第二阶段建会话）。"""

    inst_id: str
    allocation_pct: float
    budget_usd: float
    config: dict
    # 轴 A 启动期预取的官方费率快照（P1-7）：`_create_group_run` 是 @staticmethod，既拿不到
    # config_map 也拿不到执行器，故这个值只能随成员计划下传。取数失败 ⇒ None ⇒ 回落配置常数。
    fee_rates_wire: dict | None = None


@dataclass
class _GroupBuildCtx:
    """第二阶段建会话的共享上下文（避免 `_build_member` 形参堆叠）。"""

    req: AiTradeGroupStartRequest
    creds: AccountCredentials
    provider: object
    lever: float
    sum_pct: float
    total_budget_usd: float
    group_id: str
    created_ts: int
    book: GroupExposureBook | None = None     # 分组共享敞口簿（总闸③关 ⇒ None ⇒ corrScale ≡ 1.0）


class AiTradeManager:
    """进程内单活跃 AI 分组注册表（真实资金，一次至多一个 group，含 1..N 个标的会话）。"""

    def __init__(self):
        self._group: AiTradeGroup | None = None
        # start 关键区互斥锁，消除「容量检查 → 置位 self._group」之间的 TOCTOU（单活跃分组）。
        self._start_lock = asyncio.Lock()
        # 冷启后的轻量占位（pause-resume §3.3）。`_group` 与 `_paused_ref` **永不同时非空**（G-9）。
        self._paused_ref: PausedGroupRef | None = None
        # [P0-3] 水合盲区标志：**「读不到」与「没有」在真实资金上不是同一件事**。置真时
        # `status()` 仍回一个非空占位（live_auto 零改动地保持互斥）；任何一次 `_require_capacity`
        # 的 DB 读成功即把它清掉，故盲区不会长期存在。
        self._hydration_failed = False
        # 「意图态 != 派生态」的告警去重（风险 R-9）：只在迁移瞬间记一次。
        self._last_logged_mismatch: tuple[str, str, str] | None = None

    # ---------- 分组启动（两段式：全量门禁 → 建会话 → 起会话 / 回滚）----------
    async def start_group(self, req: AiTradeGroupStartRequest) -> AiTradeGroup:
        """§4.1：双闸 → Agent 已配置 → 策略/轮数校验 → 原子互斥准入 → 两段式门禁 → 整组回滚启动。

        P1-4：「检查对方空闲 + 注册自己」临界区包在与 live-auto 共享的 `AUTO_TRADE_ADMISSION_LOCK`
        内（外层），再叠 `_start_lock`（内层），使 AI/live-auto 两侧准入串行、check-and-register 原子。
        """
        self._require_enabled(req.settings)
        provider = await resolve_active_provider(get_sessionmaker(), req.settings)
        if provider is None:
            raise AiTradePreflightError(
                "AGENT_NOT_CONFIGURED", "未配置 AI 模型，无法决策（请在设置中心配置默认模型）", 400)
        self._validate_strategies(req)
        self._validate_context_rounds(req)
        self._validate_sizing(req)      # 仓位层的两条启动期对账（越界 422，绝不留到运行期静默空转）
        async with get_admission_lock(), self._start_lock:
            await self._require_capacity(req.settings)
            members = self._validate_members(req.members_in, req.settings, req.allocation_mode)
            creds = _creds(req.account_row)
            avail, positions, configs, pending = await self._gather_context(creds, req, members)
            total = self._resolve_budget_group(
                req.total_capital_usd, avail, req.budget_cap_frac, req.default_budget_frac)
            lever = req.leverage or req.settings.live_default_leverage
            plans = self._preflight_group(members, configs, positions, total, lever, pending)
            self._note_dust_risk(req, plans, lever)   # [P0-4]：如实回执，绝不 422
            group = await self._build_group(req, creds, provider, plans, total, lever)
            await self._probe_tradability(group, req)   # 轴 A：启动断面（只提示、绝不阻断启动）
            self._group = group
            launched = await self._launch_group_or_rollback(group)
            # W1（pause-resume §3.4.2 / [P1-5]）：契约在**启动成功之后**立刻落盘，此后任何时刻断电
            # 都可续跑。刻意不在 `_launch_group_or_rollback` **之前**写——启动失败回滚会把全部成员
            # `stop("start_failed")` 并清组，磁盘上却留下一条 `status='running'` 的幽灵契约，而自愈
            # 只覆盖 `paused` 行。挪到成功之后**零信息损失**：`session.start()` 只做暖机 + 建 WS +
            # 起 monitor，**不下任何单**，启动失败时交易所上不可能有本会话开出的仓位。
            await self._persist_group_state(launched, status=pause_core.STATUS_RUNNING)
            # 市场情报层（market-intel §3.5.1）：装配排在启动成功之后——它是一条**加法**流水线，
            # 绝不允许它的任何失败影响真实资金会话的启动（判定与 fail-open 都在 `_wire_intel`）。
            await self._wire_intel(launched, provider=provider)
            return launched

    @staticmethod
    async def _probe_tradability(group: AiTradeGroup, req: AiTradeGroupStartRequest) -> None:
        """按 `instId` 逐成员各算一份可交易性预检，装进各自会话（R-10：**分组级不做任何合并**）。

        启动回执与会话 `status` 用的是**同一份对象**（同一 `asOfTs`，[P1-10]）——弹窗那份是独立的
        事前预览、允许不同。**全程 fail-open**：任何异常只 warning，绝不让一个旁路统计量挡住真实
        资金会话的启动（探针把用户挡在门外比给错建议更坏）。
        """
        try:
            sm = get_sessionmaker()
            async with sm() as db:
                rows = await probe_members(
                    db, req.settings, inst_ids=[m.inst_id for m in group.members], bar=req.bar)
            by_inst = {row.get("instId"): row for row in rows}
            group.tradability = [by_inst[m.inst_id] for m in group.members
                                 if m.inst_id in by_inst]
            for member in group.members:
                member.session.note_tradability(by_inst.get(member.inst_id))
        except Exception as exc:  # noqa: BLE001 —— 预检是旁路统计，失败只降级
            # 装配也在 try 内是刻意的：极性纪律是**整轴 fail-open**，一个只提示、不阻断的旁路
            # 统计量绝不该成为「真实资金会话能不能起来」的函数。失败即 `tradability` 缺席 ⇒
            # 前端整段不渲染 ⇒ 逐字回退到本轮之前的行为。
            logger.info("ai-trade tradability probe skipped: %s", exc)

    def _validate_members(self, members_in: list[AiTradeGroupMemberInput],
                          settings: Settings,
                          mode: str = "even") -> list[AiTradeGroupMemberInput]:
        """容量 / 百分比 / 去重边界校验（边界一次性校验，进入内部即信任）。

        `mode == "even"` 强制 Σ allocationPct ≤ 100；`mode == "max_ratio"` 跳过 Σ 校验
        （各标的对总预算的独立上限，可超 100%）。其余闸（非空 / 去重 / 超标的数上限 /
        单成员 ∈ (0,100]）两模式一致。
        """
        if not members_in:
            raise AiTradeAllocationError("AI_TRADE_GROUP_EMPTY", "AI 自动交易分组为空", 400)
        seen: set[str] = set()
        unique = [m for m in members_in if not (m.inst_id in seen or seen.add(m.inst_id))]
        if len(unique) > settings.ai_trade_max_symbols:
            raise AiTradeGroupTooLargeError(
                "AI_TRADE_GROUP_TOO_LARGE",
                f"分组标的数超上限（≤ {settings.ai_trade_max_symbols}）", 400)
        if any(not (0 < m.allocation_pct <= 100) for m in unique):
            raise AiTradeAllocationError(
                "AI_TRADE_ALLOCATION_INVALID", "每个标的可用仓位百分比须在 (0, 100] 之间", 400)
        if mode == "even" and sum(m.allocation_pct for m in unique) > 100 + 1e-9:
            raise AiTradeAllocationError(
                "AI_TRADE_ALLOCATION_INVALID", "各标的可用仓位百分比之和不可超过 100%", 400)
        return unique

    async def _gather_context(self, creds: AccountCredentials, req: AiTradeGroupStartRequest,
                              members: list[AiTradeGroupMemberInput]) -> tuple:
        """账户可用 USDT 与全账户持仓**整组各取一次**；逐成员仅取该标的 trading_config。

        启动瞬间的签名私有请求按 `live_auto_start_concurrency`（默认 2）限流排队，避免一次性把
        N×2 发 account/config + account/balance 打爆 OKX 私有限频触发 429（复用同一并发预算）。
        """
        sem = asyncio.Semaphore(max(1, req.settings.live_auto_start_concurrency))

        async def _bounded_config(inst_id: str) -> dict:
            async with sem:
                return await fetch_trading_config(
                    creds, req.account_row.id, inst_id, settings=req.settings)

        async def _bounded_fee_rate(inst_id: str) -> dict | None:
            """轴 A：官方费率档位（签名私有 GET，并入同一并发预算）。

            **失败一律 None ⇒ 回落配置常数，绝不阻断启动**——可用性不得成为开仓能力的函数。
            """
            if not req.settings.ai_trade_fee_rate_source_enabled:
                return None
            async with sem:
                try:
                    return await fetch_trade_fee(creds, inst_id)
                except Exception as exc:  # noqa: BLE001 —— 费率取数失败只降级，不拒绝启动
                    logger.info("ai-trade trade-fee prefetch failed (%s): %s", inst_id, exc)
                    return None

        configs = await asyncio.gather(*[_bounded_config(m.inst_id) for m in members])
        fee_rates = await asyncio.gather(*[_bounded_fee_rate(m.inst_id) for m in members])
        async with sem:
            positions = await fetch_live_positions(creds)
        async with sem:                       # 复用同一并发预算（规避已知的启动 429）
            pending = await self._fetch_blocking_pending(creds)
        config_map = {m.inst_id: {**cfg, "feeRates": fr}
                      for m, cfg, fr in zip(members, configs, fee_rates)}
        avail = next((cfg.get("availUsdt") or 0.0 for cfg in configs), 0.0)
        return avail, positions, config_map, pending

    @staticmethod
    async def _fetch_blocking_pending(creds: AccountCredentials) -> list[dict]:
        """全账户未结**普通**挂单（ai-trade-limit-orders §2.9）。

        **必须按 `kind` 过滤，不能用「非空」判定**（P1-4/R-14）：`fetch_live_open_orders` 并非只返回
        普通挂单——它同时并发查 `orders-algo-pending` 的 `oco`/`conditional` 并把每条 OCO **展开成
        `tp`/`sl` 两条**归一记录一并返回。而随单 OCO 正是**本项目自己**在每次开仓时附的，只要该标的
        上有任何带止盈止损的持仓、或有一条残留未清的 OCO，非空判定就会把带随单止盈止损的正常账户
        用一个自己也看不懂的 409 挡在门外。`tp`/`sl` 算法委托与本特性的「普通限价挂单」是两类不同
        对象，本就不该参与该判定。`kind` 的分类真源是 `_classify_pending_kind`，此处直接复用。
        """
        orders = await fetch_live_open_orders(creds)
        return [o for o in orders if o.get("kind") in ("limit_open", "limit_close")]

    def _preflight_group(self, members: list[AiTradeGroupMemberInput], configs: dict,
                         positions: list[dict], total: float, lever: float,
                         pending: list[dict] | None = None) -> list[_MemberPlan]:
        """第一阶段·全量前置门禁（**不下任何单**）：任一成员失败即整组拒绝并携明细。"""
        failures: list[dict] = []
        plans: list[_MemberPlan] = []
        for m in members:
            cfg = configs[m.inst_id]
            budget_i = total * m.allocation_pct / 100.0
            fail = self._member_preflight_fail(m.inst_id, cfg, positions, budget_i, lever,
                                               pending or [])
            if fail is not None:
                failures.append(fail)
            else:
                plans.append(_MemberPlan(m.inst_id, m.allocation_pct, budget_i, cfg,
                                         fee_rates_wire=cfg.get("feeRates")))
        if failures:
            raise AiTradeGroupPreflightError(failures)
        return plans

    def _member_preflight_fail(self, inst_id: str, cfg: dict, positions: list[dict],
                               budget_i: float, lever: float,
                               pending: list[dict] | None = None) -> dict | None:
        """单成员门禁：不支持 / 已有持仓 / 已有挂单 / 预算过小 → 明细；通过 → None（码对齐既有实现）。"""
        if not cfg.get("instrumentSupported"):
            return {"instId": inst_id, "code": "LIVE_INSTRUMENT_UNSUPPORTED",
                    "message": "仅支持 USDT 本位永续 / 交割合约自动交易"}
        if self._has_position(positions, inst_id):
            return {"instId": inst_id, "code": "AI_TRADE_PREEXISTING_POSITION",
                    "message": "该标的已有持仓，AI 自动交易拒绝启动（请先平仓或换标的）"}
        if self._has_pending_order(pending or [], inst_id):
            return {"instId": inst_id, "code": "AI_TRADE_PENDING_ORDER_EXISTS",
                    "message": "该标的已有未成交挂单，AI 自动交易拒绝启动（请先在实盘面板撤单或换标的）"}
        if self._budget_too_small(cfg, budget_i, lever):
            return {"instId": inst_id, "code": "AI_TRADE_MEMBER_BUDGET_TOO_SMALL",
                    "message": "分配预算不足最小下单量，请提高总预算或该标的百分比"}
        return None

    @staticmethod
    def _dust_risky(req: AiTradeGroupStartRequest, config: dict, budget: float,
                    lever: float) -> bool:
        """[P0-4] 单成员的下单量可行性判据（启动回执与纪律条**共用这一份**）。

        既有 dust 预检 `_budget_too_small` 按 **f = 1.0** 算，本判据按该预设的保守仓位下界
        `f_floor` 再算一次。两个消费者（`_note_dust_risk` 的回执、`executor.dust_risk` 驱动的
        `volSizing` 纪律条）必须读同一个式子——两处各写一遍，回执与面板迟早会给出相反的结论。
        """
        floor = float(req.discipline_f_floor)
        if floor >= 1.0 or not req.discipline_preset or req.discipline_preset == "off":
            return False    # 未装配预设 ⇒ 运行期 f 仍由模型自报 ⇒ 既有 f=1.0 预检已是同一口径
        return AiTradeManager._budget_too_small(config, budget * floor, lever)

    @staticmethod
    def _note_dust_risk(req: AiTradeGroupStartRequest, plans: list[_MemberPlan],
                        lever: float) -> None:
        """[P0-4] 装配仓位层后的**下单量可行性复核**——本轮最可能把用户直接劝退的断面。

        启动期既有的 dust 预检 `_budget_too_small` 按 **f = 1.0**（全额预算 × 杠杆）算可下张数；
        而装配 `sizing` 之后运行期真实下单量是 `f × budget`，波动率目标化 + 凯利缩放下 f 常落在
        0.2~0.4，`sizing.py` / `sizing_runtime.py` 里又**没有任何 dust 下限保护**。真的算小了，
        `executor._do_open` 只会发一条 status 文案就跳过——而那条 message 在 AI-trade 前端没有渲染
        落点。合起来就是「预检放行 → 每一轮开仓静默不下单 → 用户只看到 AI 一直不开单」，并极可能
        被误判成「纪律预设把 AI 弄坏了」。

        **刻意不 422**：把「今天本来能启动」的用户挡在门外是更坏的回归。改为在启动回执的
        `discipline.adjustments` 里如实追加一条；digest 的 `volSizing` 行由 `executor.dust_risk`
        承载同一句风险提示（G-14 要求**两个**落点都有——只写进回执，主面板上那一行仍然一片正常）。
        """
        floor = float(req.discipline_f_floor)
        risky = [p.inst_id for p in plans
                 if AiTradeManager._dust_risky(req, p.config, p.budget_usd, lever)]
        if risky:
            req.discipline_adjustments.append({
                "field": "volSizing", "from": None, "to": None,
                "instIds": risky,
                "reason": f"预算偏小：仓位层按该预设的保守下界（f={floor:.2f}）解算出的单笔委托量"
                          f"可能低于交易所最小下单量（{'、'.join(risky)}），届时该轮会跳过开仓。"
                          "提高总预算或该标的百分比可消除"})

    @staticmethod
    def _has_position(positions: list[dict], inst_id: str) -> bool:
        return any(str(p.get("instId")) == inst_id and abs(p.get("pos") or 0.0) > 0
                   for p in positions)

    @staticmethod
    def _has_pending_order(pending: list[dict], inst_id: str) -> bool:
        """该标的是否已有未结**普通**挂单（§2.9；`pending` 已由 `_fetch_blocking_pending` 按 kind 过滤）。

        会话以 in-memory `resting` 跟踪挂单，若交易所侧已有来路不明的挂单，自管状态与真实状态从第一
        秒就不一致——**绝不静默接管来路不明的挂单**，显式拒绝启动并提示先在 OKX 侧处理（§5.2）。
        """
        return any(str(o.get("instId")) == inst_id for o in pending)

    @staticmethod
    def _budget_too_small(cfg: dict, budget_i: float, lever: float) -> bool:
        """镜像 LiveExecutor._compute_sz：可下张数 < minSz 即判过小；行情缺失则不拦（运行期再守）。"""
        meta = cfg.get("instMeta") or {}
        ct_val = meta.get("ctVal") or 0.0
        min_sz = meta.get("minSz") or 0.0
        lot_sz = meta.get("lotSz") or 0.0
        last_px = cfg.get("lastPx") or 0.0
        if ct_val <= 0 or last_px <= 0 or min_sz <= 0:
            return False
        raw = (budget_i * lever) / (ct_val * last_px)
        return snap_size(raw, lot_sz) < min_sz

    async def _build_group(self, req: AiTradeGroupStartRequest, creds: AccountCredentials,
                           provider: object, plans: list[_MemberPlan], total: float,
                           lever: float) -> AiTradeGroup:
        """第二阶段·建会话（**不启动**）：逐成员建 Executor + Decider + Run + Session（止盈止损按比例分摊）。"""
        # 续跑复用**同一** group_id 与同一批 run（不变式 I-3）；`created_ts` 也取原分组的，
        # 否则窗口对账只覆盖后半段、`coverageMismatch` 恒真（§3.5 S6）。
        resume_of = req.resume_of or {}
        created_ts = int(resume_of.get("createdTs") or time.time())
        # 敞口簿是**分组级**共享状态（每成员一个 runtime，共用一本簿）；`unit` 取平均单标的满仓名义额。
        rule = parse_sizing_rule(req.settings)
        book = (GroupExposureBook(unit_notional=total * lever / max(1, len(plans)),
                                  cap_mult=rule.exposure_cap_mult, bar_seconds=BAR_SECONDS[req.bar])
                if rule is not None and rule.exposure_cap_mult > 0 else None)
        ctx = _GroupBuildCtx(
            req=req, creds=creds, provider=provider, lever=lever,
            sum_pct=sum(p.allocation_pct for p in plans), total_budget_usd=total,
            group_id=str(resume_of.get("groupId") or f"aitradegrp_{uuid.uuid4().hex}"),
            created_ts=created_ts, book=book)
        members = [await self._build_member(ctx, p) for p in plans]
        group = AiTradeGroup(
            group_id=ctx.group_id, bar=req.bar,
            strategy_ids=[s.strategy_id for s in req.specs],
            strategy_names=[s.strategy_name for s in req.specs],
            total_budget_usd=total, leverage=lever,
            context_refresh_rounds=req.context_refresh_rounds,
            created_ts=created_ts, members=members,
            wake_interval_s=req.wake_interval_s, base_rule=req.base_rule,
            profit_rule=req.profit_rule, sizing_rule=rule, exposure_book=book,
            stop_rule=parse_stop_rule(req.settings), horizon_rule=req.horizon_rule,
            discipline_preset=req.discipline_preset,
            discipline_adjustments=req.discipline_adjustments,
            discipline_thresholds=req.discipline_thresholds,
            account_id=req.account_row.id, start_wire=dict(req.start_wire or {}),
            resumed_count=int(resume_of.get("resumedCount") or 0))
        for member in members:
            # 可续跑契约的两条会话侧接线（pause-resume §3.4.2）：`group_id` 让 W2/W3 的增量写有
            # 落点；`on_stopped` 是 W5——会话**自终止**（暂停期止盈止损 / stale_feed / 启动回滚 /
            # stop_member）之后把状态表收敛，否则磁盘上会留一张指向已平仓会话的续跑卡。
            member.session.group_id = group.group_id
            member.session.on_stopped = partial(self._note_session_stopped, group)
        return group

    async def _build_member(self, ctx: _GroupBuildCtx, plan: _MemberPlan) -> AiTradeGroupMember:
        """建单成员会话：Executor（按 budget_i）+ Decider + Run（含 groupId/allocationPct）+ Session。

        两套风控须区分（勿混）：① **会话级止盈损 USD**（tp_i/sl_i）由分组总额按 pct/Σpct 分摊（各成员
        阈值之和恒等于总额）；② **单仓交易所侧 OCO pct** 经 `_resolve_trade_risk` 夹紧后**各成员同值**
        （来自 req.stop_loss_pct/take_profit_pct，留空回落 config 默认）。`AiTradeSession` 构造签名不变。
        """
        req = ctx.req
        executor = self._build_executor(ctx.creds, req, plan.inst_id, plan.config,
                                        plan.budget_usd, ctx.lever)
        tp_i = self._scale_threshold(req.take_profit_usd, plan.allocation_pct, ctx.sum_pct)
        sl_i = self._scale_threshold(req.stop_loss_usd, plan.allocation_pct, ctx.sum_pct)
        # ai-trade-limit-orders §5.3：限价总闸只在**生产实盘装配**处显式打开（决策器与回测均默认
        # False）——schema 露出 orderType/limitPrice、动作枚举加 cancel_order，皆由此单点决定。
        allow_limit = req.settings.ai_trade_limit_orders_enabled
        # 仓位/刹车层：三条规则由本层从 `settings` 自解析（[P1-7]：**不给启动请求加字段**，否则永远没人填）。
        sizing = build_sizing_runtime(req.settings, plan.inst_id, BAR_SECONDS[req.bar],
                                      book=ctx.book, budget_usd=plan.budget_usd)
        # 交易周期模式（holding-horizon §2.6）：前瞻窗口与交易所侧 OCO 读数由**同一份契约**派生，
        # 使宪章说的、成本判定用的、先验校验的是同一个数（未装配 ⇒ None ⇒ 三处逐字回退）。
        risk = self._resolve_trade_risk(req, req.settings)
        hold_bars = None if req.horizon_rule is None else req.horizon_rule.expected_hold_bars
        # 研判深度档（deep-reasoning-chain §3.0）：**一次解析、三处使用**——三份 LLM 预算、
        # 决策系统 prompt 的审议协议、以及决策器（超时 / 墙钟闸 / 追问阈 / 反思侧复核协议）。
        # 反思协议**不在这里注入**：`build_reflection_system_prompt` 全仓唯一的调用点是
        # `decider._stream_reflection`（每轮现造 system），在这里传 `reasoning_text` 会落空
        # 且不报错、不判红（spec §3.2 / P0-5，装配判据见用例 S-7）。
        depth = resolve_depth_profile(req.settings, override=req.reasoning_depth)
        clients = build_ai_trade_llm_clients(
            ctx.provider, req.settings, profile=depth,
            # 工厂必须从**本模块**传进去：20+ 个测试文件按
            # `app.ai_auto.manager.make_llm_client_from_config` 注入假客户端，换到 llm_budget
            # 内解析会一次性打翻它们全部（本仓库前科：「换代打破测试注入缝」）。
            make_client=make_llm_client_from_config)
        decider = AiTradeDecider(
            clients.decision,
            build_ai_trade_system_prompt(
                plan.inst_id, req.bar, plan.budget_usd, ctx.lever,
                options=SystemPromptOptions(
                    position_step_pct=req.position_step_pct,
                    allow_limit=allow_limit,
                    # F-3：价带文案与本地门禁同源（settings 唯一真源）
                    limit_band_pct=req.settings.ai_trade_limit_price_max_deviation_pct),
                texts=SystemPromptTexts(
                    # 审议协议 + 输出预算纪律；off 档 → "" ⇒ 中文脊柱逐字节回退。
                    # `symmetry` 把 S7 / S10 / S11 三处 mandate 方向对称化（轴 C-1）；
                    # 总闸关 → 三处逐字节回退。
                    reasoning_text=build_deliberation_protocol(
                        depth,
                        reserve_tokens=req.settings
                        .ai_trade_reasoning_reserve_tokens,
                        symmetry=req.settings
                        .ai_trade_directional_symmetry_enabled),
                    # 方向对称宪章章（轴 C-2 的第六段）；总闸关 → "" ⇒ 两条脊柱逐字节回退。
                    # ⚠️ `backtest.py` 是同款的第二个 `SystemPromptTexts` 构造点，只填一处会让
                    # 实盘与回测的 prompt 分叉（`base_rules` P1-4 的成文教训）。
                    direction_text=format_direction_charter(
                        req.settings.ai_trade_directional_symmetry_enabled),
                    # 【基础策略】宪章章（软引导）；未装配 → "" ⇒ 渲染逐字节不变
                    base_rules_text=format_charter(req.base_rule),
                    # 利润保护宪章章（软引导）；未装配 → "" ⇒ 渲染逐字节不变
                    profit_guard_text=format_profit_guard_charter(
                        req.profit_rule),
                    # 成本宪章章（轴 D）；总闸关 → "" ⇒ 渲染逐字节不变
                    cost_text=build_charter_text(
                        req.settings, plan.inst_id,
                        fee_rates_wire=plan.fee_rates_wire,
                        bar_seconds=BAR_SECONDS[req.bar],
                        allow_limit=allow_limit,
                        expected_hold_bars=hold_bars),
                    # 交易周期宪章章（软引导）；未装配 → "" ⇒ 渲染逐字节不变。
                    # 解除阈按**本会话真实的** OCO 止损与杠杆现算，绝不写死算例。
                    horizon_text=format_horizon_charter(
                        req.horizon_rule,
                        stop_loss_pct=risk.stop_loss_pct,
                        take_profit_pct=risk.take_profit_pct,
                        leverage=ctx.lever,
                        # 「不必每轮盯盘」这句授权只有在**真的有核实通道**
                        # 时才成立（turnover-budget §2.3.3）：总闸关或无凭证
                        # ⇒ 改口为「尚无核实通道」，绝不据一句未经核实的
                        # 承诺让模型放松盯盘。
                        backstop_armed=bool(
                            req.settings.ai_trade_backstop_check_enabled
                            and ctx.creds is not None)),
                    # 仓位与风险层宪章章；三条规则全未装配 → "" ⇒ 渲染逐字节不变
                    sizing_text=format_sizing_charter(
                        sizing.rule, sizing.stop_rule, sizing.brake_rule),
                    # 战术周期串：与会话真正注入的周期集合**同源**（共用
                    # `tactical_bars_for`），说的和给的必须是同一批周期
                    tactical_bars_text=format_tactical_bars_text(
                        tactical_bars_for(req.settings, req.bar,
                                          req.wake_interval_s)))),
            context_refresh_rounds=req.context_refresh_rounds, settings=req.settings,
            # 三份预算 + 深度档（§3.3.1 / §3.1.1）：反思与摘要各走自己的 client，深度档是决策器
            # 上的唯一开关（超时 / 尝试次数闸 / 轮级墙钟闸 / 追问阈 / 反思侧复核协议全由它派生）。
            reflection_llm=clients.reflection, summary_llm=clients.summary,
            depth_profile=depth,
            # 中文证据摘要（§3.1.7）：随深度档露出；off 档 ⇒ schema 恒等回退（G-2）。
            allow_reasoning_digest=depth.enabled,
            position_step_pct=req.position_step_pct,
            reflection_enabled=req.settings.ai_trade_reflection_enabled,   # 生产实盘显式开（默认 True）
            allow_limit=allow_limit,
            # C1 §2.4：承诺退出线总闸经既有 profit_rule 通路派生（装配保护层且承诺开启时露出 holdExitPx）。
            allow_hold_exit=(req.profit_rule is not None and req.profit_rule.commit_enabled),
            # exit-priority §6.1：离场优先通道总闸经既有 horizon 契约通路派生（装配了周期层且
            # 本层已装配时才露出 `exitConviction` / `exitEvidence`）。未装配 ⇒ schema / 描述 /
            # 三份 prompt 全部逐字节回退（X-7）。
            allow_exit_priority=getattr(req.horizon_rule, "exit_priority", None) is not None,
            # directional-symmetry A-3：`contrarianEvidence` 随逆势通路露出。两个总闸任一关 /
            # `min_consensus<=0`（共识门禁本就不触发）⇒ `None` ⇒ 工具 schema 逐字节回退。
            # `req.settings` 是 `_resolve_discipline` 之后的派生副本，与 executor 那一份同源。
            contrarian_schema=contrarian_schema(
                consensus_rule_from_settings(req.settings)),
            # intel-closed-loop 轴 A：`intelRefs` 随情报总闸与 cite 单键露出。任一关 ⇒ 工具
            # schema **对象恒等**回退到 `DECISION_TOOL_SCHEMA`（G-1）。回测车道恒不装配（R-12
            # 红线：历史 point-in-time 语料取不回来，接入必然前视偏差）。
            allow_intel_refs=bool(req.settings.ai_trade_intel_enabled
                                  and req.settings.ai_trade_intel_cite_enabled),
            intel_max_refs=int(req.settings.ai_trade_intel_cite_max_refs))
        # 不变式 I-3（续跑不新建 run）：冷续跑逐成员**复用同一 `run_id`**，成交 / 指标 / 历史聚合
        # 天然连续，一次交易在历史列表里永远只有一条。这是本方法与 `start` 路径的**唯一**差异。
        run_id = ((ctx.req.resume_of or {}).get("runIds") or {}).get(plan.inst_id)
        if run_id is None:
            run_id = await self._create_group_run(ctx, plan, tp_i, sl_i)
        session = AiTradeSession(
            f"aitrade_{uuid.uuid4().hex}", run_id, req.specs, plan.inst_id, req.bar, executor,
            decider, take_profit_usd=tp_i, stop_loss_usd=sl_i,
            priming=req.priming, priming_bars=req.priming_bars,
            context_refresh_rounds=req.context_refresh_rounds,
            wake_interval_s=req.wake_interval_s, base_rule=req.base_rule,
            profit_rule=req.profit_rule,
            prior_snapshot=(req.prior_snapshots or {}).get(plan.inst_id), sizing=sizing,
            horizon_rule=req.horizon_rule,
            # 只为落库回答「这次会话开的是哪个预设」；阈值本身已在 executor.settings 那份派生副本里。
            discipline_preset=req.discipline_preset,
            track_record=(req.track_records or {}).get(plan.inst_id),
            # 经验层 W-3：`run_id` 在本方法尾部才确定 ⇒ runtime 必须在它之后构造（`EventIdentity`
            # 的 `run_id` 是事件的溯源身份之一）。未装配 ⇒ 快照与规则皆 None ⇒ 全链路空转。
            experience=ExperienceRuntime(
                req.experience_snapshot, req.experience_rule, req.settings,
                EventIdentity(group_id=ctx.group_id, run_id=run_id,
                              inst_id=plan.inst_id, bar=req.bar)),
            on_experience_dirty=partial(self._on_experience_dirty, ctx.group_id))
        return AiTradeGroupMember(inst_id=plan.inst_id, allocation_pct=plan.allocation_pct,
                                  budget_usd=plan.budget_usd, session=session, run_id=run_id)

    async def _launch_group_or_rollback(self, group: AiTradeGroup) -> AiTradeGroup:
        """第二阶段·起会话；任一失败 → 回滚**全部**成员会话 + 清组，不掩盖原异常（镜像 live-auto）。"""
        try:
            for member in group.members:
                await member.session.start()
        except Exception:
            # 回滚须覆盖**所有**成员（非仅已起的）：_build_group 已为每成员建 status="running" 的 Run，
            # session.stop 幂等且对未起会话安全（落 Run 终态并清理 WS/盯市），杜绝孤儿滞留。
            for member in group.members:
                try:
                    await member.session.stop("start_failed")
                except Exception:  # noqa: BLE001 —— 回滚清理失败不掩盖原始启动异常
                    pass
            self._group = None
            await self._drop_state_row_quietly(group.group_id)   # G-30：绝不留幽灵契约
            raise
        return group

    @staticmethod
    async def _drop_state_row_quietly(group_id: str) -> None:
        """回滚清理：删掉该组可能因竞态已写入的契约行。**绝不掩盖原始启动异常**，故全吞。"""
        try:
            async with get_sessionmaker()() as s:
                await state_repo.delete_group_state(s, group_id)
        except Exception as exc:  # noqa: BLE001 —— 见 docstring
            logger.warning("ai-trade state rollback cleanup skipped (%s): %s", group_id, exc)

    # ---------- 分组停止 ----------
    async def stop_group(self, group_id: str, reason: str = "user_stop") -> dict | None:
        """停所有成员（各自平自管仓、落 Run 终态），聚合每成员平仓回执；全部停止后清组。"""
        group = self._group
        if group is None or group.group_id != group_id:
            return None
        rows = []
        for member in group.members:
            flat = await self._stop_session(member.session, reason)
            rows.append({"instId": member.inst_id, "sessionId": member.session.session_id,
                         "runId": member.run_id, "flattened": flat})
        self._group = None
        self._forget_paused_ref(group_id)   # I-2：`_group` 与 `_paused_ref` 永不同时非空
        self._unwire_intel(group_id)        # **同步**：只 cancel、不 await（G-8 的时延门禁）
        return {"groupId": group_id, "stopReason": reason, "members": rows}

    def _forget_paused_ref(self, group_id: str) -> None:
        """命中该 group_id 时清掉冷占位（`_paused_ref` 的三处清空点之一，§3.3）。"""
        if self._paused_ref is not None and self._paused_ref.group_id == group_id:
            self._paused_ref = None

    async def stop_member(self, group_id: str, inst_id: str, reason: str = "user_stop") -> dict | None:
        """仅停某成员（其余续跑）；停后无 **active** 成员则清组（保证 group_status() 不误报僵尸分组）。

        [P1-10]：清组判据必须是 `is_active` 而不是 `state == "running"`。分组进入 `paused` 后没有
        任何成员是 running，于是对一个 paused 组调一次本端点就会**把整个占位清掉**：其余 paused
        成员的 monitor 仍在后台跑却再也路由不到，互斥与续跑卡同时消失。状态表侧的收敛由会话
        `stop()` 的 W5 回调（`_note_session_stopped`）负责，两者共用同一个 `is_active` 真源。
        """
        group = self._group
        if group is None or group.group_id != group_id:
            return None
        member = next((m for m in group.members if m.inst_id == inst_id), None)
        if member is None:
            return None
        flat = await self._stop_session(member.session, reason)
        if not any(m.session.is_active for m in group.members):
            self._group = None
            self._forget_paused_ref(group_id)
        return {"groupId": group_id, "instId": inst_id, "flattened": flat}

    # ---------- 暂停 / 继续 / 结束（ai-trade-pause-resume §3.3）----------
    async def pause_group(self, group_id: str, *, reason: str = "user",
                          flatten: bool = False) -> dict | None:
        """逐成员挂起；**不清 `self._group`**（热暂停的占位就是它自己，I-2）。

        返回聚合回执（含逐成员持仓身份与兜底覆盖）。分组不存在 ⇒ None（API 层 404）；
        已是 `paused` ⇒ 回上次结果（幂等，API 层据 `state` 判 409）。
        """
        group = self._group
        if group is None or group.group_id != group_id:
            return None
        if group.state == pause_core.STATUS_PAUSED:
            return {"groupId": group_id, "state": group.state, "alreadyPaused": True}
        receipts = [await member.session.pause(reason, flatten=flatten)
                    for member in group.members]
        group.state = pause_core.STATUS_PAUSED
        group.pause_reason = reason
        group.paused_ts = int(time.time())
        # 情报调度同步暂停：**只停调度、不取消在飞轮**，在飞轮跑完自然收敛（§3.5.1）。
        intel_scheduler.pause(group_id)
        rows = self._state_member_rows(group, receipts)
        await self._persist_group_state(group, status=pause_core.STATUS_PAUSED,
                                        member_rows=rows)     # W4（失败必须上抛）
        # 经验层 W-5b（★ E-5 / 评审 P0-5）：**关软件走的是这条路**（`ai_trade_shutdown_action`
        # 默认 `"hold"` ⇒ `shutdown()` 调 `pause_group("app_quit")`，不经 `_stop_session`）。
        # 只挂 stop 等于「桌面端正常退出时终局物化根本不发生」，去抖攒下的脏标志随进程消亡，
        # 「经验是累积的」在最常见的使用节奏下不兑现。**只物化不蒸馏**：暂停不是终结。
        for member in group.members:
            self._schedule_experience(
                self._experience_finalize(member.session, distill=False))
        settings = self._group_settings(group)
        return {
            "groupId": group_id, "state": group.state, "pauseReason": reason,
            "pausedTs": group.paused_ts, "holdPosition": not flatten, "resumable": True,
            "members": [{**receipt, "allocationPct": member.allocation_pct}
                        for member, receipt in zip(group.members, receipts)],
            "notice": pause_core.build_pause_notice(
                rows, hold_position=not flatten,
                flatten_on_stale=bool(getattr(settings, "live_auto_flatten_on_stale", False))),
        }

    async def resume_group(self, group_id: str, *, acknowledged: bool) -> AiTradeGroup:
        """继续：热路径逐成员 `session.resume()`；冷路径委托 `ai_auto/resume.py` 重建。

        与 `start_group` 共用**同一对**锁（`get_admission_lock()` + `_start_lock`），否则
        「resume 与 start 并发」会造出双分组（§八风险 10）。

        热路径五步（ai-trade-lifecycle-truth B-2，修 D-1 与 L-5）：
          ① 体检（锁内、无 IO）：全 stopped ⇒ 自愈清组 + 409；部分 stopped ⇒ 409 逐标的明细；
          ② 逐成员 `resume()` 并**收集回执**（D-1 的整条根因就是这一个被丢弃的赋值）；
          ③ 复检 `_verify_armed`（**热 / 冷共用同一段判定**）；不通过 ⇒ 回滚 + 409；
          ④ 只有到这里才置 `running` 并落契约；⑤ 冷路径在 `resume_cold_group` 返回后走同一个 ③。

        **不存在「置了 running 但有成员没起来」的中间态**——那正是用户报告的那句「貌似恢复了」。
        """
        async with get_admission_lock(), self._start_lock:
            group = self._group
            if group is not None and group.group_id == group_id:
                if group.state != pause_core.STATUS_PAUSED:
                    raise AiTradePreflightError(
                        "AI_TRADE_NOT_PAUSED", "该分组不处于已暂停状态", 409)
                await self._preflight_resume(group)                                 # ①
                receipts = [await member.session.resume() for member in group.members]  # ②
                await self._verify_armed(group, receipts)                           # ③
                group.state = pause_core.STATUS_RUNNING                             # ④
                group.pause_reason = None
                group.paused_ts = 0
                group.resumed_count += 1
                await self._persist_group_state(group, status=pause_core.STATUS_RUNNING)
                self._forget_paused_ref(group_id)
                intel_scheduler.resume(group_id)   # 热续跑：runtime 还在，只需放开调度
                return group
            from .resume import resume_cold_group     # 延迟导入：resume.py 反向依赖本模块
            started = time.time()
            resumed = await resume_cold_group(self, group_id, acknowledged=acknowledged)
            # B-9：慢续跑必须可诊断（前端超时档已抬到 3 分钟，但「到底慢在哪」只能由后端说）。
            logger.info("ai-trade resume cold pipeline took %.1fs (group=%s members=%d)",
                        time.time() - started, group_id, len(resumed.members))
            # [评审 R1-2] 冷路径**共用同一段复检**：用户报告里「关软件 → 重开 → 继续」正是这条路。
            # 复检的价值不在拦截率（刚 `_prime()` 完通常必然 armed），而在「说 running 之前先核实
            # 一遍」这条纪律**没有第二种写法**。
            await self._verify_armed(resumed, None, cold=True)                      # ⑤
            self._group = resumed
            self._paused_ref = None
            # 冷续跑：进程里没有 runtime 了，必须整个重建。`ai_intel_watch.next_due_ts` 让它接着
            # 上次的节奏跑，而不是一开机就把 N 个标的一起打出去（R-10）。
            await self._wire_intel(resumed)
            await self._persist_group_state(resumed, status=pause_core.STATUS_RUNNING)
            return resumed

    async def _preflight_resume(self, group: AiTradeGroup) -> None:
        """① 体检（锁内）。总闸关 ⇒ 整段不参与（逐字回退到本轮之前）。

        「全 stopped」那一格要做的是**自愈**而不只是拒绝：`_note_session_stopped` 之前不清组，
        故进程里可能留着一个僵尸分组，磁盘上还留着一张指向它的 `paused` 契约。只清内存不收敛
        契约的话，用户照着 409 里那句「请重新启动一个新的分组」去点，会被容量闸拿
        `AI_TRADE_PAUSED_GROUP_EXISTS` 挡回来 —— 一句给不出出路的错误提示，比不给提示更坏。
        """
        if not self._lifecycle_truth_enabled(group):
            return
        states = [m.session.state for m in group.members]
        if not states or all(s == "stopped" for s in states):
            self._clear_group_if_current(group)
            await self._mark_state_ended(group.group_id)     # 契约一并收敛，别把出路堵死
            self._forget_paused_ref(group.group_id)
            raise AiTradeGroupEnded(
                "这个分组的所有会话都已结束（" + self._ended_because(group)
                + "）。请重新启动一个新的 AI 自动交易分组。",
                members=self._resume_member_rows(group))
        if any(s == "stopped" for s in states):
            raise AiTradeResumePartiallyDead(self._resume_member_rows(group))
        if any(s == "running" for s in states):
            raise AiTradePreflightError(
                "AI_TRADE_NOT_PAUSED", "该分组已有成员在运行中，无需继续", 409)

    @staticmethod
    def _ended_because(group: AiTradeGroup) -> str:
        """整组已终止时的原因短语（取第一个能说清楚的 `stop_reason`）。"""
        for member in group.members:
            why = lifecycle_core.STOP_REASON_TEXT.get(
                str(getattr(member.session, "stop_reason", None) or ""), "")
            if why:
                return f"原因：{why}"
        return "原因未知"

    @staticmethod
    def _resume_member_rows(group: AiTradeGroup) -> list[dict]:
        """逐标的明细（`.members`，与 `AI_TRADE_GROUP_PREFLIGHT` 同形，前端直接复用渲染）。

        **`message` 一律由后端生成**：本仓库「稳定语义码直出 UI」的事故已经发生过一次，
        前端不得按 code 自己拼中文（§5.3）。
        """
        rows = []
        for member in group.members:
            session = member.session
            state = getattr(session, "state", "running")
            if state == "stopped":
                why = lifecycle_core.STOP_REASON_TEXT.get(
                    str(getattr(session, "stop_reason", None) or ""), "")
                message = f"会话已结束（{why}）。" if why else "会话已结束。"
            elif state == "paused":
                message = "会话仍在暂停态，可以继续。"
            else:
                message = "会话正在运行。"
            rows.append({"instId": member.inst_id, "code": state, "message": message})
        return rows

    async def _verify_armed(self, group: AiTradeGroup, receipts: list[dict] | None,
                            *, cold: bool = False) -> None:
        """③ 「说 running 之前先核实一遍」的**单点**实现（热 / 冷两条路径共用）。

        既是 L-5 的守卫，也是 [评审 R0-5] 的安全前置：`_check_staleness` 在
        `elapsed > stale_after * 4` 且 `live_auto_flatten_on_stale=True`（[P2-7]）时会
        **直接 `stop("stale_feed")` 平掉真实持仓**。B-4 让陈旧时钟变得如实之后，一个「断了
        很久才点继续」的分组如果被置成 running 再由 monitor 去发现陈旧，用户点下的「继续」
        会在几秒内变成一次强平。

        故：复检判到 `feed_stale` / `feed_down` ⇒ 当场不武装 ⇒ 回滚回 `paused`；用户看到的是
        一句「继续失败，已退回暂停态（原因：行情不可达）」而不是一次没人解释的强平。
        暂停态下该强平判据与今天**逐字一致**（`_check_staleness` 本就是 `is_active` 判据），
        本轮不豁免、也不加严 —— 只是不让「继续」这个动作成为它的触发器。

        **必须在任何 monitor tick 之前跑完**：本方法是纯内存判定（唯一的 await 在回滚分支上），
        而 monitor 循环的第一件事是 `await asyncio.sleep(poll_interval_s)`，故这条时序成立。

        Args:
            group: 刚刚 resume 过的分组。
            receipts: 逐成员 `session.resume()` 的回执（冷路径为 None）。
            cold: 冷路径（回滚方式不同：那些会话是本次新建的，直接 `stop()` 掉）。

        Raises:
            AiTradeResumeNotArmed: 有成员没有武装成功（已完成回滚）。
        """
        if not self._lifecycle_truth_enabled(group):
            return
        readings = [self._member_readings(m) for m in group.members]
        # 回执按**位置**对齐，不按 `instId` 建索引：`session.resume()` 的回执形状是
        # `{"ok", "state", "reason"?, "reasonText"?, "lifecycle"?}`，**里面没有 instId** ——
        # 按 instId 查表会得到一张永远查不中的表，于是 ② 收集来的回执又一次被丢掉，
        # 而 D-1 的整条根因就是「回执被丢掉」。位置对齐是安全的：② 那行推导式逐成员顺序生成。
        receipt_rows = list(receipts or [])
        failed = []
        for index, (member, reading) in enumerate(zip(group.members, readings)):
            receipt = receipt_rows[index] if index < len(receipt_rows) else None
            declined = isinstance(receipt, dict) and receipt.get("ok") is False
            code, text = lifecycle_core.classify_member(reading)
            if declined or code != lifecycle_core.ARMED_OK:
                failed.append({"instId": member.inst_id,
                               "code": code if code != lifecycle_core.ARMED_OK
                               else lifecycle_core.NOT_ARMED_PAUSED,
                               "message": text or "会话没有武装成功。"})
        if not failed:
            return
        # 读数说得清就用读数的措辞（单标的省前缀、多标的点名）；只有「回执自己说 ok=False 而读数
        # 看起来正常」那一格会走回落，此时逐条摊开 `failed` —— 一句没有原因的「继续失败」正是
        # 本轮要消灭的那种话。
        detail = lifecycle_core.describe_not_armed(readings) or "；".join(
            f"{row['instId']}：{row['message']}" for row in failed)
        await self._rollback_resume(group, cold=cold)
        raise AiTradeResumeNotArmed(failed, "继续失败，已退回暂停态。" + detail)

    async def _rollback_resume(self, group: AiTradeGroup, *, cold: bool) -> None:
        """复检未过的整组回滚。**best-effort**：失败只 warning，绝不掩盖原始异常（风险 R-5）。

        热路径把已经起来的成员重新 `pause(resume_rollback, flatten=False)` —— `pause()` 是既有
        的**减风险**动作，恒可执行。冷路径沿用 `resume_failed` 那段收尾（先摘 W5 回调再 `stop()`）：
        摘回调是因为那条回调是给「一个已经在跑的会话自终止」用的、会把契约置 `ended`，而这里契约
        必须**留在 `paused`** 供再试。

        ⚠️ **冷路径的 `stop()` 必须显式 `flatten=False`**（spec 说「一字不改地复用」既有那段，
        但那段用的是默认 `flatten=True`）。既有那段跑在 `session.start()` 抛错时 —— 会话根本没起来；
        而本方法跑在**复检未过**时，此刻 `_adopt_group` 已经把交易所上那笔**真实持仓**灌进了
        executor，`flatten=True` 会当场把它平掉。那正是 [评审 R0-5] 花了整整一节要堵死的
        「点继续 → 几秒后被强平」，只是从另一扇门进来。详见 spec §实施过程发现的方案缺陷 I-11。
        """
        for member in group.members:
            session = member.session
            try:
                if cold:
                    session.on_stopped = None
                    await session.stop("resume_failed", flatten=False)
                elif session.state == pause_core.STATUS_RUNNING:
                    await session.pause(pause_core.PAUSE_REASON_RESUME_ROLLBACK, flatten=False)
            except Exception as exc:  # noqa: BLE001 —— 回滚失败不掩盖原始拒绝
                logger.warning("ai-trade resume rollback failed (%s): %s", member.inst_id, exc)
        if cold:
            # 冷路径此刻 `self._group` 还没被赋值（复检刻意排在赋值**之前**），故无组可清；
            # `_paused_ref` 也必须留着——契约仍是 `paused`，那张续跑卡应当还在。
            return
        group.state = pause_core.STATUS_PAUSED
        group.pause_reason = pause_core.PAUSE_REASON_RESUME_ROLLBACK
        group.paused_ts = int(time.time())
        try:
            await self._persist_group_state(group, status=pause_core.STATUS_PAUSED)
        except Exception as exc:  # noqa: BLE001 —— 契约本就是 paused，写失败只是没刷新原因
            logger.warning("ai-trade rollback persist failed (%s): %s", group.group_id, exc)

    async def end_group(self, group_id: str, *, flatten: bool | None,
                        acknowledged: bool) -> dict | None:
        """归档一个已暂停的分组（热 / 冷两条路径共用）。

        [P1-12]：**带仓时 `flatten` 必填**，且「不平仓」要求显式确认——否则「结束」会造出一个
        「本地已归档、交易所上仍在」的孤儿仓，而本轮的立论正是拿「今天已经存在孤儿持仓，只是
        没人告诉用户」来论证必要性，绝不能自己再造一个入口。回执**必带** `orphanPositions`。
        """
        state_row = await self._load_state_row(group_id)
        group = self._group if (self._group is not None
                                and self._group.group_id == group_id) else None
        ref = self._paused_ref if (self._paused_ref is not None
                                   and self._paused_ref.group_id == group_id) else None
        if group is None and ref is None and state_row is None:
            return None
        members = (state_row or {}).get("members") or (ref.members if ref else [])
        ended_from = (state_row or {}).get("status") or (
            group.state if group is not None else pause_core.STATUS_PAUSED)
        holding = self._has_recorded_position(members)
        flatten_flag = self._resolve_end_flatten(holding, flatten, acknowledged)
        residual: list[dict] = []
        if group is not None:
            for member in group.members:
                if not member.session.is_active:
                    continue
                flat = await self._stop_session(member.session, "user_end", flatten=flatten_flag)
                # 平仓**可能失败**（`stop()` 承诺「平仓失败也要完成停止流程」并如实回 `residual`）。
                # 不接住它，回执就会是「已平仓、无孤儿持仓」而交易所上仍留着一笔没人看管的仓——
                # 与 RV-2 同一形状：用户点的是「平掉并结束」，得到的是一句没兑现的承诺。
                if flatten_flag and not flat.get("ok"):
                    residual.append({"instId": member.inst_id, "position": flat.get("residual"),
                                     "positionUnknown": flat.get("residual") is None,
                                     "backstop": None})
            self._group = None
        else:
            # 冷路径：进程里没有会话、没有 executor、没有装配好的凭证，**没有任何东西能代用户下
            # 平仓单**。此时若照 `flatten=True` 归档，回执会说「已平仓、无孤儿持仓」而交易所上那笔
            # 仓位原样还在 —— 正是 §1.2 第三段拿来论证本轮必要性的那个洞，绝不能自己再造一个。
            # 故 fail-closed：明确拒绝并给出两条真实可走的路（与 §3.6 四格拒绝的出路同一口径）。
            self._require_cold_end_holdable(flatten_flag)
            await self._archive_cold_runs(members)
        await self._mark_state_ended(group_id)
        self._forget_paused_ref(group_id)
        self._unwire_intel(group_id)
        # `flattened` 描述的是**结果**而不是意图：请求平仓但有成员没平掉 ⇒ 它必须是 False，
        # 且那几笔如实进 `orphanPositions`（回执里这两个键绝不能互相打架）。
        return {"groupId": group_id, "endedFrom": ended_from,
                "stopReason": "user_end", "flattened": flatten_flag and not residual,
                "orphanPositions": residual if flatten_flag else self._orphan_rows(members),
                "members": [{"instId": m.get("instId"), "runId": m.get("runId"),
                             "sessionId": m.get("sessionId")} for m in members]}

    @staticmethod
    def _resolve_end_flatten(holding: bool, flatten: bool | None,
                             acknowledged: bool) -> bool:
        """[P1-12] 的服务端规则（无仓 ⇒ 可省；带仓 ⇒ 必填 + 不平仓须显式确认）。"""
        if not holding:
            return bool(flatten)
        if flatten is None:
            raise AiTradePreflightError(
                "AI_TRADE_END_FLATTEN_REQUIRED",
                "该分组仍有持仓，请明确选择「平掉这些持仓并结束」或「保留持仓，仅结束本地会话」", 400)
        if not flatten and not acknowledged:
            raise AiTradePreflightError(
                "AI_TRADE_END_ORPHAN_NOT_ACKNOWLEDGED",
                "保留持仓结束后，这些持仓将不再由任何自动化管理，请先确认你会自行在实盘面板处理", 400)
        return bool(flatten)

    @staticmethod
    def _require_cold_end_holdable(flatten: bool) -> None:
        """冷路径的「结束并平仓」是**做不到**的，故必须当场拒绝而不是事后谎报。

        软件重启后这次会话只剩磁盘上的一份契约：没有 Executor、没有暖机窗口，也就没有任何东西
        能下那一单。两条真实可走的路都让**用户**做决定（同 §5.3 删掉 `onDrift: flatten` 的理由）：
        先「继续」把会话恢复出来再停止，或到实盘面板自己平掉后再选「保留持仓，仅结束本地会话」。
        """
        if flatten:
            raise AiTradePreflightError(
                "AI_TRADE_END_FLATTEN_UNAVAILABLE",
                "软件重启后这次会话已不在进程内，无法代你下平仓单。请先点「继续」恢复会话再停止，"
                "或到实盘面板手动平掉这些持仓后，再选「保留持仓，仅结束本地会话」", 409)

    @staticmethod
    def _has_recorded_position(members: list[dict]) -> bool:
        """状态表快照里是否「带仓」。`positionUnknown` 一律**按带仓处理**（保守极性，同 S3 fail-closed）。"""
        return any(m.get("position") is not None or m.get("positionUnknown")
                   for m in members or [])

    @staticmethod
    def _orphan_rows(members: list[dict]) -> list[dict]:
        """此次结束后仍会留在交易所上的持仓（+ 各自最后一次已知的兜底覆盖情况）。"""
        return [{"instId": m.get("instId"), "position": m.get("position"),
                 "positionUnknown": bool(m.get("positionUnknown")),
                 "backstop": m.get("backstopAtPause")}
                for m in members or []
                if m.get("position") is not None or m.get("positionUnknown")]

    @staticmethod
    async def _archive_cold_runs(members: list[dict]) -> None:
        """冷路径归档：进程里没有会话对象，只把各成员的 run 落终态（绝不下任何单）。"""
        sm = get_sessionmaker()
        now = int(time.time())
        async with sm() as s:
            for member in members or []:
                run_id = member.get("runId")
                if run_id:
                    await repo.finalize_run(s, int(run_id), status="stopped", end_ts=now)

    def resumable(self) -> dict | None:
        """冷占位的只读 wire（`GET /ai-trade/status` 的 `resumable` 键）。

        **与 `group` 互斥**：热暂停走 `group.state="paused"`，冷暂停才走这里；两者同时非空即
        bug（G-9 冻结）。故本方法在 `self._group` 非空时恒回 None。
        """
        ref = self._paused_ref
        if ref is None or self._group is not None:
            return None
        now = int(time.time())
        return {"groupId": ref.group_id, "bar": ref.bar, "accountId": ref.account_id,
                "totalBudgetUsd": ref.total_budget_usd, "leverage": ref.leverage,
                "strategyNames": list(ref.strategy_names), "createdTs": ref.created_ts,
                "pausedTs": ref.paused_ts, "pauseReason": ref.pause_reason,
                "pauseReasonText": pause_core.PAUSE_REASON_TEXT.get(ref.pause_reason or "", ""),
                "resumedCount": ref.resumed_count,
                "offlineSeconds": max(0, now - ref.paused_ts) if ref.paused_ts else 0,
                "offlineText": pause_core.describe_offline_span(
                    max(0, now - ref.paused_ts) if ref.paused_ts else 0),
                "hasPosition": self._has_recorded_position(ref.members),
                "members": list(ref.members)}

    def adopt_paused_ref(self, ref: PausedGroupRef) -> None:
        """装上冷占位（由 lifespan 的水合协程调用）。**不启动任何东西**。"""
        self._paused_ref = ref
        self._hydration_failed = False

    def note_hydration_failed(self) -> None:
        """[P0-3]：水合有界重试仍失败 ⇒ 显式表示「我不知道」（而不是表示成「没有」）。"""
        self._hydration_failed = True

    def is_hydration_blind(self) -> bool:
        """水合盲区是否仍在（B-8，修 D-12）。

        今天这个事实只影响 `_paused_wire_for_mutex` 的互斥占位，**对用户完全不可见**：
        `group_status()` 与 `resumable()` 在盲区下都回 `None`，于是「后端不知道有没有暂停分组」
        在界面上表现为「没有暂停分组」—— 与真实资金上「读不到 ≠ 没有」的既定极性矛盾。
        """
        return self._hydration_failed

    # ---------- 生命周期真值（ai-trade-lifecycle-truth B-6 / L-1）----------
    @staticmethod
    def _lifecycle_truth_enabled(group: AiTradeGroup | None) -> bool:
        """整轴总闸（§七）。分组无成员时回落进程级设置。"""
        if group is not None and group.members:
            settings = group.members[0].session.executor.settings
        else:
            from ..config import get_settings
            settings = get_settings()
        return bool(getattr(settings, "ai_trade_lifecycle_truth_enabled", True))

    @staticmethod
    def _member_readings(member: AiTradeGroupMember) -> lifecycle_core.MemberReadings:
        """成员读数的**唯一**构造点。

        真实会话走 `AiTradeSession.lifecycle_readings()`（那里才有窗口、时钟与链路键）；
        测试替身 / 老会话对象没有这个方法 ⇒ 用 `getattr` 兜一份最小读数，把三处 `_FakeSession`
        的改动面压到最小（成文教训：`test_ai_trade_group.py` / `test_ai_trade_api.py` /
        `test_auto_trade_admission.py` 各有一个替身，一个只读视图不该把它们整片打红）。

        兜底值刻意选**不会造假红条**的那一组：`warmed_bars=1` / `stale_after_s=0` /
        `link_state="unknown"` ⇒ 一个 `running` 的替身会话恒判 armed。
        """
        session = member.session
        readings_fn = getattr(session, "lifecycle_readings", None)
        if callable(readings_fn):
            return readings_fn()
        return lifecycle_core.MemberReadings(
            inst_id=getattr(session, "inst_id", member.inst_id),
            state=getattr(session, "state", lifecycle_core.STATE_RUNNING),
            stop_reason=getattr(session, "stop_reason", None),
            pause_reason=getattr(session, "pause_reason", None),
            warmed_bars=1, feed_age_s=0.0, stale_after_s=0.0, link_state="unknown",
            next_wake_in_s=None, next_bar_in_s=None,
            rounds_completed=int(getattr(session, "rounds_completed", 0) or 0))

    def _note_state_mismatch(self, group: AiTradeGroup, derived: str) -> None:
        """`declaredState != state` 即 bug 的信号（风险 R-9）。

        **只在迁移瞬间记一次**（`_last_logged_mismatch` 去重），且是 `warning` 不是 `error`：
        一条会刷屏的告警很快就会被所有人忽略，那等于没有告警。
        """
        declared = getattr(group, "state", pause_core.STATUS_RUNNING)
        if declared == derived:
            self._last_logged_mismatch = None
            return
        signature = (group.group_id, declared, derived)
        if signature == self._last_logged_mismatch:
            return
        self._last_logged_mismatch = signature
        logger.warning("ai-trade lifecycle mismatch: group=%s declared=%s derived=%s",
                       group.group_id, declared, derived)

    # ---------- 可续跑契约的整行写点（W1 / W4 / W5）----------
    async def _persist_group_state(self, group: AiTradeGroup, *, status: str,
                                   member_rows: list[dict] | None = None) -> None:
        """整行 upsert（W1 / W4）。**失败必须上抛**——契约没落地却说续跑可用是欺骗。"""
        write = state_repo.GroupStateWrite(
            group_id=group.group_id, status=status, account_id=group.account_id,
            bar=group.bar, start_wire=group.start_wire,
            resolved_echo=self.group_status() or {},
            members=member_rows if member_rows is not None else self._state_member_rows(group),
            pause_reason=group.pause_reason, paused_ts=group.paused_ts or None,
            resumed_count=group.resumed_count)
        sm = get_sessionmaker()
        async with sm() as s:
            await state_repo.upsert_group_state(s, write)

    @staticmethod
    def _state_member_rows(group: AiTradeGroup,
                           receipts: list[dict] | None = None) -> list[dict]:
        """`members_json` 的逐成员行；有暂停回执时用**交易所真值**覆盖持仓身份（[P0-1] 的采集点）。"""
        by_inst = {str(r.get("instId") or ""): r for r in (receipts or [])}
        rows = []
        for member in group.members:
            row = member.session.state_member_row()
            row["allocationPct"] = member.allocation_pct
            row["budgetUsd"] = member.budget_usd
            receipt = by_inst.get(member.inst_id)
            # B-7（修 D-11）：判据必须是「回执里**真的有**持仓这个事实」，而不是「有回执」。
            # 成员非 `running` 时 `pause()` 早退回的那份回执**没有 `position` 键**，照
            # `receipt is not None` 覆盖会把契约里「这个标的还有仓」这个事实抹成 None ——
            # 续跑复核与「结束」的孤儿仓提示同时失真。
            if receipt is not None and "position" in receipt:
                row["position"] = receipt.get("position")
                row["positionUnknown"] = bool(receipt.get("positionUnknown"))
                row["backstopAtPause"] = receipt.get("backstop")
            rows.append(row)
        return rows

    async def _note_session_stopped(self, group: AiTradeGroup,
                                    session: AiTradeSession) -> None:
        """W5：某成员会话**自终止**后收敛状态表（置 `ended` 或只摘掉该成员）。

        触发来源覆盖全部自终止路径：暂停期的会话止盈止损、`stale_feed` 强平、启动回滚
        `stop("start_failed")`、`stop_member`。没有它，`strategy_runs` 已 `stopped` 而状态表仍
        `paused` ⇒ 磁盘上留一张指向已平仓会话的续跑卡。
        """
        sm = get_sessionmaker()
        if any(m.session.is_active for m in group.members):
            async with sm() as s:
                row = await state_repo.load_group_state(s, group.group_id)
                if row is None:
                    return
                rest = [m for m in row["members"]
                        if str(m.get("instId") or "") != session.inst_id]
                await state_repo.patch_group_state(s, group.group_id, {"members": rest})
            return
        await self._mark_state_ended(group.group_id)
        self._forget_paused_ref(group.group_id)
        # B-1（修 D-2）：**清组**。三处清组点 `stop_group` / `stop_member` / `_note_session_stopped`
        # 判据统一为 `not any(m.session.is_active …)`；此前只有前两处清，于是一个在暂停期自终止的
        # 分组会永久留在 `self._group` 里 —— `/ai-trade/status` 回一个 `state:"running"` 而成员全
        # `stopped` 的僵尸分组，`live_auto` 的互斥闸被它永久占住，用户再点「暂停」还会给这个僵尸
        # 写出一张新的 `paused` 契约（说谎循环）。
        self._clear_group_if_current(group)

    def _clear_group_if_current(self, group: AiTradeGroup) -> None:
        """带**身份守卫**的清组（[评审 R0-1]，B-1 与冷路径回滚共用）。

        `_note_session_stopped` 是经 `partial(self._note_session_stopped, group)` 绑定的
        **旧分组**回调。上一个分组的最后一个会话延迟自终止时，`self._group` 可能已经是**新分组**：
        无条件 `self._group = None` 会把新分组连同它占着的 live_auto 互斥位一起抹掉，界面整块
        消失而 Agent 其实还在跑 —— 这正是本轮要消灭的那句谎话的镜像版（说「没在跑」而它在跑）。

        判据用 `group_id` 而不是 `is`：冷续跑会重建分组对象，同一个 `group_id` 的新对象在语义上
        就是它自己。
        """
        if self._group is not None and self._group.group_id == group.group_id:
            self._group = None

    @staticmethod
    async def _mark_state_ended(group_id: str) -> None:
        async with get_sessionmaker()() as s:
            await state_repo.patch_group_state(s, group_id,
                                               {"status": pause_core.STATUS_ENDED})

    @staticmethod
    async def _load_state_row(group_id: str) -> dict | None:
        async with get_sessionmaker()() as s:
            return await state_repo.load_group_state(s, group_id)

    async def exit_member(self, group_id: str, inst_id: str) -> dict | None:
        """人工授权的**单次**市价离场（exit-priority §6.2）；分组 / 成员不存在 ⇒ `None` → 404。

        镜像 `stop_member` / `repair_member` 的既有姿态：只按 `(group_id, inst_id)` 定位成员并
        转发，判定、锁与下单一律在会话侧（`AiTradeSession.exit_now`）——manager 不做任何业务判断，
        **也不改分组生命周期**（`/exit` 是会话继续运行的中途动作，不是 `stop`）。
        """
        group = self._group
        if group is None or group.group_id != group_id:
            return None
        member = next((m for m in group.members if m.inst_id == inst_id), None)
        return None if member is None else await member.session.exit_now()

    async def relink_member(self, group_id: str, inst_id: str) -> dict | None:
        """人工授权的**单次**强制重连 + 活性补取（feed-self-healing §4.2）；缺失 ⇒ `None` → 404。

        镜像 `exit_member` / `repair_member` 的既有姿态：只按 `(group_id, inst_id)` 定位成员并
        转发，判定、冷却与死线一律在会话侧（`AiTradeSession.relink_feed`）——manager 不做任何业务
        判断，**也不改分组生命周期**（`/relink` 是会话继续运行的中途动作，不是 `stop`）。
        """
        group = self._group
        if group is None or group.group_id != group_id:
            return None
        member = next((m for m in group.members if m.inst_id == inst_id), None)
        return None if member is None else await member.session.relink_feed()

    async def repair_member(self, group_id: str, inst_id: str) -> dict | None:
        """人工授权的单次兜底补挂（backstop-repair §4.1）；分组 / 成员不存在 ⇒ `None` → 404。

        镜像 `stop_member` 的既有姿态：只按 `(group_id, inst_id)` 定位成员并转发，判定、频率锁与
        下单一律在会话侧（`AiTradeSession.repair_backstop`）——manager 不做任何业务判断。
        """
        group = self._group
        if group is None or group.group_id != group_id:
            return None
        member = next((m for m in group.members if m.inst_id == inst_id), None)
        return None if member is None else await member.session.repair_backstop()

    async def _stop_session(self, session: AiTradeSession, reason: str, *,
                            flatten: bool = True) -> dict:
        flat = await session.stop(reason, flatten=flatten)
        if not flat.get("ok"):   # 平仓失败不静默——日志留痕（API 仍回 flattened 供 UI 强提示）
            logger.warning("ai-trade group stop flatten failed: session=%s code=%s residual=%s",
                           session.session_id, flat.get("code"), flat.get("residual"))
        # 经验层 W-5：终局物化 + 叙事蒸馏在**返回前 spawn**，**绝不 await**（评审 F-1 / G-5）。
        # `stop_group` 串行遍历成员，而客户端 `/ai-trade/{groupId}/stop` 的超时档是
        # `FAST_MS = 20_000` 且被判为写路径——一次 45 秒的 LLM 调用（或一次 5 秒的物化 ×3 成员）
        # 会在真实资金的停止/平仓按钮上弹出「本次操作结果未知」。
        self._schedule_experience(self._experience_finalize(session))
        return flat

    # ---------- 经验层的后台调度（W-4；**逻辑本体在 `experience/scheduler.py`**）----------
    # 本文件已超 CLAUDE.md 的 800 行阈值，故这里只留薄转发：调度 / 变脏 / 终局 / 排空 / 死线。
    def _schedule_experience(self, coro) -> None:
        scheduler.schedule(coro)

    def _on_experience_dirty(self, group_id: str) -> None:
        """会话侧「有新事件落盘」的**同步**回调（`_dispatch` 的 close 侧 hook 直接调它）。"""
        scheduler.note_dirty(group_id, lambda: self._experience_ctx(group_id))

    def _experience_ctx(self, group_id: str) -> scheduler.GroupContext | None:
        """把后台物化需要的分组事实**同步**打包（绝不查 DB）；分组已不在 ⇒ None。"""
        group = self._group
        if group is None or group.group_id != group_id or not group.members:
            return None
        alive = [m.session for m in group.members if m.session.is_active]
        rule = next((m.session.experience.rule for m in group.members
                     if m.session.experience.rule is not None), None)
        return scheduler.GroupContext(settings=self._group_settings(group),
                                      sessions=alive, rule=rule)

    async def _experience_finalize(self, session: AiTradeSession, *,
                                   distill: bool = True) -> None:
        await scheduler.finalize(session, distill=distill)

    async def drain_experience_tasks(self) -> None:
        """关停链的一步：**先 cancel、再有界收敛**（★ E-4，死线由启动期校验器钉死 < 单步死线）。"""
        await scheduler.drain(self._experience_drain_timeout())

    # ---------- 市场情报层的薄转发（market-intel §6.1 #21；**判定全在 `intel/` 包内**）----------
    async def _wire_intel(self, group: AiTradeGroup, *, provider=None) -> None:
        """装配一个分组的情报流水线。**全程 fail-open**。

        情报是一条加法流水线：检索源没配、key 过期、模型不可用——任何一种都不该让一次真实资金
        的启动失败。这与 `_probe_tradability` 的取舍同源：一个旁路统计量把用户挡在门外，比给错
        建议更坏。
        """
        try:
            settings = self._group_settings(group)
            cfg = provider or await resolve_active_provider(get_sessionmaker(), settings)
            runtime = await intel_runtime.build_group_runtime(
                group_id=group.group_id,
                members=[member.inst_id for member in group.members],
                settings=settings, sessionmaker=get_sessionmaker(), provider_cfg=cfg,
                # **必须传本模块里的这一份** `make_llm_client_from_config`：20+ 个测试文件以
                # `monkeypatch.setattr("app.ai_auto.manager.make_llm_client_from_config", …)`
                # 注入假客户端，把工厂换到别处解析会一次性打翻它们全部（E-5）。
                make_client=make_llm_client_from_config, bar=group.bar,
                change_24h_pct=partial(self._intel_change_pct, group.group_id),
                notify=partial(self._on_intel_summary, group.group_id))
            intel_runtime.register(runtime)
            intel_scheduler.start(runtime)
        except Exception as exc:  # noqa: BLE001 —— 见 docstring
            logger.warning("ai-trade intel wiring skipped for %s: %s", group.group_id, exc)

    def _unwire_intel(self, group_id: str) -> None:
        """**同步**拆卸：只 cancel、不 await（G-8：`stop_group` 串行遍历成员，客户端超时档 20s）。"""
        intel_scheduler.cancel(group_id)
        intel_runtime.unregister(group_id)

    def _intel_change_pct(self, group_id: str, inst_id: str) -> float | None:
        """近 24 小时涨跌（**同步、只读会话内存的 K 线缓存**）；取不到 ⇒ None ⇒ 该行省略。"""
        group = self._group
        if group is None or group.group_id != group_id:
            return None
        member = next((m for m in group.members if m.inst_id == inst_id), None)
        if member is None:
            return None
        try:
            return member.session.change_24h_pct()
        except Exception:  # noqa: BLE001 —— 一行可选的上下文，绝不允许它打断一轮情报
            return None

    def _on_intel_summary(self, group_id: str, inst_id: str, summary: dict) -> None:
        """情报轮的 WS 出口（**同步回调，不 await**）：把 summary 推给对应成员会话。"""
        group = self._group
        if group is None or group.group_id != group_id:
            return
        member = next((m for m in group.members if m.inst_id == inst_id), None)
        if member is None:
            return
        intel_scheduler.schedule(member.session.push_intel(summary))

    async def drain_intel_tasks(self) -> None:
        """关停链的一步（`main._shutdown_steps()` 的第 7 步）：先 cancel、再有界收敛。

        死线是**独立设置项**而不是运行期算的比例，且由启动期校验器钉死 <
        `shutdown_step_timeout_s`——倒挂时外层先 cancel、内层清理永不执行（评审 P1-7）。
        """
        await intel_scheduler.drain(self._intel_drain_timeout())

    def _intel_drain_timeout(self) -> float:
        """drain 死线：优先取活跃分组的派生副本，无分组时回落进程级默认。"""
        group = self._group
        settings = self._group_settings(group) if group is not None and group.members else None
        if settings is None:
            from ..config import get_settings

            settings = get_settings()
        return float(getattr(settings, "ai_trade_intel_drain_timeout_s", 5.0))

    def _experience_drain_timeout(self) -> float:
        """drain 死线：优先取活跃分组的派生副本，无分组时回落进程级默认。"""
        group = self._group
        settings = self._group_settings(group) if group is not None and group.members else None
        if settings is None:
            from ..config import get_settings

            settings = get_settings()
        return float(getattr(settings, "ai_trade_experience_drain_timeout_s", 5.0))

    def experience_live_sessions(self) -> list[dict]:
        """运行中会话的经验快照时刻（**同步、只查内存，绝不查 DB**）。

        §7.1 的「检索预演」必须说实话：预演读的是**当前 DB 里的图**，而运行中的会话用的是
        **启动那一刻**载入内存的快照（不变式 I-3 使然），两者会因后台物化与其它成员会话的写入
        而分叉。少了这条对照，那一屏在最需要它的时刻（会话正在跑）说的是假话——
        **一屏假话比没有这一屏更坏。**
        """
        group = self._group
        if group is None:
            return []
        return [{"instId": member.inst_id,
                 "graphAsOfTs": member.session.experience.graph_as_of_ts}
                for member in group.members if member.session.is_active]

    # ---------- 只读视图 / WS 路由 ----------
    def status(self) -> dict | None:
        """当前活跃 AI 分组只读视图（= group_status()）。

        **关键**：这样 live_auto 侧 `_require_capacity` 的 `get_ai_trade_manager().status() is not None`
        无需任何改动即可正确检测活跃 AI 分组（live_auto 零改动）。互斥正确性前提：各停止路径
        （stop_group/stop_member/shutdown）在无运行成员时清 `self._group`，使 `status() is not None`
        ⟺ 存在活跃成员（`AiTradeSession` 仅经 stop() 离开 "running"、不自终止）。

        ⚠️ **本轮唯一一处改变既有函数返回值语义的地方**（pause-resume §3.3）：冷启后存在可续跑
        的暂停契约时，它回一个**轻量占位**而不是 `None`——否则 D-9 的洞会原样复发（一个带仓的
        AI 暂停会话完全拦不住用户去启动「策略自动交易」）。唯一消费者 `live_auto/manager` 只判
        `is not None`，故安全（G-11 冻结这条）。它必须保持**同步**（调用方在同步函数里），所以
        它绝不能查 DB —— 这正是 [P0-3] 用 `_hydration_failed` 内存标志表达「我不知道」的原因。
        """
        return self.group_status() or self._paused_wire_for_mutex()

    def _paused_wire_for_mutex(self) -> dict | None:
        """互斥用的最小占位（**同步**，绝不查 DB）：有冷占位、或**水合失败**时都返回非空。

        极性说明：**「读不到」与「没有」在真实资金上不是同一件事**。在这两者之间选默认值时，
        永远选更保守的那一个——宁可多拦一次 live-auto 启动，也不让两套自动化同管一个账户。
        盲区不会长期存在：任何一次 `_require_capacity` 的 DB 读成功即把它清掉。
        """
        ref = self._paused_ref
        if ref is not None:
            return {"groupId": ref.group_id, "state": pause_core.STATUS_PAUSED,
                    "bar": ref.bar, "members": []}
        if self._hydration_failed:
            return {"groupId": "", "state": "unknown", "bar": "", "members": []}
        return None

    def group_status(self) -> dict | None:
        """活跃分组只读视图（前端重载后据此重连各成员 WS）；无活跃分组→ None。

        **`state` 的语义在本轮变了（L-1）**：它不再是「上一次动作赋的值」，而是
        `derive_group_state(各成员会话态)` 的**派生真值**；旧语义经加法键 `declaredState`
        保留，只用于诊断与「两者不等即 bug」的告警。R-1 已全仓核实读点：已知消费者只有前端与
        `live_auto._require_capacity`（后者只判 `is not None`，不读 `state`）。
        """
        group = self._group
        if group is None:
            return None
        return {"groupId": group.group_id, "bar": group.bar,
                "strategyIds": group.strategy_ids, "strategyNames": group.strategy_names,
                "totalBudgetUsd": group.total_budget_usd, "leverage": group.leverage,
                "contextRefreshRounds": group.context_refresh_rounds,
                "wakeIntervalMinutes": (round(group.wake_interval_s / 60, 4)
                                        if group.wake_interval_s > 0 else None),
                "baseRules": rule_wire(group.base_rule),   # 【基础策略】回执镜像（未装配→ null）
                "profitGuard": profit_guard_wire(group.profit_rule),   # 利润保护回执镜像（同上）
                # 交易周期模式回执镜像（同上）：**生效值** + 两个派生量，使「弹窗预览 == 落地」可逐
                # 字段核对（[P1-9]：只回显入参等于把 expectedHoldBars 的派生关系藏起来）。
                "tradingHorizon": horizon_wire(group.horizon_rule),
                # 交易纪律预设回执（轴 A / 轴 B）：`rows` 取任一成员的 13 行自检——预设与阈值是
                # **分组共享**的（同一份派生 settings），逐成员各发一份只是噪音；成员之间真正会
                # 分叉的只有 `exchangeBackstop` 那一行，它已随各成员的 `status.disciplines` 下发。
                "discipline": self._discipline_wire(group),
                # 方向对称性回执镜像（directional-symmetry §7.1）：**服务端解析后的生效值**，
                # `null` = 未装配（总闸关 / 用户显式关闭 / 该预设的 `minConsensus<=0` 使共识门禁
                # 本就不触发）。回执必须能逐字段核对，否则「我在弹窗里关掉的逆势通路到底有没有
                # 生效」这个问题在产品里无从回答。
                "direction": self._direction_wire(group),
                # 轴 A 启动断面（§5.4）：**逐成员一份的列表**，各带 `instId` 与 `asOfTs`。
                # 桌面旧包 / 直接调 API 的用户拿不到弹窗那块预检，本回执是他们唯一的告知面。
                # 该端点 `-> dict` 且无 `response_model`，故新键不会被 FastAPI 静默吞掉（已核实）。
                "tradability": list(getattr(group, "tradability", None) or []),
                "createdTs": group.created_ts,
                # 分组生命周期（pause-resume §5.6）：`running` | `paused`。前端 `groupStateFromStatus`
                # 是**逐键手抄**的映射且此前把 `state` 写死成 "running"，那正是本仓库「静默吞新键」
                # 的成文缺陷形状——不下发这个键，暂停态在「刷新页面后重发现」这条路径上恒显示运行中。
                # `getattr` 兜底与上面 `tradability` 那一行同款：既有测试替身用 `SimpleNamespace`
                # 直接构造分组，新增字段对它们必须是**加法**，不能让一个只读视图把它们打红。
                **self._lifecycle_group_keys(group),
                "pauseReason": getattr(group, "pause_reason", None),
                "pausedTs": getattr(group, "paused_ts", 0),
                "resumedCount": getattr(group, "resumed_count", 0),
                # 验收闭环三键的**分组合并口径**（horizon-acceptance [P0-3] / [P1-9]）：合并的是
                # 各成员的原始计数器而不是已判定的卡——比例类指标不可平均。本端点由前端 15s 轮询
                # 消费，是分组口径唯一的实时来源（成员级实时数据仍走既有成员 WS status）。
                **self._acceptance_bundle(group),
                "members": [self._member_row(m) for m in group.members]}

    @staticmethod
    def _direction_wire(group: AiTradeGroup) -> dict | None:
        """分组级方向对称性回执；未装配 ⇒ `None` ⇒ 前端整段不渲染。

        取任一成员会话上的那份 `ConsensusRule`——它是**分组共享**的同一份派生 settings 派生出来
        的（`consensus_rule_from_settings`），逐成员各发一份只是噪音。

        `getattr` 兜底与本方法上下的 `tradability` / 生命周期三键**同款**：既有测试替身用
        `SimpleNamespace` 直接构造分组与会话，新增字段对它们必须是**加法**——不能让一个只读
        视图把它们打红（本仓库为这条已立过明文）。
        """
        if not group.members:
            return None
        reader = getattr(group.members[0].session, "direction_wire", None)
        return reader() if callable(reader) else None

    @staticmethod
    def _discipline_wire(group: AiTradeGroup) -> dict:
        """分组级纪律回执 = 静态预设（preset/阈值表/调整项）+ 任一成员现算的 13 行自检。

        `rows` 取第一个成员：预设与阈值是分组共享的同一份派生 settings，逐成员各发一份只是噪音。
        无成员 ⇒ `rows` 为空数组（**不是缺席**）——前端据此区分「还没成员」与「后端没上线这个键」。
        """
        digest = (group.members[0].session.discipline_wire() if group.members
                  else {"rows": [], "hint": None})
        return {"preset": group.discipline_preset,
                "presetLabel": PRESET_LABELS.get(group.discipline_preset,
                                                 group.discipline_preset),
                "thresholds": group.discipline_thresholds,
                "adjustments": group.discipline_adjustments,
                **digest}

    @staticmethod
    def _acceptance_bundle(group: AiTradeGroup) -> dict:
        """分组级验收卡 / 退出分布 / 资金费（合并各成员的原始计数器后**判定一次**）。

        settings 取自成员会话的 executor。**注意口径**：自 discipline-preset-and-integrity 轴 A 起，
        每个会话持的是 API 层派生的**会话私有 Settings 副本**（不再是进程级单例），故这里读到的是
        「本分组这次启动实际生效的那一份」——分组内各成员共享同一份副本，「阈值从哪来」在一个分组
        里仍然只有一个答案（[P2-7]：原注释「进程内同一个 Settings 实例」已随本轮变成假话，故订正）。
        无成员 ⇒ 三键 `None`。
        """
        if not group.members:
            return {"acceptance": None, "exits": None, "funding": None}
        bundles = [m.session.acceptance_bundle() for m in group.members]
        inputs = [inp for inp in (input_from_wire(b.get("acceptanceInput"))
                                  for b in bundles) if inp is not None]
        merged = merge_bundle(
            inputs, rule=group.horizon_rule,
            settings=group.members[0].session.executor.settings,
            fundings=[b.get("funding") for b in bundles])
        out = {key: merged[key] for key in ("acceptance", "exits", "funding")}
        if out["acceptance"] is not None and len(group.members) > 1:
            # R-10：合并只用于**总判定**，但一个赚一个亏被平均成「还行」是这张卡最危险的读法，
            # 故同时下发成员级摘要（笔数 / 净额 / 各自的结论）。单标的分组 ⇒ 整键缺席（那时它
            # 与总判定逐字相同，多挂一行只是噪音）。
            out["acceptance"]["bySymbol"] = [
                {"instId": m.session.inst_id,
                 "roundTrips": int((b.get("acceptanceInput") or {}).get("roundTrips") or 0),
                 "netPnlUsd": float((b.get("acceptanceInput") or {}).get("netPnlUsd") or 0.0),
                 "verdict": (b.get("acceptance") or {}).get("verdict")}
                for m, b in zip(group.members, bundles)]
        return out

    def _lifecycle_group_keys(self, group: AiTradeGroup) -> dict:
        """分组级 `state` / `declaredState` / `lifecycle` 三键（B-6）。

        总闸关 ⇒ **逐字回退**：只回 `state = getattr(group, "state", …)`（本轮之前的那一行），
        `declaredState` / `lifecycle` 整键缺席（用例 12 的护栏就是这一条）。
        """
        declared = getattr(group, "state", pause_core.STATUS_RUNNING)
        if not self._lifecycle_truth_enabled(group):
            return {"state": declared}
        readings = [self._member_readings(m) for m in group.members]
        derived = lifecycle_core.derive_group_state([r.state for r in readings])
        self._note_state_mismatch(group, derived)
        return {
            "state": derived,                   # ← 派生真值（前端唯一消费的那个）
            "declaredState": declared,          # ← 意图（诊断 / 告警用；两者不等即 bug 的信号）
            "lifecycle": lifecycle_core.group_lifecycle_wire(
                readings, declared=declared,
                paused_ts=int(getattr(group, "paused_ts", 0) or 0),
                pause_reason=getattr(group, "pause_reason", None),
                resumed_count=int(getattr(group, "resumed_count", 0) or 0),
                now_ts=int(time.time())),
        }

    def _member_row(self, member: AiTradeGroupMember) -> dict:
        """成员行 = 会话 `info()` 全字段 + 分配占比 + 成员级 `lifecycle`（B-6）。

        成员卡片要能**逐标的**说明「这一个到底起来了没有」——整组与门会把还在正常交易的那个
        也说成停了（[评审 R1-7]）。老会话对象 / 测试替身没有 `lifecycle_wire` ⇒ 整键缺席。
        """
        row = {**member.session.info(), "allocationPct": member.allocation_pct}
        wire_fn = getattr(member.session, "lifecycle_wire", None)
        if callable(wire_fn) and self._lifecycle_truth_enabled(self._group):
            row["lifecycle"] = wire_fn()
        return row

    def get(self, session_id: str) -> AiTradeSession | None:
        """跨所有成员按 session_id 查会话（WS 路由用）。"""
        group = self._group
        if group is None:
            return None
        return next((m.session for m in group.members if m.session.session_id == session_id), None)

    async def shutdown(self) -> dict:
        """进程退出：按 `ai_trade_shutdown_action` 分派（pause-resume §3.3 / [P0-2]）。

        - `"hold"`（**默认**）⇒ `pause_group(reason="app_quit")`：带仓暂停，重开后有一张续跑卡；
        - `"flatten"`（可选）⇒ 沿用既有 `_stop_session`（平仓 + 落 Run 终态）。

        默认值从 `"flatten"` 改成 `"hold"` 的理由：D-4 证明今天这条平仓路径（lifespan 那句
        `shutdown()`）在 Windows 上几乎从不执行，而 §3.8 新加的 `quit-pause` **HTTP** 钩子是
        **能跑通的**——默认 `flatten` 不是「保持现状」，而是**新增**「每次关软件都平掉真实仓位
        并归档」，用户重开后什么都续不了，本轮需求原地失效。

        已是 `paused` 的分组在两种配置下都**只 touch 时间戳、不平仓**。
        """
        group = self._group
        if group is None:
            return {"action": "noop", "groupId": None, "paused": False}
        settings = self._group_settings(group)
        # 整轴总闸关 ⇒ **逐字回退老行为**（平仓 + 落 Run 终态），绝不因为 `shutdown_action`
        # 默认是 `hold` 就让一个已回滚的部署改变关停语义。
        action = (str(getattr(settings, "ai_trade_shutdown_action", "hold") or "hold")
                  if getattr(settings, "ai_trade_pause_enabled", True) else "flatten")
        if group.state == pause_core.STATUS_PAUSED:
            await self._touch_state_row(group.group_id)
            return {"action": "noop", "groupId": group.group_id, "paused": True}
        if action == "flatten":
            for member in group.members:
                if member.session.is_active:
                    await self._stop_session(member.session, "shutdown")
            self._group = None
            self._forget_paused_ref(group.group_id)
            return {"action": "flatten", "groupId": group.group_id, "paused": False}
        await self.pause_group(group.group_id, reason=pause_core.PAUSE_REASON_APP_QUIT)
        return {"action": "hold", "groupId": group.group_id, "paused": True}

    @staticmethod
    def _group_settings(group: AiTradeGroup):
        """该分组这次启动实际生效的那份 Settings（会话私有派生副本，非进程级单例）。"""
        if group.members:
            return group.members[0].session.executor.settings
        from ..config import get_settings
        return get_settings()

    @staticmethod
    async def _touch_state_row(group_id: str) -> None:
        """只刷 `last_seen_ts`（`quit-pause` 命中已暂停分组时的 no-op 路径）。best-effort。"""
        try:
            sm = get_sessionmaker()
            async with sm() as s:
                await state_repo.patch_group_state(s, group_id, {})
        except Exception as exc:  # noqa: BLE001 —— 心跳是观测面
            logger.debug("ai-trade state touch skipped (%s): %s", group_id, exc)

    # ---------- 前置门禁辅助 ----------
    @staticmethod
    def _require_enabled(settings: Settings) -> None:
        if not (settings.ai_trade_enabled and settings.live_trading_enabled):
            raise AiTradePreflightError(
                "AI_TRADE_DISABLED",
                "AI 自动交易未启用（需 AI_TRADE_ENABLED 与 LIVE_TRADING_ENABLED 均开启）", 403)

    async def _require_capacity(self, settings: Settings | None = None) -> None:
        """单活跃分组独占 + 与 live-auto 互斥（须在共享准入锁内调用，P1-4）。延迟导入避免循环依赖。

        [P0-3]：暂停分组这一条的真值取**状态表**，不取内存。内存 `_paused_ref` 只是展示与互斥的
        缓存，而它的装载（lifespan 水合协程）按仓库惯例是「失败只 warning 不阻断启动」——一次
        偶发的水合失败绝不该变成「可以在一份带仓的暂停契约之上再开一个新分组」。这条 DB 读同时
        **自愈** `_hydration_failed`：读到了就说明 DB 可用，盲区解除。

        整轴总闸 `ai_trade_pause_enabled=False` 时**整条新闸不参与**（含它 fail-closed 的那个
        409）——一键回滚必须回滚到本轮之前的**完整**行为，不能留一个只在本轮才可能出现的新失败态。
        """
        if settings is None:
            from ..config import get_settings
            settings = get_settings()
        if settings.ai_trade_pause_enabled:
            await self._require_no_paused_group()
        group = self._group
        if group is not None and any(m.session.state == "running" for m in group.members):
            raise AiTradeSessionLimitError(
                "AI_TRADE_SESSION_LIMIT", "已有活跃 AI 自动交易分组（单活跃独占，请先停止）", 409)
        from ..live_auto.manager import get_live_auto_manager
        if get_live_auto_manager().group_status() is not None:
            raise AiTradeSessionLimitError(
                "AI_TRADE_SESSION_LIMIT", "已有活跃策略自动交易分组（与 AI 互斥，请先停止）", 409)

    async def _require_no_paused_group(self) -> None:
        """存在已暂停契约即拒绝新分组；DB 读**本身**失败也拒绝（fail-closed，与 §3.5 S3 同极性）。"""
        try:
            sm = get_sessionmaker()
            async with sm() as s:
                paused = await state_repo.load_paused_group_brief(s)
        except Exception as exc:  # noqa: BLE001 —— 读不到就不开新仓，绝不 fail-open
            logger.warning("ai-trade paused-group probe failed: %s", exc)
            raise AiTradeSessionLimitError(
                "AI_TRADE_RESUME_UNVERIFIABLE",
                "无法确认是否存在已暂停的 AI 自动交易分组，暂不启动新分组（请稍后重试）", 409) from exc
        self._hydration_failed = False        # 能读到即解除盲区（对 live_auto 同步生效）
        if paused is not None or (self._group is not None and self._group.state == "paused"):
            raise AiTradeSessionLimitError(
                "AI_TRADE_PAUSED_GROUP_EXISTS",
                "已有一个已暂停的 AI 自动交易分组（请先继续或结束它）", 409)

    def _validate_strategies(self, req: AiTradeGroupStartRequest) -> None:
        n = len(req.specs)
        if not (1 <= n <= req.settings.ai_trade_max_strategies):
            raise AiTradePreflightError(
                "AI_TRADE_STRATEGIES_INVALID",
                f"策略选择非法（需 1..{req.settings.ai_trade_max_strategies} 个可实例化策略）", 400)

    @staticmethod
    def _validate_context_rounds(req: AiTradeGroupStartRequest) -> None:
        lo = req.settings.ai_trade_min_context_rounds
        hi = req.settings.ai_trade_max_context_rounds
        if not (lo <= req.context_refresh_rounds <= hi):
            raise AiTradePreflightError(
                "AI_TRADE_CONTEXT_ROUNDS_INVALID", f"上下文刷新轮数须在 [{lo}, {hi}] 之间", 400)

    @staticmethod
    def _validate_sizing(req: AiTradeGroupStartRequest) -> None:
        """仓位层的两条启动期对账（vol-sizing [P1-6]/G-8）：越界即 422，绝不留到运行期静默空转。

        ① 敞口上限取值域 `{0} ∪ [1,10]`（下界保证单成员分组恒不受约束）；② 窗口装不下回看根数则波动率
        恒不可用、相关性恒 `ρ=1`，两个旗舰机制永久静默失效。窗口上限用真的 `MarketWindow` 探针算，绝不
        在此复制一份 `_window_max` 公式（复制即漂移）。
        """
        rule = parse_sizing_rule(req.settings)
        if rule is None or not req.members_in:      # 空分组归 `_validate_members` 的 400，不抢它的码
            return
        if 0 < rule.exposure_cap_mult < 1.0:
            raise AiTradePreflightError(
                "AI_TRADE_EXPOSURE_CAP_INVALID",
                "有效敞口上限倍数须为 0（关闭）或 ≥ 1.0（下界保证单成员分组恒不受约束）", 422)
        probe = MarketWindow(req.members_in[0].inst_id, req.bar, req.specs, req.priming_bars,
                             options=WindowOptions(
                                 context_enabled=req.settings
                                 .ai_trade_multi_timeframe_context))
        message = check_window_capacity(rule, probe.window_max)
        if message is not None:
            raise AiTradePreflightError("AI_TRADE_SIZING_WINDOW_TOO_SMALL", message, 422)

    @staticmethod
    def _resolve_budget_group(capital_usd: float | None, avail: float,
                              cap_frac: float, default_frac: float) -> float:
        """留空→ avail×default_frac（1%）；非空→ 夹到 ≤ avail×cap_frac（maxBudgetPct 与运营天花板共同主导）。

        分数由 `api/ai_auto.py` 预解析（cap_frac = min(maxBudgetPct/100, ai_trade_max_capital_frac,
        live_auto_group_max_capital_frac)）；成员预算再按 pct **切分**（budget_i=total×pct/100，非倍增），
        故开 N 个标的的 AI 交易总账户敞口仍 ≤ avail×cap_frac。
        """
        hard_cap = avail * cap_frac
        budget = capital_usd if (capital_usd and capital_usd > 0) else avail * default_frac
        return min(budget, hard_cap) if hard_cap > 0 else budget

    @staticmethod
    def _scale_threshold(total_amount: float | None, pct: float, sum_pct: float) -> float | None:
        """总止盈/止损按 pct/Σpct 归一分摊（Σpct>0 必成立），各会话阈值之和恒等于总额。"""
        if total_amount is None or sum_pct <= 0:
            return None
        return total_amount * pct / sum_pct

    @staticmethod
    def _resolve_trade_risk(req: AiTradeGroupStartRequest, settings: Settings) -> RiskParams:
        """E4：入参 sl/tp% 夹紧到 `ai_trade_max_sl_pct/tp_pct`，留空回落 config 默认；构造期 OCO。

        不改 `LiveExecutor` 任何代码（其构造本就收 `risk`），只把原写死的 config 默认变为
        「用户可在夹紧区间内设定」。各成员单仓 OCO pct 同值。

        `max_hold_bars`（holding-horizon [P1-5]）：默认仍取 config 常数（默认 0 = 禁用）；仅当交易
        周期契约显式 `enforceMaxHold=true` 且派生出了 `expected_hold_bars` 时改用后者。这一处是
        `enforceMaxHold` 的**唯一实现落点**——不接它，那个字段就是一个没有落点的死字段。开启后它与
        §2.3.3 的 `max_hold_bars` 豁免构成闭环：executor 在 `bars_in_position >= max_hold_bars` 时
        强平，本层在**同一判据**上整体解除，绝不出现「系统要强平、本层却拦着 Agent 先走一步」。
        本改动**只作用于 AI 链路**（`live_auto` 有自己的 `_resolve_*`，不经本函数）。
        """
        sl = (_clamp(req.stop_loss_pct, 0.0, settings.ai_trade_max_sl_pct)
              if req.stop_loss_pct is not None else settings.ai_trade_default_sl_pct)
        tp = (_clamp(req.take_profit_pct, 0.0, settings.ai_trade_max_tp_pct)
              if req.take_profit_pct is not None else settings.ai_trade_default_tp_pct)
        rule = req.horizon_rule
        max_hold = settings.ai_trade_default_max_hold_bars or None
        if rule is not None and rule.enforce_max_hold and rule.expected_hold_bars:
            max_hold = int(rule.expected_hold_bars)
        return RiskParams(stop_loss_pct=sl or None, take_profit_pct=tp or None,
                          max_hold_bars=max_hold)

    @staticmethod
    def _build_executor(creds: AccountCredentials, req: AiTradeGroupStartRequest, inst_id: str,
                        config: dict, budget: float, lever: float) -> LiveExecutor:
        """镜像 live-auto `_build_executor`，但 `risk` 由 E4 入参夹紧后覆盖（单仓 OCO 各成员同值）。"""
        meta = config.get("instMeta") or {}
        settings = req.settings
        risk = AiTradeManager._resolve_trade_risk(req, settings)
        executor = LiveExecutor(
            creds=creds, account_id=req.account_row.id, inst_id=inst_id,
            td_mode=settings.live_default_td_mode,
            pos_mode=config.get("posMode") or "long_short_mode",
            leverage=lever, budget_usd=budget, risk=risk,
            ct_val=meta.get("ctVal") or 0.0, lot_sz=meta.get("lotSz") or 0.0,
            min_sz=meta.get("minSz") or 0.0, settings=settings,
            # 成本记账的作用域缝（§3.2.0）：**只有** AI 链路注入账本；live_auto 不传 ⇒ 逐字节回退。
            fee_ledger=build_fee_ledger(settings, ct_val=meta.get("ctVal") or 0.0,
                                        fee_rates=config.get("feeRates")),
            # 成交时钟对齐（discipline-preset-and-integrity 轴 D / D-3）：同款「只有 AI 链路传」的
            # 作用域缝。锚点（相位）由会话在每根确认根落地时写入 `executor.bar_anchor_ts`——这里
            # 只给周期长度，绝不预设 UTC 相位（OKX 的 12H/1D 是港时开盘，[P0-1]）。
            bar_seconds=BAR_SECONDS[req.bar])
        # [P0-4] 启动期复核结论随执行器走：纪律条的 `volSizing` 行据此**在第一次跳过发生之前**就
        # 示警。只在回执里说一句是不够的——回执用户看一眼就走了，而那一行会一直挂在面板上。
        executor.dust_risk = AiTradeManager._dust_risky(req, config, budget, lever)
        return executor

    @staticmethod
    async def _create_group_run(ctx: _GroupBuildCtx, plan: _MemberPlan,
                                take_profit_usd: float | None,
                                stop_loss_usd: float | None) -> int:
        """每成员一条 `strategy_runs`（mode="ai-trade"，config 追加分组元数据；零 schema 迁移，P2-5）。"""
        req = ctx.req
        sm = get_sessionmaker()
        async with sm() as s:
            run = await repo.create_run(
                s, strategy_id=req.specs[0].strategy_id, inst_id=plan.inst_id, bar=req.bar,
                mode="ai-trade",
                config={"mode": "ai-trade",
                        "strategyIds": [sp.strategy_id for sp in req.specs],
                        "strategyNames": [sp.strategy_name for sp in req.specs],
                        "budgetUsd": plan.budget_usd, "leverage": ctx.lever,
                        "takeProfitUsd": take_profit_usd, "stopLossUsd": stop_loss_usd,
                        "contextRefreshRounds": req.context_refresh_rounds,
                        "groupId": ctx.group_id, "allocationPct": plan.allocation_pct,
                        "allocationMode": req.allocation_mode,
                        "totalBudgetUsd": ctx.total_budget_usd,
                        "positionStepPct": req.position_step_pct,   # P2 观测：档位落 free-form config（零列迁移）
                        # 墙钟定时唤醒周期（分钟；关闭态存 None，与 group_status/info 回执口径一致，RN-7）。
                        "wakeIntervalMinutes": (round(req.wake_interval_s / 60, 4)
                                                if req.wake_interval_s > 0 else None),
                        # 【基础策略】铁律配置随 config_json 落库（零列迁移，A-12；未装配存 None）。
                        "baseRules": rule_wire(req.base_rule),
                        # 利润保护配置随 config_json 落库（零列迁移；未装配存 None）。
                        "profitGuard": profit_guard_wire(req.profit_rule),
                        # 交易周期模式同款落库（零列迁移；未装配存 None）——历史详情接口直读 json 列，
                        # 故「这次会话到底选了哪种周期」事后可核对。
                        "tradingHorizon": horizon_wire(req.horizon_rule),
                        # 仓位/刹车层配置同款落库（零列迁移；三条规则全未装配存 None）。
                        "sizing": sizing_config_wire(req.settings, BAR_SECONDS[req.bar]),
                        # 交易纪律预设同款落库（零列迁移）：历史会话据此回答「当时开着哪些纪律、
                        # 服务端替我改了什么」——这正是后续用验收卡校准这组阈值的前提。
                        "discipline": {"preset": req.discipline_preset,
                                       "thresholds": req.discipline_thresholds,
                                       "adjustments": req.discipline_adjustments},
                        # 会话级静态口径标记（cost-truth §3.2 B-4，零迁移）：聚合层据此判断
                        # trades.pnl 是净额还是毛额；缺键的老 run 走 legacy 推定 + costBasisAssumed。
                        "costBasis": ("net" if req.settings.ai_trade_net_pnl_enabled
                                      else "gross"),
                        "feeRates": plan.fee_rates_wire},
                status="running",
                # ai-trade-stats-dashboard §3.2：填既有 group_* 列（零迁移），使 ai-trade run 可按组聚合
                # 出历史会话；各成员冗余同值。组级秒级起点为 ctx.created_ts（P1-3；ctx 无 started_ts）。
                group_id=ctx.group_id, group_started_ts=ctx.created_ts,
                group_total_budget_usd=ctx.total_budget_usd)
            return run.id


def paused_ref_from_state_row(row: dict) -> PausedGroupRef:
    """状态表行 → 冷占位（lifespan 水合的唯一装配点，pause-resume §3.7）。

    展示字段优先取**落地回执快照** `resolvedEcho`（那是上次启动真正生效的数），取不到才回落
    `startWire`（用户填的原始入参）——两者可能不同（预算会被夹紧），说错一个数比不说更坏。
    """
    echo = row.get("resolvedEcho") or {}
    wire = row.get("startWire") or {}
    return PausedGroupRef(
        group_id=row["groupId"], bar=str(row.get("bar") or echo.get("bar") or ""),
        account_id=int(row.get("accountId") or 0),
        total_budget_usd=float(echo.get("totalBudgetUsd") or wire.get("capitalUsd") or 0.0),
        leverage=float(echo.get("leverage") or wire.get("leverage") or 0.0),
        strategy_names=list(echo.get("strategyNames") or []),
        paused_ts=int(row.get("pausedTs") or 0), pause_reason=row.get("pauseReason"),
        resumed_count=int(row.get("resumedCount") or 0),
        created_ts=int(echo.get("createdTs") or row.get("createdTs") or 0),
        members=list(row.get("members") or []), start_wire=wire, resolved_echo=echo)


_manager: AiTradeManager | None = None


def get_ai_trade_manager() -> AiTradeManager:
    global _manager
    if _manager is None:
        _manager = AiTradeManager()
    return _manager
