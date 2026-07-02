"""AiTradeManager —— AI 自动交易「单活跃会话」注册表（ai-auto-trading §2.5）。

职责：启动前置门禁（安全关键，绝不假启动）、单活跃会话独占（cap=1）、与 live-auto 的**原子互斥**
（经共享 `AUTO_TRADE_ADMISSION_LOCK`，P1-4）、lifecycle（start/stop/status/get/shutdown）。真实下单
一律经 `LiveExecutor` → `okx/trading.py` 服务层，本层只做编排与守门。

与 live-auto 的互斥（安全关键）：二者共用同一实盘账户预算，绝不同时下单。AI 侧 `_require_capacity`
在共享准入锁内「检查无活跃 AI 会话 且 无活跃 live-auto 分组 + 注册自己」；live-auto 侧
`LiveAutoManager._require_capacity` 反向校验无活跃 AI 会话——两侧临界区共用同一把锁，check-and-register
原子化，杜绝并发 start 的 TOCTOU 双双通过。
"""
from __future__ import annotations

import logging
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
from ..okx.trading import fetch_live_positions, fetch_trading_config
from ..paper.strategy_factory import PaperStrategySpec
from .admission import get_admission_lock
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
    """已有活跃 AI 会话或活跃 live-auto 分组（单活跃独占 + 与 live-auto 互斥）。"""


def _creds(account_row: repo.OkxAccount) -> AccountCredentials:
    return AccountCredentials(api_key=account_row.api_key, secret_key=account_row.secret_key,
                             passphrase=account_row.passphrase)


def _clamp(value: float, low: float, high: float) -> float:
    """把 `value` 夹到 `[low, high]`（E4 单仓 pct 越界夹紧）。"""
    return low if value < low else high if value > high else value


@dataclass
class AiTradeStartRequest:
    """`start` 聚合形参（避免位置堆参，满足 CLAUDE.md ≤5 形参；镜像 live-auto `GroupStartRequest`）。"""

    specs: list[PaperStrategySpec]
    inst_id: str
    bar: str
    account_row: repo.OkxAccount
    settings: Settings
    context_refresh_rounds: int
    capital_usd: float | None = None
    leverage: float | None = None
    take_profit_usd: float | None = None
    stop_loss_usd: float | None = None
    stop_loss_pct: float | None = None       # E4：单仓交易所侧 OCO 止损%（留空→config 默认；越界服务端夹紧）
    take_profit_pct: float | None = None     # E4：单仓 OCO 止盈%（留空→config 默认）
    priming: bool = True
    priming_bars: int = 300


