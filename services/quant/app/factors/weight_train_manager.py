"""权重模型流式训练作业管理（weight-model-workspace §3.3）。

进程内单例 ``WeightTrainManager`` **只持作业注册表**（镜像 ``paper/manager.py``）；asyncio.Queue 扇出 +
全量事件缓冲（含终态，供晚订阅 / 重连 / 快作业无损回放，R-06）落在 per-job ``_TrainJob`` 上（镜像
``paper/session.py`` 的 ``subscribe/unsubscribe/_push``，R-08）。作业与 socket **解耦**：WS 断开只取消订阅，
训练继续跑完并落库（历史持久可靠）。

「loss 曲线」为**真实扩样本学习曲线**（``fit_at_fraction`` 在严格因果时序切分上度量验证段损失 / IC），
末步 ``f=1.0`` 经 ``train_weights`` 装配最终产物，与 ``POST /factor-sets/train`` 对相同输入字节级等价
（放行条件 1）。取数一律走真实 OKX（复用 ``service._load_df`` 的进程内 K 线缓存），CPU 工作交
``run_in_threadpool``（绝不阻塞事件循环）。每条 run 必达终态之一（succeeded / failed / cancelled）。
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass

from fastapi.concurrency import run_in_threadpool

from ..config import get_settings
from ..db import repositories as repo
from ..db.session import get_sessionmaker
from ..engine.factors import (
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

    def __init__(self, job_id: str, spec: JobSpec):
        self.job_id = job_id
        self.run_id = spec.run_id
        self.model_id = spec.model_id
        self.status = "running"
        self._spec = spec
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
        try:
            self._push({"type": "run", "data": {
                "runId": self.run_id, "modelId": self.model_id, "status": "running"}})
            self._emit_phase("loading", 5)
            df = await service._load_df(spec.rest, spec.window)
            self._emit_phase("building", 25)
            norm_window = int(spec.config.get("normWindow", DEFAULT_NORM_WINDOW))
            clip = float(spec.config.get("clip", DEFAULT_CLIP))
            matrix, names, norm = await run_in_threadpool(
                build_factor_matrix, df, spec.members, norm_window=norm_window, clip=clip,
                bar=spec.window.bar)
            label = forward_return_label(df, spec.horizon)
            await self._fit_and_finalize(matrix, label, names, norm)
        except OKXError as exc:
            await self._fail("OKX_UNREACHABLE", exc.message)
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
        except ValueError as exc:
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

    async def _finalize_cancelled(self) -> None:
        if self.is_terminal:
            return
        await self._persist(repo.WeightRunOutcome(
            status="cancelled", finished_ts=_now(), error_code="WEIGHT_TRAIN_CANCELLED",
            error_message="用户取消训练。"))
        self._push({"type": "cancelled", "data": {"runId": self.run_id, "finishedTs": _now()}})

    async def _fail(self, code: str, message: str) -> None:
        if self.is_terminal:
            return
        await self._persist(repo.WeightRunOutcome(
            status="failed", finished_ts=_now(), error_code=code, error_message=message))
        self._push({"type": "error", "data": {"code": code, "message": message, "runId": self.run_id}})

    async def _persist(self, outcome: repo.WeightRunOutcome) -> None:
        """一次性 sessionmaker session 落终态（与 ``_backfill_companion_strategies`` 同范式）+ 维护模型指针。"""
        sm = get_sessionmaker()
        async with sm() as session:
            await repo.finalize_weight_run(session, self.run_id, outcome=outcome,
                                    run_cap=self._spec.run_cap)
        self.status = outcome.status
        self.finished_mono = time.monotonic()

    def _emit_phase(self, phase: str, pct: int) -> None:
        self._push({"type": "phase", "data": {"phase": phase, "pct": pct}})


def _now() -> int:
    return int(time.time())


class WeightTrainManager:
    """流式训练作业注册表（进程内单例，job-capped，lifespan 关闭）。"""

    def __init__(self):
        self._jobs: dict[str, _TrainJob] = {}

    def active_count(self) -> int:
        return sum(1 for job in self._jobs.values() if job.status == "running")

    def start_job(self, spec: JobSpec) -> _TrainJob:
        """注册并启动一个流式训练作业（此刻起即开缓冲，R-01 守门已在 service 层全过）。"""
        self._prune_finished()
        job = _TrainJob(uuid.uuid4().hex, spec)
        self._jobs[job.job_id] = job
        job.start()
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
        for job in list(self._jobs.values()):
            job.request_cancel()
            await job.join()
        self._jobs.clear()

    def _prune_finished(self) -> None:
        """淘汰过期终态作业（TTL + 数量上限）；running 作业绝不淘汰（作业与 socket 解耦，续跑可靠）。"""
        now = time.monotonic()
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
