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
from dataclasses import dataclass

from ..agent.llm.factory import make_llm_client_from_config
from ..agent.providers import resolve_active_provider
from ..config import Settings
from ..db import repositories as repo
from ..db.session import get_sessionmaker
from ..engine.simulator import RiskParams
from ..live_auto.executor import LiveExecutor
from ..okx.accounts import AccountCredentials
from ..okx.order_builder import snap_size
from ..okx.trading import fetch_live_open_orders, fetch_live_positions, fetch_trading_config
from ..paper.strategy_factory import PaperStrategySpec
from .admission import get_admission_lock
from .base_rules import MaBandRule, format_charter, rule_wire
from .profit_guard import ProfitGuardRule, format_profit_guard_charter
from .profit_guard import rule_wire as profit_guard_wire
from .decider import AiTradeDecider
from .prompts import build_ai_trade_system_prompt
from .session import AiTradeSession

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
    # 解析，且**只挂实盘启动**——回测显式非目标，ai-trade-profit-protection §1.3/P2-4）。
    profit_rule: ProfitGuardRule | None = None


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


@dataclass
class _MemberPlan:
    """通过第一阶段门禁的单成员落地参数（待第二阶段建会话）。"""

    inst_id: str
    allocation_pct: float
    budget_usd: float
    config: dict


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
        async with get_admission_lock(), self._start_lock:
            self._require_capacity()
            members = self._validate_members(req.members_in, req.settings, req.allocation_mode)
            creds = _creds(req.account_row)
            avail, positions, configs, pending = await self._gather_context(creds, req, members)
            total = self._resolve_budget_group(
                req.total_capital_usd, avail, req.budget_cap_frac, req.default_budget_frac)
            lever = req.leverage or req.settings.live_default_leverage
            plans = self._preflight_group(members, configs, positions, total, lever, pending)
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

        configs = await asyncio.gather(*[_bounded_config(m.inst_id) for m in members])
        async with sem:
            positions = await fetch_live_positions(creds)
        async with sem:                       # 复用同一并发预算（规避已知的启动 429）
            pending = await self._fetch_blocking_pending(creds)
        config_map = {m.inst_id: cfg for m, cfg in zip(members, configs)}
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
                plans.append(_MemberPlan(m.inst_id, m.allocation_pct, budget_i, cfg))
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
        ctx = _GroupBuildCtx(
            req=req, creds=creds, provider=provider, lever=lever,
            sum_pct=sum(p.allocation_pct for p in plans), total_budget_usd=total,
            group_id=f"aitradegrp_{uuid.uuid4().hex}", created_ts=created_ts)
        members = [await self._build_member(ctx, p) for p in plans]
        return AiTradeGroup(
            group_id=ctx.group_id, bar=req.bar,
            strategy_ids=[s.strategy_id for s in req.specs],
            strategy_names=[s.strategy_name for s in req.specs],
            total_budget_usd=total, leverage=lever,
            context_refresh_rounds=req.context_refresh_rounds,
            created_ts=created_ts, members=members,
            wake_interval_s=req.wake_interval_s, base_rule=req.base_rule,
            profit_rule=req.profit_rule)

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
                                             req.profit_rule)),
            context_refresh_rounds=req.context_refresh_rounds, settings=req.settings,
            position_step_pct=req.position_step_pct,
            reflection_enabled=req.settings.ai_trade_reflection_enabled,   # 生产实盘显式开（默认 True）
            allow_limit=allow_limit)
        run_id = await self._create_group_run(ctx, plan, tp_i, sl_i)
        session = AiTradeSession(
            f"aitrade_{uuid.uuid4().hex}", run_id, req.specs, plan.inst_id, req.bar, executor,
            decider, take_profit_usd=tp_i, stop_loss_usd=sl_i,
            priming=req.priming, priming_bars=req.priming_bars,
            context_refresh_rounds=req.context_refresh_rounds,
            wake_interval_s=req.wake_interval_s, base_rule=req.base_rule,
            profit_rule=req.profit_rule)
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
                "createdTs": group.created_ts,
                "members": [self._member_row(m) for m in group.members]}

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
        「用户可在夹紧区间内设定」；`max_hold_bars` 仍取 config 默认。各成员单仓 OCO pct 同值。
        """
        sl = (_clamp(req.stop_loss_pct, 0.0, settings.ai_trade_max_sl_pct)
              if req.stop_loss_pct is not None else settings.ai_trade_default_sl_pct)
        tp = (_clamp(req.take_profit_pct, 0.0, settings.ai_trade_max_tp_pct)
              if req.take_profit_pct is not None else settings.ai_trade_default_tp_pct)
        return RiskParams(stop_loss_pct=sl or None, take_profit_pct=tp or None,
                          max_hold_bars=settings.ai_trade_default_max_hold_bars or None)

    @staticmethod
    def _build_executor(creds: AccountCredentials, req: AiTradeGroupStartRequest, inst_id: str,
                        config: dict, budget: float, lever: float) -> LiveExecutor:
        """镜像 live-auto `_build_executor`，但 `risk` 由 E4 入参夹紧后覆盖（单仓 OCO 各成员同值）。"""
        meta = config.get("instMeta") or {}
        settings = req.settings
        risk = AiTradeManager._resolve_trade_risk(req, settings)
        return LiveExecutor(
            creds=creds, account_id=req.account_row.id, inst_id=inst_id,
            td_mode=settings.live_default_td_mode,
            pos_mode=config.get("posMode") or "long_short_mode",
            leverage=lever, budget_usd=budget, risk=risk,
            ct_val=meta.get("ctVal") or 0.0, lot_sz=meta.get("lotSz") or 0.0,
            min_sz=meta.get("minSz") or 0.0, settings=settings)

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
                        "profitGuard": profit_guard_wire(req.profit_rule)},
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
