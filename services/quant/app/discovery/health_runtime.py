"""持续样本外体检守护（discovery-asset-ledger §2.6）—— 独立于 run 生命周期的后台协程。

四条边界纪律，一条都不能松：

- **AL-6**：只读 OKX 行情、只写 ``discovery_assets`` / ``discovery_asset_checks`` 两张新表。
  **严禁**改 ``factors`` / ``factor_sets`` / ``strategies`` / ``strategy_runs``，**严禁**下单，
  **严禁**改任何 run 的状态。降级只标注（advisory），停用与否是用户的显式动作。
- **AL-7**：``discovery_enabled`` 与 ``discovery_health_enabled`` 双闸，**每 tick 开头重读**；
  任一为假即本 tick 零外部副作用（不取数、不写库）。只在启动时判一次的话，运行期关不掉。
- **AL-9**：资源硬闸按**底层 ``run_backtest`` 调用次数**计，预估口径 ``2 × per_segment``
  （``run_split_backtest`` 无条件跑 IS + OOS 两段），被闸挡下的资产必须 log + 计数。
- **AL-12**：``instId`` / ``bar`` / ``horizon`` 一律取**资产自己的**；按 ``(instId, bar)``
  分组，一组一次取数、**一组一份 df 与一份 memo**（跨组必须新建，见 ``_GroupData``）。
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field

import numpy as np
from fastapi import HTTPException
from fastapi.concurrency import run_in_threadpool

from ..config import get_settings
from ..db.session import get_sessionmaker
from ..engine.backtester import candles_to_df
from ..engine.mtf import bar_to_seconds
from ..okx.rest_client import OKXError, get_rest_client
from . import asset_repositories as asset_repo
from . import health, window_loader
from .genomes import GENOME_LOADERS
from .lanes import LaneContext
from .validation import ValidationContext, evaluate_split

logger = logging.getLogger("aragon")

DATA_BACKOFF_BASE_S = 60.0
DATA_BACKOFF_MAX_S = 3600.0
FACTOR_AXIS = "factor"


@dataclass
class _GroupData:
    """一个 ``(instId, bar)`` 组的取数产物。

    ``memo`` **一组一份、跨组必须新建**：``validation._context_of`` 用固定键把
    ``build_context(df_full)`` 缓存在 memo 上且**不校验 df 身份**，同一个 memo 见到第二份 df，
    第二个资产就会在**别人的字段上下文**上求值——不抛错、指标照算、全是错的（评审 P1-2）。
    """

    candles: list
    frames: dict
    df_full: object
    window_start_ts: int
    window_end_ts: int
    memo: dict = field(default_factory=dict)


@dataclass
class _CheckOutcome:
    """一次体检的判级产物（落库前的内存形态）。"""

    status: str
    reasons: list
    retention: float | None
    metrics: dict
    clip_mode: str = ""
    error: str = ""
    backtests: int = 0


class HealthGuard:
    """体检守护单例：双闸 → 到期选取 → 分组取数 → 逐资产判级 → 落库 + 剪枝。"""

    def __init__(self) -> None:
        self._inflight: set[int] = set()
        self._market_backoff: dict[tuple[str, str], float] = {}
        # 连续 error 次数（**内存态**，不落库）：AL-8 明令 error 不得动 ``consecutive_fail``
        # （那是 H4/H5/H6 的迟滞计数），所以退避的指数只能另外记。拿 ``consecutive_fail``
        # 顶替会让退避指数取一个语义完全无关的数——重启即清零是可接受的代价。
        self._error_streak: dict[int, int] = {}
        self._task: asyncio.Task | None = None
        self._state: dict = {"enabled": True, "running": False, "lastTickTs": 0,
                             "lastError": "", "checkedTotal": 0, "skippedByCap": 0,
                             "dataFailures": 0}

    # ---------- 同步预占（无 await，杜绝 TOCTOU） ----------
    def claim(self, asset_id: int) -> bool:
        """占用该资产；已被守护或另一次手动体检占用即 ``False``（调用方回 409）。"""
        if int(asset_id) in self._inflight:
            return False
        self._inflight.add(int(asset_id))
        return True

    def release(self, asset_id: int) -> None:
        self._inflight.discard(int(asset_id))

    def state(self) -> dict:
        settings = get_settings()
        from . import assets

        return {**self._state, "inFlight": sorted(self._inflight),
                "assetLimitHits": assets.limit_hits(),
                "intervalS": float(settings.discovery_health_interval_s),
                "tickS": float(settings.discovery_health_tick_s),
                "warmupBars": int(settings.discovery_health_warmup_bars),
                "windowBars": int(settings.discovery_health_window_bars)}

    # ---------- 生命周期 ----------
    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self.run_forever())

    async def shutdown(self) -> None:
        task = self._task
        self._task = None
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001 —— 关停不外抛
                pass
        self._state["running"] = False

    async def run_forever(self) -> None:
        """常驻循环；cancel-safe，其余异常只记 ``lastError`` 不让协程死掉（R-9）。"""
        self._state["running"] = True
        try:
            while True:
                try:
                    await self._tick()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001 —— 单 tick 失败不得终结守护
                    self._state["lastError"] = f"{type(exc).__name__}:{exc}"[:256]
                    logger.warning("discovery health tick failed: %s", exc)
                await asyncio.sleep(float(get_settings().discovery_health_tick_s))
        except asyncio.CancelledError:
            return
        finally:
            self._state["running"] = False

    # ---------- 单 tick ----------
    async def _tick(self) -> None:
        settings = get_settings()
        enabled = bool(settings.discovery_enabled and settings.discovery_health_enabled)
        self._state["enabled"] = enabled
        if not enabled:
            return                       # 零外部副作用：不取数、不写库（AL-7）
        now = int(time.time())
        sm = get_sessionmaker()
        async with sm() as session:
            due = await asset_repo.list_due_assets(
                session, now=now, limit=int(settings.discovery_health_batch))
        if not due:
            self._state["lastTickTs"] = now
            return
        used = 0
        cap = int(settings.discovery_health_max_backtests_per_tick)
        for (inst_id, bar), group in _group_by_market(due).items():
            used += await self._run_group(inst_id, bar, group, used=used, cap=cap)
        self._state["lastTickTs"] = int(time.time())

    async def _run_group(self, inst_id: str, bar: str, group: list, *, used: int,
                         cap: int) -> int:
        """一组一次取数、逐资产体检；返回本组消耗的底层回测次数。"""
        settings = get_settings()
        if self._market_backoff.get((inst_id, bar), 0.0) > time.monotonic():
            return 0
        try:
            data = await self._load_group(inst_id, bar, group, settings)
        except (OKXError, HTTPException, asyncio.TimeoutError, OSError) as exc:
            self._note_data_failure(inst_id, bar, exc)
            return 0                     # AL-8：取数失败**不写** check 行，整组跳过
        spent = 0
        for asset in group:
            estimate = _estimate_backtests(asset, settings)
            if used + spent + estimate > cap:
                self._state["skippedByCap"] = int(self._state["skippedByCap"]) + 1
                logger.info("discovery health tick capped: 资产 #%s 本轮跳过（已用 %d/%d 次回测）",
                            asset.id, used + spent, cap)
                break
            if not self.claim(int(asset.id)):
                continue
            try:
                spent += await self._check_and_write(asset, data, settings)
            finally:
                self.release(int(asset.id))
        return spent

    async def _load_group(self, inst_id: str, bar: str, group: list,
                          settings) -> _GroupData:
        step = bar_to_seconds(bar)
        limits = health.HealthLimits.of(settings)
        check_end = health.aligned_check_end(time.time(), step)
        window_start = health.group_window_start(check_end, step, limits)
        candles, frames = await window_loader.load_window(
            get_rest_client(), inst_id=inst_id, bar=bar, start_ts=window_start,
            end_ts=check_end, timeframes=_group_timeframes(group, settings),
            settings=settings)
        self._market_backoff.pop((inst_id, bar), None)
        return _GroupData(candles=list(candles), frames=frames,
                          df_full=candles_to_df(candles), window_start_ts=window_start,
                          window_end_ts=check_end)

    def _note_data_failure(self, inst_id: str, bar: str, exc: Exception) -> None:
        """按 ``(instId, bar)`` 指数退避；**绝不**用旧帧或插值把体检跑完。"""
        self._state["dataFailures"] = int(self._state["dataFailures"]) + 1
        previous = self._market_backoff.get((inst_id, bar), 0.0) - time.monotonic()
        delay = min(DATA_BACKOFF_MAX_S, max(DATA_BACKOFF_BASE_S, previous * 2.0))
        self._market_backoff[(inst_id, bar)] = time.monotonic() + delay
        logger.warning("discovery health 取数失败 %s/%s（%s 秒后重试）：%s",
                       inst_id, bar, int(delay), exc)

    # ---------- 单资产 ----------
    async def _check_and_write(self, asset, data: _GroupData, settings) -> int:
        """跑一次体检并落库；返回真实消耗的底层回测次数（用于回冲预估，AL-9）。"""
        limits = health.HealthLimits.of(settings)
        step = bar_to_seconds(asset.bar)
        window = _window_of(asset, data, step, limits)
        outcome = await self._evaluate(asset, data, window, settings)
        now = int(time.time())
        status, streak = _status_of(asset, outcome, limits)
        errors = self._note_error_streak(int(asset.id), status)
        plan = health.SchedulePlan(status=status, now=now, step=step,
                                   fresh_bars=window.fresh_bars,
                                   consecutive_error=errors)
        sm = get_sessionmaker()
        async with sm() as session:
            await asset_repo.write_check(
                session,
                asset_repo.CheckValues(
                    asset_id=int(asset.id), checked_ts=now,
                    window_start_ts=window.window_start_ts,
                    window_end_ts=window.window_end_ts,
                    fresh_start_ts=window.fresh_start_ts, fresh_bars=window.fresh_bars,
                    warmup_bars=window.warmup_bars, status=status, reasons=outcome.reasons,
                    retention=outcome.retention, clip_mode=outcome.clip_mode,
                    metrics=outcome.metrics, error=outcome.error),
                health={"status": status, "retention": outcome.retention,
                        "consecutiveFail": streak,
                        "nextCheckTs": health.next_check_ts(plan, limits)},
                max_rows=int(settings.discovery_health_max_check_rows))
        self._state["checkedTotal"] = int(self._state["checkedTotal"]) + 1
        return int(outcome.backtests)

    def _note_error_streak(self, asset_id: int, status: str) -> int:
        """连续 error 计数（退避的指数）；任何非 error 结果立刻清零。"""
        if status != "error":
            self._error_streak.pop(asset_id, None)
            return 0
        streak = self._error_streak.get(asset_id, 0)
        self._error_streak[asset_id] = streak + 1
        return streak

    async def _evaluate(self, asset, data: _GroupData, window, settings) -> _CheckOutcome:
        """求值 / 回测 + 判级；超时与异常都收口为 ``status="error"`` 的 check 行（AL-8）。"""
        limits = health.HealthLimits.of(settings)
        thresholds = asset_repo.loads(asset.accept_thresholds_json, {}) or {}
        accept = asset_repo.loads(asset.accept_metrics_json, {}) or {}
        short = health.window_reasons(window.fresh_bars, window.warmup_bars, limits)
        if short:
            return _CheckOutcome(status="unknown", reasons=short, retention=None,
                                 metrics={"fresh": {}, "history": _history_of(asset, accept)})
        try:
            fresh, clip_mode, backtests = await asyncio.wait_for(
                run_in_threadpool(_measure, asset, data, window, settings),
                timeout=float(settings.discovery_health_asset_timeout_s))
        except asyncio.TimeoutError:
            return _CheckOutcome(status="error", reasons=[], retention=None,
                                 metrics={"fresh": {}, "history": _history_of(asset, accept)},
                                 error="timeout")
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 —— 单资产失败绝不影响后续资产
            return _CheckOutcome(status="error", reasons=[], retention=None,
                                 metrics={"fresh": {}, "history": _history_of(asset, accept)},
                                 error=f"{type(exc).__name__}:{exc}"[:256])
        fresh = {**fresh, "freshBars": window.fresh_bars, "warmupBars": window.warmup_bars}
        side = _accept_side(asset, accept)
        reasons = health.grade(str(asset.kind), fresh, side, thresholds, limits)
        return _CheckOutcome(
            status="", reasons=reasons,
            retention=health.retention_of(str(asset.kind), fresh, side),
            metrics={"fresh": _bounded(fresh), "history": _history_of(asset, accept)},
            clip_mode=clip_mode, backtests=backtests)

    # ---------- 手动立即体检（§2.8） ----------
    async def check_now(self, asset_id: int):
        """同步跑一次并返回新写入的 check 行；与守护的互斥由 ``claim`` 保证。"""
        settings = get_settings()
        sm = get_sessionmaker()
        async with sm() as session:
            asset = await asset_repo.get_asset(session, int(asset_id))
        if asset is None:
            raise _bad("NOT_FOUND", "成果资产不存在", 404)
        if asset.archived:
            raise _bad("DISCOVERY_ASSET_ARCHIVED", "已归档资产不接受体检", 409)
        try:
            data = await self._load_group(asset.inst_id, asset.bar, [asset], settings)
        except (OKXError, HTTPException, asyncio.TimeoutError, OSError) as exc:
            self._note_data_failure(asset.inst_id, asset.bar, exc)
            raise _bad("DISCOVERY_DATA_UNAVAILABLE",
                       f"体检取数失败，未写入任何记录：{exc}", 503) from exc
        await self._check_and_write(asset, data, settings)
        sm = get_sessionmaker()
        async with sm() as session:
            rows, _total = await asset_repo.list_checks(session, int(asset_id), limit=1)
        return rows[-1] if rows else None


# ============================ 模块级纯函数 ============================
def _bad(code: str, message: str, status: int) -> HTTPException:
    return HTTPException(status, detail={"code": code, "message": message})


def _group_by_market(assets_due: list) -> dict:
    grouped: dict = {}
    for asset in assets_due:
        grouped.setdefault((str(asset.inst_id), str(asset.bar)), []).append(asset)
    return grouped


def _group_timeframes(group: list, settings) -> list[str]:
    """组内**策略资产**所需高周期的并集；组内没有策略资产就一帧都不取。"""
    needed: set[str] = set()
    for asset in group:
        if str(asset.kind) == FACTOR_AXIS:
            continue
        try:
            needed.update(_timeframes_of(asset, settings))
        except Exception as exc:  # noqa: BLE001 —— 陈旧基因不得拖垮整组取数
            logger.debug("资产 #%s 的高周期需求无法判定：%s", asset.id, exc)
    return sorted(needed)


def _assembler_of(asset, settings):
    from .lanes.strategy_lane import assembler_for

    genome = asset_repo.loads(asset.genome_json, {}) or {}
    return assembler_for(str(asset.kind), GENOME_LOADERS[str(asset.kind)](genome), settings)


def _timeframes_of(asset, settings) -> list[str]:
    from .promote import _required_timeframes

    return list(_required_timeframes(_assembler_of(asset, settings)))


def _estimate_backtests(asset, settings) -> int:
    """预估口径 **``2 × backtests_per_segment()``**（AL-9）；因子资产计 0。

    ``run_split_backtest`` 无条件跑 IS + OOS 两段。按 ``per_segment`` 计，护栏放行量正好是
    设计值的两倍，而表现是「从没触发过闸」——看起来完全正常（评审 P1-4）。
    """
    if str(asset.kind) == FACTOR_AXIS:
        return 0
    try:
        return 2 * int(_assembler_of(asset, settings).backtests_per_segment())
    except Exception:  # noqa: BLE001 —— 判不出来就按最保守的一次双段计
        return 2


def _window_of(asset, data: _GroupData, step: int, limits: health.HealthLimits):
    mined = asset_repo.loads(asset.mined_window_json, {}) or {}
    fresh_start = health.fresh_start_of(int(mined.get("endTs") or 0), data.window_end_ts,
                                        step, limits)
    return health.split_window(list(data.df_full["ts"]), fresh_start_ts=fresh_start,
                               window_start_ts=data.window_start_ts,
                               window_end_ts=data.window_end_ts)


def _status_of(asset, outcome: _CheckOutcome, limits: health.HealthLimits) -> tuple[str, int]:
    """``error`` 与 ``unknown`` 一律不动连续失败计数（AL-8）。"""
    if outcome.status in ("error", "unknown"):
        return outcome.status, int(asset.consecutive_fail or 0)
    return health.next_status(int(asset.consecutive_fail or 0), outcome.reasons,
                              fail_streak_gate=limits.fail_streak)


def _accept_side(asset, accept: dict) -> dict:
    """判级分母：因子取登记时的 ``is`` 段，策略取 ``oos`` 段（AL-3）。"""
    key = "is" if str(asset.kind) == FACTOR_AXIS else "oos"
    side = accept.get(key)
    return dict(side) if isinstance(side, dict) else {}


def _history_of(asset, accept: dict) -> dict:
    """详情双栏的左栏（登记时快照），≤12 个标量。"""
    side = _accept_side(asset, accept)
    keys = (("rankIc", "ir", "tStat", "samples") if str(asset.kind) == FACTOR_AXIS
            else ("sharpe", "totalReturn", "maxDrawdown", "calmar", "tradeCount"))
    return {key: side.get(key) for key in keys if key in side}


def _bounded(fresh: dict) -> dict:
    """fresh 栏同样 ≤12 个标量（check 行不因此膨胀）。"""
    keys = ("rankIc", "ir", "tStat", "occurrences", "samples", "coverage", "sharpe",
            "totalReturn", "maxDrawdown", "calmar", "tradeCount", "profitFactor")
    return {key: fresh[key] for key in keys if key in fresh}


def _measure(asset, data: _GroupData, window, settings) -> tuple[dict, str, int]:
    """同步的求值 / 回测（CPU 密集，由调用方送进线程池）。"""
    if str(asset.kind) == FACTOR_AXIS:
        return _measure_factor(asset, data, window), "", 0
    return _measure_strategy(asset, data, window, settings)


def _measure_factor(asset, data: _GroupData, window) -> dict:
    """因子资产：``evaluate_split`` + **冻结方向**（AL-1）；H3 的分母**现切一刀算**。

    ``SplitEvaluation.coverage`` 是**全窗**有限值占比（含暖机段的 rolling NaN），直接拿它当
    H3 的输入就是把暖机 NaN 的锅算到资产头上，而且暖机段越长越冤（评审 P1-3）。
    """
    from .expression import parse_expression

    ctx = ValidationContext(
        df_full=data.df_full, horizon=int(asset.horizon or 1),
        thresholds=asset_repo.loads(asset.accept_thresholds_json, {}) or {},
        memo=data.memo, split_at=window.split, frozen_direction=str(asset.direction or ""))
    result = evaluate_split(parse_expression(asset.expr_text or ""), ctx)
    stat = (result.res_oos.horizons or [None])[0]
    fresh_values = np.asarray(result.values[window.split:], dtype=float)
    coverage = float(np.isfinite(fresh_values).mean()) if len(fresh_values) else 0.0
    return {"rankIc": getattr(stat, "rankIc", None), "ir": getattr(stat, "ir", None),
            "tStat": getattr(stat, "tStat", None),
            "occurrences": int(getattr(result.res_oos, "occurrences", 0)),
            "samples": int(getattr(result.res_oos, "occurrences", 0)),
            "coverage": round(coverage, 6)}


def _measure_strategy(asset, data: _GroupData, window,
                      settings) -> tuple[dict, str, int]:
    """策略资产：``run_split_backtest``（AL-3 的同一把尺子）。

    D-28：判级分母取的是登记快照、理论上用不上 IS 段，但仍付两段回测的代价——为省一半算力
    去写一个「只跑 OOS」的分支，等于在本轮引入第二条求值路径。
    """
    from .backtest_lane import run_split_backtest

    split = max(1, min(window.split, len(data.candles) - 1))
    ctx = LaneContext(df_full=data.df_full, candles=data.candles, htf_frames=data.frames,
                      bar=str(asset.bar), split=split,
                      split_ts=int(data.df_full["ts"].iloc[split]), config={},
                      settings=settings, memo=data.memo)
    result = run_split_backtest(_assembler_of(asset, settings), ctx)
    if result.reject_reason:
        return ({"rejectReason": result.reject_reason}, result.clip_mode,
                int(result.backtests))
    return dict(result.oos_metrics), result.clip_mode, int(result.backtests)


_GUARD: HealthGuard | None = None


def get_health_guard() -> HealthGuard:
    """进程内单例（镜像 ``get_discovery_manager`` 的取用姿态）。"""
    global _GUARD
    if _GUARD is None:
        _GUARD = HealthGuard()
    return _GUARD