class AiTradeManager:
    """进程内单活跃 AI 会话注册表（真实资金，一次至多一个会话）。"""

    def __init__(self):
        self._session: AiTradeSession | None = None

    # ---------- 启动（前置门禁 → 建会话 → 起会话 / 回滚）----------
    async def start(self, req: AiTradeStartRequest) -> AiTradeSession:
        """§2.5：双闸 → Agent 已配置 → 原子互斥准入 → 标的/持仓/策略/轮数/预算门禁 → 建并启会话。"""
        self._require_enabled(req.settings)
        provider = await resolve_active_provider(get_sessionmaker(), req.settings)
        if provider is None:
            raise AiTradePreflightError(
                "AGENT_NOT_CONFIGURED", "未配置 AI 模型，无法决策（请在设置中心配置默认模型）", 400)
        self._validate_strategies(req)
        self._validate_context_rounds(req)
        async with get_admission_lock():
            self._require_capacity()
            creds = _creds(req.account_row)
            config = await fetch_trading_config(
                creds, req.account_row.id, req.inst_id, settings=req.settings)
            self._require_instrument(config)
            positions = await fetch_live_positions(creds)
            self._require_no_position(positions, req.inst_id)
            budget = await self._resolve_budget(req, config)
            lever = req.leverage or req.settings.live_default_leverage
            session = await self._build_session(req, creds, config, provider, budget, lever)
            self._session = session
        return await self._launch_or_rollback(session)

    async def _build_session(self, req: AiTradeStartRequest, creds: AccountCredentials,
                             config: dict, provider, budget: float,
                             lever: float) -> AiTradeSession:
        executor = self._build_executor(creds, req, config, budget, lever)
        decider = AiTradeDecider(
            make_llm_client_from_config(provider),
            build_ai_trade_system_prompt(req.inst_id, req.bar, budget, lever),
            context_refresh_rounds=req.context_refresh_rounds, settings=req.settings)
        run_id = await self._create_run(req, budget, lever)
        return AiTradeSession(
            f"aitrade_{uuid.uuid4().hex}", run_id, req.specs, req.inst_id, req.bar, executor,
            decider, take_profit_usd=req.take_profit_usd, stop_loss_usd=req.stop_loss_usd,
            priming=req.priming, priming_bars=req.priming_bars,
            context_refresh_rounds=req.context_refresh_rounds)

    async def _launch_or_rollback(self, session: AiTradeSession) -> AiTradeSession:
        """起会话；失败 → 停会话（幂等，落 Run 终态 + 清 WS/monitor）+ 清注册，不掩盖原异常。"""
        try:
            await session.start()
        except Exception:
            try:
                await session.stop("start_failed")
            except Exception:  # noqa: BLE001 —— 回滚清理失败不掩盖原始启动异常
                pass
            self._session = None
            raise
        return session

    # ---------- 停止 / 只读 ----------
    async def stop(self, session_id: str, reason: str = "user_stop") -> tuple | None:
        """按 session_id 停会话；平自管仓 + 落 Run 终态（刻意不守门，确保随时可停）。"""
        session = self._session
        if session is None or session.session_id != session_id:
            return None
        flat = await session.stop(reason)
        if not flat.get("ok"):
            logger.warning("ai-trade stop flatten failed: session=%s code=%s residual=%s",
                           session.session_id, flat.get("code"), flat.get("residual"))
        return session, flat

    def status(self) -> dict | None:
        """当前活跃 AI 会话只读视图（无活跃则 None，供互斥反向校验与前端重发现）。"""
        session = self._session
        if session is None or session.state != "running":
            return None
        return session.info()

    def get(self, session_id: str) -> AiTradeSession | None:
        session = self._session
        return session if (session is not None and session.session_id == session_id) else None

    async def shutdown(self) -> None:
        session = self._session
        if session is not None and session.state == "running":
            flat = await session.stop("shutdown")
            if not flat.get("ok"):
                logger.warning("ai-trade shutdown flatten failed: %s", flat.get("code"))
        self._session = None

    # ---------- 前置门禁辅助 ----------
    @staticmethod
    def _require_enabled(settings: Settings) -> None:
        if not (settings.ai_trade_enabled and settings.live_trading_enabled):
            raise AiTradePreflightError(
                "AI_TRADE_DISABLED",
                "AI 自动交易未启用（需 AI_TRADE_ENABLED 与 LIVE_TRADING_ENABLED 均开启）", 403)

    def _require_capacity(self) -> None:
        """单活跃独占 + 与 live-auto 互斥（须在共享准入锁内调用，P1-4）。延迟导入避免循环依赖。"""
        if self._session is not None and self._session.state == "running":
            raise AiTradeSessionLimitError(
                "AI_TRADE_SESSION_LIMIT", "已有活跃 AI 会话（单活跃独占，请先停止）", 409)
        from ..live_auto.manager import get_live_auto_manager
        if get_live_auto_manager().group_status() is not None:
            raise AiTradeSessionLimitError(
                "AI_TRADE_SESSION_LIMIT", "已有活跃策略自动交易分组（与 AI 互斥，请先停止）", 409)

    def _validate_strategies(self, req: AiTradeStartRequest) -> None:
        n = len(req.specs)
        if not (1 <= n <= req.settings.ai_trade_max_strategies):
            raise AiTradePreflightError(
                "AI_TRADE_STRATEGIES_INVALID",
                f"策略选择非法（需 1..{req.settings.ai_trade_max_strategies} 个可实例化策略）", 400)

    @staticmethod
    def _validate_context_rounds(req: AiTradeStartRequest) -> None:
        lo = req.settings.ai_trade_min_context_rounds
        hi = req.settings.ai_trade_max_context_rounds
        if not (lo <= req.context_refresh_rounds <= hi):
            raise AiTradePreflightError(
                "AI_TRADE_CONTEXT_ROUNDS_INVALID", f"上下文刷新轮数须在 [{lo}, {hi}] 之间", 400)

    @staticmethod
    def _require_instrument(config: dict) -> None:
        if not config.get("instrumentSupported"):
            raise AiTradePreflightError(
                "LIVE_INSTRUMENT_UNSUPPORTED", "仅支持 USDT 本位永续 / 交割合约自动交易", 400)

    @staticmethod
    def _require_no_position(positions: list[dict], inst_id: str) -> None:
        if any(str(p.get("instId")) == inst_id and abs(p.get("pos") or 0.0) > 0
               for p in positions):
            raise AiTradeSessionLimitError(
                "AI_TRADE_PREEXISTING_POSITION",
                "该标的已有持仓，AI 自动交易拒绝启动（请先平仓或换标的）", 409)

    @staticmethod
    async def _resolve_budget(req: AiTradeStartRequest, config: dict) -> float:
        """预算硬上限夹紧：留空→ avail×default_frac；非空→ 夹到 ≤ avail×min(maxBudgetPct/100, max_frac)。"""
        avail = config.get("availUsdt") or 0.0
        settings = req.settings
        async with get_sessionmaker()() as s:
            at = await repo.get_auto_trade_settings(s)
        cap_frac = min(at["maxBudgetPct"] / 100.0, settings.ai_trade_max_capital_frac)
        default = avail * settings.ai_trade_default_capital_frac
        budget = req.capital_usd if (req.capital_usd and req.capital_usd > 0) else default
        hard_cap = avail * cap_frac
        return min(budget, hard_cap) if hard_cap > 0 else budget

    @staticmethod
    def _resolve_trade_risk(req: AiTradeStartRequest, settings: Settings) -> RiskParams:
        """E4：入参 sl/tp% 夹紧到 `ai_trade_max_sl_pct/tp_pct`，留空回落 config 默认；构造期 OCO。

        不改 `LiveExecutor` 任何代码（其构造本就收 `risk`），只把原写死的 config 默认变为
        「用户可在夹紧区间内设定」；`max_hold_bars` 仍取 config 默认。
        """
        sl = (_clamp(req.stop_loss_pct, 0.0, settings.ai_trade_max_sl_pct)
              if req.stop_loss_pct is not None else settings.ai_trade_default_sl_pct)
        tp = (_clamp(req.take_profit_pct, 0.0, settings.ai_trade_max_tp_pct)
              if req.take_profit_pct is not None else settings.ai_trade_default_tp_pct)
        return RiskParams(stop_loss_pct=sl or None, take_profit_pct=tp or None,
                          max_hold_bars=settings.ai_trade_default_max_hold_bars or None)

    @staticmethod
    def _build_executor(creds: AccountCredentials, req: AiTradeStartRequest, config: dict,
                        budget: float, lever: float) -> LiveExecutor:
        """镜像 live-auto `_build_executor`，但 `risk` 由 E4 入参夹紧后覆盖（与单策略解耦，§2.6/P2-2）。"""
        meta = config.get("instMeta") or {}
        settings = req.settings
        risk = AiTradeManager._resolve_trade_risk(req, settings)
        return LiveExecutor(
            creds=creds, account_id=req.account_row.id, inst_id=req.inst_id,
            td_mode=settings.live_default_td_mode,
            pos_mode=config.get("posMode") or "long_short_mode",
            leverage=lever, budget_usd=budget, risk=risk,
            ct_val=meta.get("ctVal") or 0.0, lot_sz=meta.get("lotSz") or 0.0,
            min_sz=meta.get("minSz") or 0.0, settings=settings)

    @staticmethod
    async def _create_run(req: AiTradeStartRequest, budget: float, lever: float) -> int:
        sm = get_sessionmaker()
        async with sm() as s:
            run = await repo.create_run(
                s, strategy_id=req.specs[0].strategy_id, inst_id=req.inst_id, bar=req.bar,
                mode="ai-trade",
                config={"mode": "ai-trade",
                        "strategyIds": [sp.strategy_id for sp in req.specs],
                        "strategyNames": [sp.strategy_name for sp in req.specs],
                        "budgetUsd": budget, "leverage": lever,
                        "takeProfitUsd": req.take_profit_usd, "stopLossUsd": req.stop_loss_usd,
                        "contextRefreshRounds": req.context_refresh_rounds},
                status="running")
            return run.id


_manager: AiTradeManager | None = None


def get_ai_trade_manager() -> AiTradeManager:
    global _manager
    if _manager is None:
        _manager = AiTradeManager()
    return _manager
