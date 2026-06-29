"""LiveAutoManager —— 实时自动交易单会话独占注册表（live-auto-trading §2.4 / §2.6）。

职责：启动前置门禁（安全关键，绝不假启动）、单会话独占（cap=1）、lifecycle（start/stop/get/
status/shutdown）。真实下单一律经 `LiveExecutor` → `okx/trading.py` 服务层，本层只做编排与守门。
"""
from __future__ import annotations

import asyncio
import logging
import uuid

from ..config import Settings
from ..db import repositories as repo
from ..db.session import get_sessionmaker
from ..okx.accounts import AccountCredentials
from ..okx.trading import fetch_live_positions, fetch_trading_config
from .executor import LiveExecutor
from .session import LiveAutoSession

logger = logging.getLogger(__name__)


class LiveAutoPreflightError(Exception):
    """启动前置门禁不通过（携稳定语义码与 HTTP 状态，API 层据此回结构化错误）。"""

    def __init__(self, code: str, message: str, status: int = 400):
        self.code = code
        self.message = message
        self.status = status
        super().__init__(message)


class LiveAutoSessionLimitError(LiveAutoPreflightError):
    """已有活跃自动会话（单会话独占）。"""


def _creds(account_row: repo.OkxAccount) -> AccountCredentials:
    return AccountCredentials(api_key=account_row.api_key, secret_key=account_row.secret_key,
                              passphrase=account_row.passphrase)


