"""权重模型流式训练作业管理（weight-model-workspace §3.3 + hardening 主线 A）。

进程内单例 ``WeightTrainManager`` **只持作业注册表**（镜像 ``paper/manager.py``）；asyncio.Queue 扇出 +
全量事件缓冲（含终态，供晚订阅 / 重连 / 快作业无损回放，R-06）落在 per-job ``_TrainJob`` 上（镜像
``paper/session.py`` 的 ``subscribe/unsubscribe/_push``，R-08）。作业与 socket **解耦**：WS 断开只取消订阅，
训练继续跑完并落库（历史持久可靠）。

「loss 曲线」为**真实扩样本学习曲线**（``fit_at_fraction`` 在严格因果时序切分上度量验证段损失 / IC），
末步 ``f=1.0`` 经 ``train_weights`` 装配最终产物，与 ``POST /factor-sets/train`` 对相同输入字节级等价
（放行条件 1）。取数一律走真实 OKX（复用 ``service._load_df`` 的进程内 K 线缓存），CPU 工作交
``run_in_threadpool``（绝不阻塞事件循环）。每条 run 必达终态之一（succeeded / failed / cancelled）。

加固（hardening 主线 A）：H-B1 落库失败也必达终态（``_persist`` 尽力而为不抛 + 有界重试一次，绝不让 WS
永久悬挂 / run 永久卡 running）；H-B2 取消在加载 / 构建阶段也及时响应；H-B3 并发上限改同步预占硬上限；
H-B4 关停先置标志、再带超时 join、最后 ``force_cancel`` 兜底；H-B6 注入时钟使 TTL 淘汰可确定性测试。
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass

from fastapi import HTTPException
from fastapi.concurrency import run_in_threadpool

from ..config import get_settings
from ..db import repositories as repo
from ..db.session import get_sessionmaker
from ..engine.factors import (
    InsufficientSamplesError,
    build_factor_matrix,
    fit_at_fraction,
    forward_return_label,
    learning_curve_steps,
    prepare_curve_sample,
    train_weights,
)
from ..engine.factors.scoring import DEFAULT_CLIP, DEFAULT_NORM_WINDOW
from ..okx.rest_client import OKXError
from . import service
from .service import FactorWindow

logger = logging.getLogger("aragon")

_TERMINAL_TYPES = ("done", "error", "cancelled")
_FINISHED_TTL_SECONDS = 600.0   # 终态作业在注册表保留时长（供晚订阅 / 重连回放后回收）
_FINISHED_JOBS_CAP = 64         # 终态作业保留数上限（超出按完成时间淘汰最旧）
_PERSIST_RETRY_DELAY = 0.2      # H-B1/R-02：瞬态 SQLite 锁多在数十~数百 ms 内自清，单次有界重试
_SHUTDOWN_JOIN_TIMEOUT = 5.0    # H-B4：关停 join 单作业超时，超时则 task.cancel() 硬取消兜底


@dataclass(frozen=True)
class JobSpec:
    """启动一个流式训练作业的全部输入（聚合传参）。"""

    run_id: int
    model_id: int
    rest: object
    members: list
    method: str
    config: dict
    window: FactorWindow
    horizon: int
    run_cap: int
    curve_steps: int


class _TrainJob:
    """单个流式训练作业：扇出 + 全量事件缓冲（含终态）+ async 训练协程（镜像 PaperSession 分层）。"""

    def __init__(self, job_id: str, spec: JobSpec, *, clock: Callable[[], float] = time.monotonic):
        self.job_id = job_id
        self.run_id = spec.run_id
        self.model_id = spec.model_id
        self.status = "running"
        self._spec = spec
        self._clock = clock                       # H-B6：可注入单调时钟（TTL 确定性测试）
        self._buffer: list[dict] = []
        self._subscribers: set[asyncio.Queue] = set()
        self._cancel = False
        self._task: asyncio.Task | None = None
        self.finished_mono: float | None = None

    # ---------- fan-out（抄 paper/session.py，R-08）----------
    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=256)
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subscribers.discard(q)

    def snapshot(self) -> list[dict]:
        """订阅即全量回放：从 ``run`` 到终态的**全部**缓冲事件（晚订阅 / 重连 / 快作业无损，R-06）。"""
        return list(self._buffer)

    @property
    def is_terminal(self) -> bool:
        return self.status in ("succeeded", "failed", "cancelled")

    def _push(self, event: dict) -> None:
        self._buffer.append(event)
        for q in list(self._subscribers):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass

    # ---------- 生命周期 ----------
    def start(self) -> None:
        self._task = asyncio.create_task(self._run())

    def request_cancel(self) -> None:
        """协作式取消标志置位（步间检查，R-03）；已终态幂等无副作用。"""
        if not self.is_terminal:
            self._cancel = True

    def force_cancel(self) -> None:
        """硬取消（H-B4）：取消协程任务，由 ``_run`` 的 ``except CancelledError`` 落 ``cancelled`` 终态。"""
        task = self._task
        if task is not None and not task.done():
            task.cancel()

    async def join(self) -> None:
        task = self._task
        if task is not None and not task.done():
            try:
                await task
            except asyncio.CancelledError:
                pass

    # ---------- 训练协程 ----------
    async def _run(self) -> None:
        spec = self._spec
        logger.info("weight run %d 启动流式训练：method=%s inst=%s bar=%s",   # G-B8：启动可观测
                    self.run_id, spec.method, spec.window.inst_id, spec.window.bar)
        try:
            self._push({"type": "run", "data": {
                "runId": self.run_id, "modelId": self.model_id, "status": "running"}})
            self._emit_phase("loading", 5)
            df = await service._load_df(spec.rest, spec.window)
            if self._cancel:                       # H-B2：取消在最长的 OKX 取数后立即生效
                return await self._finalize_cancelled()
            self._emit_phase("building", 25)
            norm_window = int(spec.config.get("normWindow", DEFAULT_NORM_WINDOW))
            clip = float(spec.config.get("clip", DEFAULT_CLIP))
            matrix, names, norm = await run_in_threadpool(
                build_factor_matrix, df, spec.members, norm_window=norm_window, clip=clip,
                bar=spec.window.bar)
            if self._cancel:                       # H-B2：构建（线程池）后立即生效
                return await self._finalize_cancelled()
            label = forward_return_label(df, spec.horizon)
            await self._fit_and_finalize(matrix, label, names, norm)
        except OKXError as exc:
            await self._fail("OKX_UNREACHABLE", exc.message)
        except HTTPException as exc:               # G-B2：数据加载层 HTTPException（如 EMPTY_RANGE）如实分类，
            code, message = _http_detail(exc)      # 不再吞成 WEIGHT_TRAIN_FAILED + dict repr 外泄。
            await self._fail(code, message)
        except asyncio.CancelledError:
            await self._finalize_cancelled()
            raise
        except Exception as exc:  # noqa: BLE001 —— 作业内任何失败都收口为 failed 终态，绝不卡 running
            await self._fail("WEIGHT_TRAIN_FAILED", str(exc))

    async def _fit_and_finalize(self, matrix, label, names, norm) -> None:
        """对齐守门（R-07）→ 逐 fraction 推真实曲线（步间检查取消，R-03）→ 末步等价装配并落库。"""
        spec = self._spec
        try:
            sample = await run_in_threadpool(prepare_curve_sample, matrix, label, spec.config)
        except InsufficientSamplesError as exc:    # H-B5/R-05：仅样本不足落 INSUFFICIENT_SAMPLES
            await self._fail("WEIGHT_TRAIN_INSUFFICIENT_SAMPLES", str(exc))
            return
        steps = learning_curve_steps(sample.cut, steps=spec.curve_steps)
        self._emit_phase("fitting", 40)
        curve: list[dict] = []
        for index, frac in enumerate(steps):
            if self._cancel:
                await self._finalize_cancelled()
                return
            weights, point = await run_in_threadpool(
                fit_at_fraction, sample.matrix, sample.label, names,
                cut=sample.cut, frac=frac, method=spec.method, config=spec.config)
            point["step"] = index
            curve.append(point)
            # WS 实时事件附带该步权重（供前端画「收敛中的权重条」§7.2#3）；**不**入 curve_json（仅存 TrainCurvePoint）。
            self._push({"type": "curve", "data": {**point, "weights": weights}})
        if self._cancel:
            await self._finalize_cancelled()
            return
        self._emit_phase("evaluating", 90)
        # 末步等价：以**原始** matrix/label 经 train_weights 装配最终产物（= 同步 POST /factor-sets/train 字节级等价）。
        result = await run_in_threadpool(
            train_weights, matrix, label, names, method=spec.method, config=spec.config,
            norm_stats=norm)
        await self._finalize_success(result, curve)

    async def _finalize_success(self, result, curve: list[dict]) -> None:
        spec = self._spec
        provenance = {"instId": spec.window.inst_id, "bar": spec.window.bar,
                      "startTs": spec.window.start_ts, "endTs": spec.window.end_ts,
                      "horizon": int(spec.horizon)}
        await self._persist(repo.WeightRunOutcome(
            status="succeeded", finished_ts=_now(), curve=curve, weights=result.weights,
            norm_stats=result.normStats, metrics=result.metrics, provenance=provenance,
            trainer_version=result.trainerVersion, note=result.note))
        self._push({"type": "result", "data": {
            "method": result.method, "weights": result.weights, "normStats": result.normStats,
            "metrics": result.metrics, "trainerVersion": result.trainerVersion,
            "note": result.note, "provenance": provenance}})
        self._push({"type": "done", "data": {"runId": self.run_id, "finishedTs": _now()}})
        logger.info("weight run %d 训练完成 succeeded：method=%s backend=%s nTrain=%s",   # G-B8
                    self.run_id, result.method, result.trainerVersion,
                    (result.metrics or {}).get("nTrain"))

    async def _finalize_cancelled(self) -> None:
        if self.is_terminal:
            return
        await self._persist(repo.WeightRunOutcome(
            status="cancelled", finished_ts=_now(), error_code="WEIGHT_TRAIN_CANCELLED",
            error_message="用户取消训练。"))
        self._push({"type": "cancelled", "data": {"runId": self.run_id, "finishedTs": _now()}})
        logger.info("weight run %d 训练已取消 cancelled", self.run_id)   # G-B8

    async def _fail(self, code: str, message: str) -> None:
        if self.is_terminal:
            return
        await self._persist(repo.WeightRunOutcome(
            status="failed", finished_ts=_now(), error_code=code, error_message=message))
        self._push({"type": "error", "data": {"code": code, "message": message, "runId": self.run_id}})
        logger.info("weight run %d 训练失败 failed：code=%s", self.run_id, code)   # G-B8

    async def _persist(self, outcome: repo.WeightRunOutcome) -> bool:
        """落终态记录（**尽力而为、绝不抛**）+ 维护模型指针；H-B1/R-02：失败先有界重试一次，仍失败则记错放弃。

        无论落库成败都把作业转终态并推终态事件（解耦 DB 结果），否则 DB 写失败会让 WS 订阅者永久卡
        ``await queue.get()`` 且 run 永久 ``running``（P0 故障链）。返回是否落库成功（调用方目前不依赖，
        留待精确重试增强）。
        """
        ok = await self._try_finalize(outcome)
        if not ok:
            await asyncio.sleep(_PERSIST_RETRY_DELAY)   # R-02：瞬态锁一次有界重试
            ok = await self._try_finalize(outcome)
        if not ok:
            # R-02：重试仍失败 → 显式记错并放弃；run 行真实留 running（绝不静默假成功），
            # 由启动对账（reconcile_dangling_weight_runs）在下次启动改判 failed。
            logger.error(
                "weight run %d 落库失败（已重试 1 次），仍发终态事件并转终态；run 行留 running 待启动对账修正",
                self.run_id)
        self.status = outcome.status            # 无论成败都转终态（is_terminal=true → 可回收、WS 可关）
        self.finished_mono = self._clock()
        return ok

    async def _try_finalize(self, outcome: repo.WeightRunOutcome) -> bool:
        """单次落库尝试（一次性 sessionmaker session，与 ``_backfill_companion_strategies`` 同范式）；成功 True，异常 False。"""
        try:
            sm = get_sessionmaker()
            async with sm() as session:
                await repo.finalize_weight_run(session, self.run_id, outcome=outcome,
                                               run_cap=self._spec.run_cap)
            return True
        except Exception as exc:  # noqa: BLE001 —— DB 落库失败不得吞掉终态事件，否则 WS 永久悬挂
            logger.warning("weight run %d finalize 尝试失败：%s", self.run_id, exc)
            return False

    def _emit_phase(self, phase: str, pct: int) -> None:
        self._push({"type": "phase", "data": {"phase": phase, "pct": pct}})


def _now() -> int:
    return int(time.time())


def _http_detail(exc: HTTPException) -> tuple[str, str]:
    """从 ``HTTPException.detail`` 安全提取 ``(code, message)``（detail 可为统一错误 dict 或裸 str），G-B2。"""
    detail = exc.detail
    if isinstance(detail, dict):
        return str(detail.get("code", "WEIGHT_TRAIN_FAILED")), str(detail.get("message", ""))
    return "WEIGHT_TRAIN_FAILED", str(detail)


class WeightTrainManager:
    """流式训练作业注册表（进程内单例，job-capped，lifespan 关闭）。"""

    def __init__(self, *, clock: Callable[[], float] = time.monotonic):
        self._jobs: dict[str, _TrainJob] = {}
        self._reserved = 0                   # H-B3：同步预占名额（覆盖守门→注册之间的 await 窗口）
        self._clock = clock                  # H-B6：可注入单调时钟（TTL 确定性测试）

    def active_count(self) -> int:
        return sum(1 for job in self._jobs.values() if job.status == "running")

    def active_run_ids(self) -> set[int]:
        """当前**非终态**作业（真·在跑）的 ``run_id`` 集（G-B1/R-01：删模型守门借此区分真·在跑 vs 孤儿）。"""
        return {job.run_id for job in self._jobs.values() if not job.is_terminal}

    def reserve(self) -> bool:
        """同步、无 await 预占一个并发名额（H-B3）：守门即占名额，杜绝 ``active_count`` 判定到注册之间的 TOCTOU。

        R-03：动态读 ``settings``——manager 为惰性单例，**不缓存** cap（缓存会与运行期 settings 覆写脱钩，
        并使「并发 cap+1」回归测试无法确定性控制上限），与既有 service 层动态读取保持一致。
        """
        cap = get_settings().max_weight_train_jobs
        if self.active_count() + self._reserved >= cap:
            return False
        self._reserved += 1
        return True

    def release(self) -> None:
        """释放一个预占名额（守门后任何失败路径调用；``start_job`` 注册成功后亦调一次转 running 计数）。"""
        self._reserved = max(0, self._reserved - 1)

    def start_job(self, spec: JobSpec) -> _TrainJob:
        """注册并启动一个流式训练作业（此刻起即开缓冲，R-01 守门已在 service 层全过）。"""
        self._prune_finished()
        job = _TrainJob(uuid.uuid4().hex, spec, clock=self._clock)
        self._jobs[job.job_id] = job
        try:
            self.release()                   # 预占转为 running 计数（job.status=="running" 即被 active_count 计入）
            job.start()
        except Exception:                    # G-B6：半注册自愈——start() 罕见抛（无事件循环）则回滚注册，
            self._jobs.pop(job.job_id, None)  # 杜绝永不终态的幽灵 running 计入 active_count。
            raise
        return job

    def get(self, job_id: str) -> _TrainJob | None:
        return self._jobs.get(job_id)

    def cancel(self, job_id: str) -> str:
        """协作式取消（R-03）；返回 'cancelling'（running 作业已置标志）| 'finished'（已终态）| 'unknown'。"""
        job = self._jobs.get(job_id)
        if job is None:
            return "unknown"
        if job.is_terminal:
            return "finished"
        job.request_cancel()
        return "cancelling"

    async def shutdown(self) -> None:
        """关停（H-B4）：① 先把所有作业一次性置取消标志 → ② 带超时 join，超时则 ``force_cancel`` 兜底。"""
        jobs = list(self._jobs.values())
        for job in jobs:                       # ① 先置全部取消标志（不再串行等待）
            job.request_cancel()
        for job in jobs:                       # ② 带超时 join；超时则硬取消兜底
            try:
                await asyncio.wait_for(job.join(), timeout=_SHUTDOWN_JOIN_TIMEOUT)
            except asyncio.TimeoutError:
                job.force_cancel()
                await job.join()
        self._jobs.clear()

    def _prune_finished(self) -> None:
        """淘汰过期终态作业（TTL + 数量上限）；running 作业绝不淘汰（作业与 socket 解耦，续跑可靠）。"""
        now = self._clock()
        finished = [(jid, job) for jid, job in self._jobs.items()
                    if job.is_terminal and job.finished_mono is not None]
        for jid, job in finished:
            if now - (job.finished_mono or now) > _FINISHED_TTL_SECONDS:
                self._jobs.pop(jid, None)
        finished = [(jid, job) for jid, job in self._jobs.items()
                    if job.is_terminal and job.finished_mono is not None]
        if len(finished) > _FINISHED_JOBS_CAP:
            finished.sort(key=lambda item: item[1].finished_mono or 0.0)
            for jid, _ in finished[:len(finished) - _FINISHED_JOBS_CAP]:
                self._jobs.pop(jid, None)


_manager: WeightTrainManager | None = None


def get_weight_train_manager() -> WeightTrainManager:
    global _manager
    if _manager is None:
        _manager = WeightTrainManager()
    return _manager
