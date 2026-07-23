"""AiBacktestManager —— AI 回测流式作业注册表（ai-auto-trading-backtest §2.6/§2.9）。

进程内单例（`get_ai_backtest_manager()`），持 `jobs` 注册表 + 并发预留闸（`ai_backtest_max_jobs`，计数式，
镜像 `WeightTrainManager` 的 `reserve()`，非 `Semaphore`）+ start/get/cancel/shutdown 生命周期。`start` 内做
§2.9 前置门禁（**显著少于**实盘：仅 Agent 已配置 / 策略合法 / 区间合法 / 轮数合法 / 计划决策轮数≤上限 /
并发未满——**不校验** AI/LIVE 双闸、就绪账户、live-auto 互斥、无既有持仓门禁，因回测不下真单），造 LLMClient +
runner + `asyncio.create_task(runner.run())`。真实资金护栏一律不适用，也一律不触碰。
"""
from __future__ import annotations

import asyncio
import logging
import math
import time
import uuid
from dataclasses import dataclass

from ..agent.providers import resolve_active_provider
from ..config import Settings, get_settings
from ..db.session import get_sessionmaker
from ..engine.mtf import bar_to_seconds
from ..okx.rest_client import get_rest_client
from ..paper.strategy_factory import PaperStrategySpec
from .backtest import AiBacktestRunner, AiBacktestSpec, _BacktestJob
from .base_rules import MaBandRule
from .profit_guard import ProfitGuardRule

logger = logging.getLogger(__name__)

_SHUTDOWN_JOIN_TIMEOUT = 5.0    # 关停 join 单作业超时，超时则 force_cancel 硬取消兜底（镜像 weight-train H-B4）


class AiBacktestError(Exception):
    """回测前置门禁不通过（携稳定语义码与 HTTP 状态，API 层据此回结构化 `{error:{code,message}}`）。"""

    def __init__(self, code: str, message: str, status: int = 400):
        self.code = code
        self.message = message
        self.status = status
        super().__init__(message)


def _clamp(value: float, low: float, high: float) -> float:
    """把 `value` 夹到 `[low, high]`（单仓 pct 越界夹紧，与 AiTradeManager._clamp 同义）。"""
    return low if value < low else high if value > high else value


@dataclass
class AiBacktestStartRequest:
    """`start` 聚合形参（避免位置堆参，满足 CLAUDE.md ≤5 形参；镜像 AiTradeStartRequest）。"""

    specs: list[PaperStrategySpec]
    inst_id: str
    bar: str
    start_ts: int
    end_ts: int
    settings: Settings
    context_refresh_rounds: int
    decision_every_bars: int | None = None
    initial_capital: float | None = None
    leverage: float | None = None
    stop_loss_pct: float | None = None
    take_profit_pct: float | None = None
    take_profit_usd: float | None = None
    stop_loss_usd: float | None = None
    min_consensus: int | None = None
    position_step_pct: float = 0.0     # 仓位操作档位步长（fraction；0=连续，直接构造该 dataclass 的既有测试零回归）
    # 【基础策略】MA 通道开单铁律（默认 None=不装配，直接构造该 dataclass 的既有测试零回归）；
    # 「缺省即开启」由 API 层 `_resolve_base_rules` 单点解析，与实盘同一函数、同一默认（P1-4/P1-5）。
    base_rule: MaBandRule | None = None
    # 利润保护层（adaptive §3.1/P1-1）：同样默认 None=不装配。**不透传就是整条断路**——API 层解析出的
    # 规则到不了 runner，F3 全部代码都在但永不生效（跑不出错、功能静默缺失）。
    profit_rule: ProfitGuardRule | None = None