class LiveAutoManager:
    """进程内单会话注册表（真实资金，绝不并发多策略自动下单）。"""

    def __init__(self):
        self._session: LiveAutoSession | None = None
        # H5：start 关键区互斥锁，消除「容量检查 → self._session=session」之间的 TOCTOU（cap=1）。
        self._start_lock = asyncio.Lock()

    async def start(self, *, strategy_spec, inst_id: str, bar: str,
                    account_row: repo.OkxAccount, settings: Settings,
                    capital_usd: float | None = None, leverage: float | None = None,
                    take_profit_usd: float | None = None, stop_loss_usd: float | None = None,
                    priming: bool = True, priming_bars: int = 300) -> LiveAutoSession:
        """§2.4 顺序前置门禁 → 建 Run → 起会话；任一门禁不过即拒绝并回结构化错误。

        H5：`_require_capacity → self._session=session → session.start()` 整段在 `_start_lock`
        内执行，使并发 `POST /start` 严格串行，杜绝两路同时通过容量检查短暂违反 cap=1。
        """
        self._require_enabled(settings)
        async with self._start_lock:
            self._require_capacity()
            creds = _creds(account_row)
            config = await fetch_trading_config(creds, account_row.id, inst_id, settings=settings)
            if not config.get("instrumentSupported"):
                raise LiveAutoPreflightError(
                    "LIVE_INSTRUMENT_UNSUPPORTED", "仅支持 USDT 本位永续 / 交割合约自动交易", 400)
            if self._has_position(await fetch_live_positions(creds), inst_id):
                raise LiveAutoPreflightError(
                    "LIVE_AUTO_PREEXISTING_POSITION",
                    "该标的已有持仓，自动交易拒绝启动（守『不动现有持仓』；请先平仓或换标的）", 409)
            budget = self._resolve_budget(capital_usd, config.get("availUsdt") or 0.0, settings)
            lever = leverage or settings.live_default_leverage
            executor = self._build_executor(creds, account_row.id, inst_id, config, budget,
                                            lever, strategy_spec.strategy.risk_params(), settings)
            run_id = await self._create_run(strategy_spec, inst_id, bar, budget, lever,
                                            take_profit_usd, stop_loss_usd)
            session = LiveAutoSession(
                f"liveauto_{uuid.uuid4().hex}", run_id, strategy_spec.strategy, inst_id, bar,
                executor, take_profit_usd=take_profit_usd, stop_loss_usd=stop_loss_usd,
                priming=priming, priming_bars=priming_bars,
                strategy_name=strategy_spec.strategy_name,
                strategy_kind=strategy_spec.strategy_kind,
                strategy_id=strategy_spec.strategy_id)
            self._session = session
            return await self._launch_or_rollback(session)

    async def _launch_or_rollback(self, session: LiveAutoSession) -> LiveAutoSession:
        """启动会话；失败则回滚平仓 + 清空注册表，不掩盖原始启动异常。"""
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

    async def stop(self, session_id: str, reason: str = "user_stop") -> tuple | None:
        session = self.get(session_id)
        if session is None:
            return None
        flat = await session.stop(reason)
        if not flat.get("ok"):   # H4：平仓失败不静默——日志留痕（API 仍回 flattened 供 UI 强提示）
            logger.warning("live-auto stop flatten failed: session=%s code=%s residual=%s",
                           session_id, flat.get("code"), flat.get("residual"))
        return session, flat

    def get(self, session_id: str) -> LiveAutoSession | None:
        session = self._session
        return session if session is not None and session.session_id == session_id else None

    def active(self) -> LiveAutoSession | None:
        session = self._session
        return session if session is not None and session.state == "running" else None

    def status_row(self) -> dict | None:
        session = self.active()
        return session.info() if session is not None else None

    async def shutdown(self) -> None:
        session = self._session
        if session is not None and session.state == "running":
            flat = await session.stop("shutdown")
            if not flat.get("ok"):   # H4：进程退出场景日志为唯一出口，不静默吞平仓失败
                logger.warning("live-auto shutdown flatten failed: session=%s code=%s residual=%s",
                               session.session_id, flat.get("code"), flat.get("residual"))
        self._session = None

    # ---------- 前置门禁辅助 ----------
    def _require_enabled(self, settings: Settings) -> None:
        """R1：自动化总闸与底层写链路总闸须同时为 True（不依赖『服务层内已强制』）。"""
        if not (settings.live_auto_trading_enabled and settings.live_trading_enabled):
            raise LiveAutoPreflightError(
                "LIVE_AUTO_TRADING_DISABLED", "实时自动交易未启用", 403)

    def _require_capacity(self) -> None:
        session = self._session
        if session is not None and session.state == "running":
            raise LiveAutoSessionLimitError(
                "LIVE_AUTO_SESSION_LIMIT", "已有活跃自动交易会话（单会话独占，请先停止）", 409)

    @staticmethod
    def _has_position(positions: list[dict], inst_id: str) -> bool:
        return any(str(p.get("instId")) == inst_id and abs(p.get("pos") or 0.0) > 0
                   for p in positions)

    @staticmethod
    def _resolve_budget(capital_usd: float | None, avail: float, settings: Settings) -> float:
        """R4：留空→ availUsdt×默认 frac；一律夹紧到 ≤ availUsdt×硬上限 frac。"""
        default_budget = avail * settings.live_auto_default_capital_frac
        budget = capital_usd if (capital_usd and capital_usd > 0) else default_budget
        hard_cap = avail * settings.live_auto_max_capital_frac
        return min(budget, hard_cap) if hard_cap > 0 else budget

    @staticmethod
    def _build_executor(creds: AccountCredentials, account_id: int, inst_id: str, config: dict,
                        budget: float, lever: float, risk, settings: Settings) -> LiveExecutor:
        meta = config.get("instMeta") or {}
        return LiveExecutor(
            creds=creds, account_id=account_id, inst_id=inst_id,
            td_mode=settings.live_default_td_mode,
            pos_mode=config.get("posMode") or "long_short_mode",
            leverage=lever, budget_usd=budget, risk=risk,
            ct_val=meta.get("ctVal") or 0.0, lot_sz=meta.get("lotSz") or 0.0,
            min_sz=meta.get("minSz") or 0.0, settings=settings)

    @staticmethod
    async def _create_run(strategy_spec, inst_id: str, bar: str, budget: float, lever: float,
                          take_profit_usd: float | None, stop_loss_usd: float | None) -> int:
        sm = get_sessionmaker()
        async with sm() as s:
            run = await repo.create_run(
                s, strategy_id=strategy_spec.strategy_id, inst_id=inst_id, bar=bar,
                mode="live-auto",
                config={"mode": "live-auto", "strategyId": strategy_spec.strategy_id,
                        "strategyName": strategy_spec.strategy_name, "budgetUsd": budget,
                        "leverage": lever, "takeProfitUsd": take_profit_usd,
                        "stopLossUsd": stop_loss_usd},
                status="running")
            return run.id


_manager: LiveAutoManager | None = None


def get_live_auto_manager() -> LiveAutoManager:
    global _manager
    if _manager is None:
        _manager = LiveAutoManager()
    return _manager
