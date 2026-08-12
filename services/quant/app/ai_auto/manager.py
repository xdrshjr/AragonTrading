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

from ..agent.llm.factory import make_llm_client_from_config
from ..agent.providers import resolve_active_provider
from ..config import Settings
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
from .market_window import MarketWindow, WindowOptions
from .profit_guard import ProfitGuardRule
from .profit_guard_text import format_profit_guard_charter
from .profit_guard import rule_wire as profit_guard_wire
from .decider import AiTradeDecider
from .prompts import build_ai_trade_system_prompt
from .session import AiTradeSession
from .sizing import SizingRule, StopRule, check_window_capacity, parse_sizing_rule, parse_stop_rule
from .sizing_runtime import build_sizing_runtime, sizing_config_wire
from .sizing_text import format_sizing_charter
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
            self._require_capacity()
            members = self._validate_members(req.members_in, req.settings, req.allocation_mode)
            creds = _creds(req.account_row)
            avail, positions, configs, pending = await self._gather_context(creds, req, members)
            total = self._resolve_budget_group(
                req.total_capital_usd, avail, req.budget_cap_frac, req.default_budget_frac)
            lever = req.leverage or req.settings.live_default_leverage
            plans = self._preflight_group(members, configs, positions, total, lever, pending)
            self._note_dust_risk(req, plans, lever)   # [P0-4]：如实回执，绝不 422
            group = await self._build_group(req, creds, provider, plans, total, lever)
            self._group = group
            return await self._launch_group_or_rollback(group)

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
        created_ts = int(time.time())
        # 敞口簿是**分组级**共享状态（每成员一个 runtime，共用一本簿）；`unit` 取平均单标的满仓名义额。
        rule = parse_sizing_rule(req.settings)
        book = (GroupExposureBook(unit_notional=total * lever / max(1, len(plans)),
                                  cap_mult=rule.exposure_cap_mult, bar_seconds=BAR_SECONDS[req.bar])
                if rule is not None and rule.exposure_cap_mult > 0 else None)
        ctx = _GroupBuildCtx(
            req=req, creds=creds, provider=provider, lever=lever,
            sum_pct=sum(p.allocation_pct for p in plans), total_budget_usd=total,
            group_id=f"aitradegrp_{uuid.uuid4().hex}", created_ts=created_ts, book=book)
        members = [await self._build_member(ctx, p) for p in plans]
        return AiTradeGroup(
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
            discipline_thresholds=req.discipline_thresholds)

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
        decider = AiTradeDecider(
            make_llm_client_from_config(ctx.provider),
            build_ai_trade_system_prompt(plan.inst_id, req.bar, plan.budget_usd, ctx.lever,
                                         position_step_pct=req.position_step_pct,
                                         allow_limit=allow_limit,
                                         # F-3：价带文案与本地门禁同源（settings 唯一真源）
                                         limit_band_pct=req.settings
                                         .ai_trade_limit_price_max_deviation_pct,
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
                                                               req.wake_interval_s))),
            context_refresh_rounds=req.context_refresh_rounds, settings=req.settings,
            position_step_pct=req.position_step_pct,
            reflection_enabled=req.settings.ai_trade_reflection_enabled,   # 生产实盘显式开（默认 True）
            allow_limit=allow_limit,
            # C1 §2.4：承诺退出线总闸经既有 profit_rule 通路派生（装配保护层且承诺开启时露出 holdExitPx）。
            allow_hold_exit=(req.profit_rule is not None and req.profit_rule.commit_enabled))
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
            discipline_preset=req.discipline_preset)
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
            raise
        return group

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
        return {"groupId": group_id, "stopReason": reason, "members": rows}

    async def stop_member(self, group_id: str, inst_id: str, reason: str = "user_stop") -> dict | None:
        """仅停某成员（其余续跑）；停后无运行成员则清组（保证 group_status() 不误报僵尸分组）。"""
        group = self._group
        if group is None or group.group_id != group_id:
            return None
        member = next((m for m in group.members if m.inst_id == inst_id), None)
        if member is None:
            return None
        flat = await self._stop_session(member.session, reason)
        if not any(m.session.state == "running" for m in group.members):
            self._group = None
        return {"groupId": group_id, "instId": inst_id, "flattened": flat}

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

    async def _stop_session(self, session: AiTradeSession, reason: str) -> dict:
        flat = await session.stop(reason)
        if not flat.get("ok"):   # 平仓失败不静默——日志留痕（API 仍回 flattened 供 UI 强提示）
            logger.warning("ai-trade group stop flatten failed: session=%s code=%s residual=%s",
                           session.session_id, flat.get("code"), flat.get("residual"))
        return flat

    # ---------- 只读视图 / WS 路由 ----------
    def status(self) -> dict | None:
        """当前活跃 AI 分组只读视图（= group_status()）。

        **关键**：这样 live_auto 侧 `_require_capacity` 的 `get_ai_trade_manager().status() is not None`
        无需任何改动即可正确检测活跃 AI 分组（live_auto 零改动）。互斥正确性前提：各停止路径
        （stop_group/stop_member/shutdown）在无运行成员时清 `self._group`，使 `status() is not None`
        ⟺ 存在活跃成员（`AiTradeSession` 仅经 stop() 离开 "running"、不自终止）。
        """
        return self.group_status()

    def group_status(self) -> dict | None:
        """活跃分组只读视图（前端重载后据此重连各成员 WS）；无活跃分组→ None。"""
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
                "createdTs": group.created_ts,
                # 验收闭环三键的**分组合并口径**（horizon-acceptance [P0-3] / [P1-9]）：合并的是
                # 各成员的原始计数器而不是已判定的卡——比例类指标不可平均。本端点由前端 15s 轮询
                # 消费，是分组口径唯一的实时来源（成员级实时数据仍走既有成员 WS status）。
                **self._acceptance_bundle(group),
                "members": [self._member_row(m) for m in group.members]}

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

    @staticmethod
    def _member_row(member: AiTradeGroupMember) -> dict:
        """成员行 = 会话 `info()` 全字段 + 分配占比（复用不变的 AiTradeSession.info()）。"""
        return {**member.session.info(), "allocationPct": member.allocation_pct}

    def get(self, session_id: str) -> AiTradeSession | None:
        """跨所有成员按 session_id 查会话（WS 路由用）。"""
        group = self._group
        if group is None:
            return None
        return next((m.session for m in group.members if m.session.session_id == session_id), None)

    async def shutdown(self) -> None:
        """停所有成员会话（进程退出场景，沿用不静默吞平仓失败）。"""
        group = self._group
        if group is not None:
            for member in group.members:
                if member.session.state == "running":
                    await self._stop_session(member.session, "shutdown")
        self._group = None

    # ---------- 前置门禁辅助 ----------
    @staticmethod
    def _require_enabled(settings: Settings) -> None:
        if not (settings.ai_trade_enabled and settings.live_trading_enabled):
            raise AiTradePreflightError(
                "AI_TRADE_DISABLED",
                "AI 自动交易未启用（需 AI_TRADE_ENABLED 与 LIVE_TRADING_ENABLED 均开启）", 403)

    def _require_capacity(self) -> None:
        """单活跃分组独占 + 与 live-auto 互斥（须在共享准入锁内调用，P1-4）。延迟导入避免循环依赖。"""
        group = self._group
        if group is not None and any(m.session.state == "running" for m in group.members):
            raise AiTradeSessionLimitError(
                "AI_TRADE_SESSION_LIMIT", "已有活跃 AI 自动交易分组（单活跃独占，请先停止）", 409)
        from ..live_auto.manager import get_live_auto_manager
        if get_live_auto_manager().group_status() is not None:
            raise AiTradeSessionLimitError(
                "AI_TRADE_SESSION_LIMIT", "已有活跃策略自动交易分组（与 AI 互斥，请先停止）", 409)

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


_manager: AiTradeManager | None = None


def get_ai_trade_manager() -> AiTradeManager:
    global _manager
    if _manager is None:
        _manager = AiTradeManager()
    return _manager