class AiBacktestManager:
    """进程内 AI 回测作业注册表（并发受 `ai_backtest_max_jobs` 限，lifespan 关停）。"""

    def __init__(self):
        self._jobs: dict[str, _BacktestJob] = {}
        self._reserved = 0                   # 同步预占名额（覆盖守门→注册之间的窗口，镜像 weight-train H-B3）

    # ---------- 启动（前置门禁 → 造 runner → 注册 / 回滚）----------
    async def start(self, req: AiBacktestStartRequest) -> _BacktestJob:
        """§2.9：Agent 已配置 → 策略/区间/轮数/计划轮数合法 → 并发未满 → 造 runner 起 job。"""
        provider = await resolve_active_provider(get_sessionmaker(), req.settings)
        if provider is None:
            raise AiBacktestError(
                "AGENT_NOT_CONFIGURED", "未配置 AI 模型，无法回测决策（请在设置中心配置默认模型）", 400)
        self._validate(req)
        planned = self._planned_rounds(req)
        limit = req.settings.ai_backtest_max_rounds
        if planned > limit:
            raise AiBacktestError(
                "AI_BACKTEST_TOO_MANY_ROUNDS",
                f"计划决策轮数 {planned} 超上限 {limit}，请缩短区间或增大决策间隔", 400)
        if not self._reserve():
            raise AiBacktestError(
                "AI_BACKTEST_BUSY", "同时进行的 AI 回测数已达上限，请稍后或取消其它回测", 429)
        return self._launch(req, provider, planned)

    def _launch(self, req: AiBacktestStartRequest, provider, planned: int) -> _BacktestJob:
        """造 spec/runner/job 并注册启动（无 await，reserve→register 原子）；异常回滚名额与注册。"""
        job: _BacktestJob | None = None
        try:
            spec = self._build_spec(req, provider, planned)
            job = _BacktestJob(f"aibt_{uuid.uuid4().hex}", AiBacktestRunner(spec))
            self._jobs[job.job_id] = job
            self._release()                  # 预占转为在跑计数（job 非终态即被 _active_count 计入）
            job.start()
        except Exception:
            if job is not None:
                self._jobs.pop(job.job_id, None)
            self._release()
            raise
        return job

    def _build_spec(self, req: AiBacktestStartRequest, provider, planned: int) -> AiBacktestSpec:
        settings = req.settings
        every = req.decision_every_bars or settings.ai_backtest_default_decision_every_bars
        sl = (_clamp(req.stop_loss_pct, 0.0, settings.ai_trade_max_sl_pct)
              if req.stop_loss_pct is not None else settings.ai_trade_default_sl_pct)
        tp = (_clamp(req.take_profit_pct, 0.0, settings.ai_trade_max_tp_pct)
              if req.take_profit_pct is not None else settings.ai_trade_default_tp_pct)
        return AiBacktestSpec(
            inst_id=req.inst_id, bar=req.bar,
            strategy_ids=[s.strategy_id for s in req.specs],
            strategy_names=[s.strategy_name for s in req.specs], specs=req.specs,
            start_ts=req.start_ts, end_ts=req.end_ts, decision_every_bars=every,
            initial_capital=req.initial_capital or settings.initial_capital,
            leverage=req.leverage or settings.default_leverage,
            stop_loss_pct=sl or None, take_profit_pct=tp or None,
            take_profit_usd=req.take_profit_usd, stop_loss_usd=req.stop_loss_usd,
            context_refresh_rounds=req.context_refresh_rounds,
            min_consensus=(req.min_consensus if req.min_consensus is not None
                           else settings.ai_trade_min_consensus),
            planned_rounds=planned, settings=settings, rest=get_rest_client(), provider=provider,
            position_step_pct=req.position_step_pct,
            reflection_enabled=settings.ai_trade_reflection_enabled,   # RV-1：生产回测显式开（默认 True）
            base_rule=req.base_rule,                                   # 【基础策略】与实盘同源（P1-4）
            profit_rule=req.profit_rule)                               # 利润保护层与实盘同源（F3）

    # ---------- 前置门禁辅助 ----------
    @staticmethod
    def _validate(req: AiBacktestStartRequest) -> None:
        settings = req.settings
        n = len(req.specs)
        if not (1 <= n <= settings.ai_trade_max_strategies):
            raise AiBacktestError(
                "AI_TRADE_STRATEGIES_INVALID",
                f"策略选择非法（需 1..{settings.ai_trade_max_strategies} 个可实例化策略）", 400)
        lo, hi = settings.ai_trade_min_context_rounds, settings.ai_trade_max_context_rounds
        if not (lo <= req.context_refresh_rounds <= hi):
            raise AiBacktestError(
                "AI_TRADE_CONTEXT_ROUNDS_INVALID", f"上下文刷新轮数须在 [{lo}, {hi}] 之间", 400)
        if req.start_ts >= req.end_ts or req.start_ts > int(time.time()):
            raise AiBacktestError(
                "AI_BACKTEST_RANGE_INVALID", "回测区间非法（起止顺序错误或起点在未来）", 400)

    @staticmethod
    def _planned_rounds(req: AiBacktestStartRequest) -> int:
        """归一估算式（与 backtest §2.5/§2.9 同式）：ceil(tradableBars / decisionEveryBars)。"""
        bar_sec = bar_to_seconds(req.bar)
        every = req.decision_every_bars or req.settings.ai_backtest_default_decision_every_bars
        tradable = max(1, (req.end_ts - req.start_ts) // bar_sec + 1)
        return math.ceil(tradable / every)

    # ---------- 名额闸（计数式，动态读 settings；镜像 weight-train reserve/release）----------
    def _active_count(self) -> int:
        return sum(1 for job in self._jobs.values() if not job.is_terminal)

    def _reserve(self) -> bool:
        cap = get_settings().ai_backtest_max_jobs
        if self._active_count() + self._reserved >= cap:
            return False
        self._reserved += 1
        return True

    def _release(self) -> None:
        self._reserved = max(0, self._reserved - 1)

    # ---------- 只读 / 取消 / 关停 ----------
    def get(self, job_id: str) -> _BacktestJob | None:
        return self._jobs.get(job_id)

    def cancel(self, job_id: str) -> str:
        """协作式取消；返回 'cancelling'（已置标志）| 'finished'（已终态）| 'unknown'。"""
        job = self._jobs.get(job_id)
        if job is None:
            return "unknown"
        if job.is_terminal:
            return "finished"
        job.request_cancel()
        return "cancelling"

    async def shutdown(self) -> None:
        """关停：先把所有作业一次性置取消标志 → 带超时 join，超时则 force_cancel 兜底（镜像 weight-train）。"""
        jobs = list(self._jobs.values())
        for job in jobs:
            job.request_cancel()
        for job in jobs:
            try:
                await asyncio.wait_for(job.join(), timeout=_SHUTDOWN_JOIN_TIMEOUT)
            except asyncio.TimeoutError:
                job.force_cancel()
                await job.join()
        self._jobs.clear()


_manager: AiBacktestManager | None = None


def get_ai_backtest_manager() -> AiBacktestManager:
    global _manager
    if _manager is None:
        _manager = AiBacktestManager()
    return _manager
