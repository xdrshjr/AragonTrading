"""多模型训练批次运行时（weight-multi-model-training §2.3/§2.4，镜像 ``_TrainJob`` 分层 + 调度泵）。

一个 ``WeightBatchRuntime`` 编排一个批次：在共享规格下把 K 个候选（每个 = 与今天字节级等价的 ``_TrainJob``，
**绝不派生第二训练器**）经 **FIFO 调度泵**逐一起跑——候选与独立训练竞争同一 ``WeightTrainManager`` 名额，
超额候选进队列、名额释放即泵起（``reserve_when_free`` 检查-等待原子闭环，杜绝丢唤醒，R2）。每个候选的
``TrainStreamEvent`` 经**转发协程**打标 ``{runId,method}`` 后并入批次级 fan-out 缓冲（含终态回放，R-06）；
转发协程**见终态即退**（R3，否则子作业终态后 ``q.get()`` 永阻塞致批次悬挂）。全部候选终态即 emit
``batchDone``（含定稿排行榜）。取消 / 关停均协作式收口，绝不留 DB running/queued 泄漏。
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass

from ..db import repositories as repo
from ..db.session import get_sessionmaker
from ..schemas import weight_batch_ranking, weight_batch_status
from .service import FactorWindow
from .weight_train_manager import _TERMINAL_TYPES, JobSpec, WeightTrainManager, _TrainJob

logger = logging.getLogger("aragon")


def _now() -> int:
    return int(time.time())


@dataclass(frozen=True)
class CandidateSpec:
    """一个批次候选的内存态（run_id 已落 queued 记录 + 方法 + 超参变体 config + 显示标签）。

    ``members``（factor-combo-exploration §2.4）：探索候选携各自组合成员；``None`` = 沿用共享
    ``BatchSharedSpec.members``（既有多方法批次全部为 ``None``，零影响）。
    """

    run_id: int
    method: str
    config: dict
    variant_label: str = ""
    members: list | None = None


@dataclass(frozen=True)
class BatchSharedSpec:
    """批次全部候选共享的训练规格（成员 / 标的·周期 / 时间窗 / 前瞻 / 归一化+阈值配置）。"""

    inst_id: str
    bar: str
    start_ts: int
    end_ts: int
    max_bars: int | None
    horizon: int
    members: list
    config: dict
    run_cap: int
    curve_steps: int


class WeightBatchRuntime:
    """单个批次的 fan-out + FIFO 调度泵 + 取消对账（镜像 ``_TrainJob`` 的 subscribe/snapshot/_push 分层）。"""

    def __init__(self, batch_id: int, parent_model_id: int, spec: BatchSharedSpec,
                 candidates: list[CandidateSpec], *, manager: WeightTrainManager, rest: object,
                 clock: Callable[[], float] = time.monotonic):
        self.batch_id = batch_id
        self.parent_model_id = parent_model_id
        self._spec = spec
        self._manager = manager
        self._rest = rest
        self._clock = clock
        self._candidates = candidates
        self._pending: deque[CandidateSpec] = deque(candidates)
        self._jobs: dict[int, _TrainJob] = {}     # run_id → 已起作业（供单候选 / 整批取消）
        self._buffer: list[dict] = []
        self._subscribers: set[asyncio.Queue] = set()
        self._child_tasks: list[asyncio.Task] = []
        self._sched_task: asyncio.Task | None = None
        self._cancelled = False
        self._finished = False
        self.finished_mono: float | None = None

    # ---------- fan-out（镜像 _TrainJob，R-08）----------
    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=512)
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subscribers.discard(q)

    def snapshot(self) -> list[dict]:
        """订阅即全量回放：从初始 roster 到 batchDone 的全部缓冲事件（晚订阅 / 重连无损，R-06）。"""
        return list(self._buffer)

    @property
    def is_terminal(self) -> bool:
        return self._finished

    def _push(self, event: dict) -> None:
        self._buffer.append(event)
        for q in list(self._subscribers):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass

    def _forward(self, cand: CandidateSpec, inner: dict) -> None:
        """把一个候选的内部 ``TrainStreamEvent`` 打标后并入批次流（前端按 runId 路由进「按候选」reducer）。"""
        self._push({"type": "candidate", "data": {
            "runId": cand.run_id, "method": cand.method,
            "variantLabel": cand.variant_label, "event": inner}})

    # ---------- 生命周期 ----------
    def start(self) -> None:
        self._emit_initial_roster()
        self._sched_task = asyncio.create_task(self._scheduler())

    def _emit_initial_roster(self) -> None:
        self._push({"type": "batch", "data": {
            "batchId": self.batch_id, "parentModelId": self.parent_model_id, "status": "running",
            "candidates": [{"runId": c.run_id, "method": c.method,
                            "variantLabel": c.variant_label, "status": "queued"}
                           for c in self._candidates]}})

    async def _scheduler(self) -> None:
        """调度泵协程：FIFO 起跑候选 → join 全部转发协程 → 定稿 batchDone（关停被取消则不 emit）。"""
        try:
            await self._pump()
            await self._await_all_children()
            await self._finalize_batch()
        except asyncio.CancelledError:
            return                                # 关停：不 emit batchDone（进程退出，交给启动对账）
        except Exception as exc:  # noqa: BLE001 —— 调度失败也须收口 batchDone，绝不让批次永不终态
            logger.exception("weight batch %d 调度异常：%s", self.batch_id, exc)
            try:
                await self._finalize_batch()
            except Exception:  # noqa: BLE001
                pass

    async def _pump(self) -> None:
        """FIFO 逐候选：``reserve_when_free`` 锁内预占或等待（防丢唤醒 R2）→ mark_run_running → start_job → attach。"""
        while self._pending and not self._cancelled:
            got = await self._manager.reserve_when_free(lambda: self._cancelled)
            if not got:                           # 仅等待期间被取消才 False（未持名额，安全退出）
                break
            if self._cancelled:                   # 唤醒后即取消：释放裸预占（未 start_job），剩余候选由取消路径收口
                self._manager.release()
                break
            cand = self._pending.popleft()        # 已持一个预占名额 → 必达 start_job（绝不在此间 break，防泄漏）
            await self._start_candidate(cand)

    async def _start_candidate(self, cand: CandidateSpec) -> None:
        try:
            sm = get_sessionmaker()
            async with sm() as session:            # queued→running：预占后唯一一处 DB await（跨窗安全，H-B3）
                await repo.mark_run_running(session, cand.run_id, started_ts=_now())
        except Exception as exc:  # noqa: BLE001 —— 落库异常绝不泄漏裸预占名额、亦不连累其余候选（P1，镜像 start_training）
            self._manager.release()                # 释放裸预占（未达 start_job）：否则名额永久泄漏、全局有效 cap 被削
            logger.warning("weight batch %d 候选 run %d 启动落库失败：%s", self.batch_id, cand.run_id, exc)
            await self._fail_candidate_start(cand)  # 就地判 failed（不悬挂 queued）→ 泵照常起其余候选、批次如期终态
            return
        spec = self._spec
        job = self._manager.start_job(JobSpec(     # 内部 release() 把预占转成 running 计数
            run_id=cand.run_id, model_id=self.parent_model_id, rest=self._rest,
            members=cand.members or spec.members, method=cand.method, config={**spec.config, **cand.config},
            window=FactorWindow(inst_id=spec.inst_id, bar=spec.bar, start_ts=spec.start_ts,
                                end_ts=spec.end_ts, max_bars=spec.max_bars),
            horizon=spec.horizon, run_cap=spec.run_cap, curve_steps=spec.curve_steps))
        self._jobs[cand.run_id] = job
        self._attach(cand, job)
        if self._cancelled:                        # 若在 mark_running await 窗口内被整批取消，补取消刚起的作业
            job.request_cancel()

    async def _fail_candidate_start(self, cand: CandidateSpec) -> None:
        """候选起跑前落库失败：就地 finalize 为 failed（DB + repoint）并转发终态 error 事件，泵继续起其余候选。
        finalize 亦失败（DB 仍不可用）时该 run 留 queued、交启动对账改判，绝不再抛以免连累批次收口。"""
        try:
            sm = get_sessionmaker()
            async with sm() as session:
                await repo.finalize_weight_run(session, cand.run_id, outcome=repo.WeightRunOutcome(
                    status="failed", finished_ts=_now(), error_code="WEIGHT_TRAIN_INTERRUPTED",
                    error_message="候选启动失败（落库异常），已判失败。"), run_cap=self._spec.run_cap)
        except Exception as exc:  # noqa: BLE001
            logger.warning("weight batch %d 候选 run %d 失败落库亦异常，交启动对账：%s",
                           self.batch_id, cand.run_id, exc)
        self._forward(cand, {"type": "error", "data": {
            "code": "WEIGHT_TRAIN_INTERRUPTED", "message": "候选启动失败，已判失败。", "runId": cand.run_id}})

    def _attach(self, cand: CandidateSpec, job: _TrainJob) -> None:
        self._child_tasks.append(asyncio.create_task(self._forward_job(cand, job)))

    async def _forward_job(self, cand: CandidateSpec, job: _TrainJob) -> None:
        """转发协程：回放子作业缓冲（含终态）→ 增量转发直到终态即退（R3，对齐 ws_weight_train「终态即关」）。"""
        q = job.subscribe()
        snapshot = job.snapshot()                  # subscribe 与 snapshot 间无 await：原子，snapshot 为前缀不重复
        for event in snapshot:
            self._forward(cand, event)
        if snapshot and snapshot[-1].get("type") in _TERMINAL_TYPES:
            job.unsubscribe(q)                     # 快作业 / 订阅晚于终态：队列不会再来事件，直接收尾
            return
        try:
            while True:
                event = await q.get()
                self._forward(cand, event)
                if event.get("type") in _TERMINAL_TYPES:
                    break                          # 终态即退，否则 q.get() 永阻塞 → 批次悬挂（R3）
        finally:
            job.unsubscribe(q)

    async def _await_all_children(self) -> None:
        if self._child_tasks:
            await asyncio.gather(*self._child_tasks, return_exceptions=True)

    async def _finalize_batch(self) -> None:
        if self._finished:
            return
        sm = get_sessionmaker()
        async with sm() as session:
            runs = await repo.list_batch_runs(session, self.batch_id)
        self._push({"type": "batchDone", "data": {
            "batchId": self.batch_id, "finishedTs": _now(),
            "ranking": weight_batch_ranking(runs)}})
        self._finished = True
        self.finished_mono = self._clock()
        logger.info("weight batch %d 完成：status=%s 候选=%d", self.batch_id,
                    weight_batch_status(runs), len(runs))

    # ---------- 取消 ----------
    async def request_cancel(self) -> None:
        """整批取消（§2.3）：置 ``_cancelled`` → 对全部 running 子作业置取消标志 → 对全部 queued 候选就地
        finalize cancelled → 显式 ``notify_slot_free`` 唤醒卡在 ``reserve_when_free`` 的泵（泵醒即退）。"""
        if self._finished:
            return
        self._cancelled = True
        for job in list(self._jobs.values()):
            job.request_cancel()                   # 已终态幂等无副作用
        await self._finalize_pending_cancelled()   # queued（未起作业）→ cancelled，绝不留 DB 泄漏
        self._manager.notify_slot_free()           # 唤醒泵（见 cancelled()=真即退，不再起新候选）

    async def _finalize_pending_cancelled(self) -> None:
        while self._pending:
            await self._finalize_candidate_cancelled(self._pending.popleft())

    async def _finalize_candidate_cancelled(self, cand: CandidateSpec) -> None:
        """把一个尚未起作业的 queued 候选就地收口为 cancelled（DB finalize + 合成 cancelled 事件转发）。"""
        sm = get_sessionmaker()
        async with sm() as session:
            await repo.finalize_weight_run(session, cand.run_id, outcome=repo.WeightRunOutcome(
                status="cancelled", finished_ts=_now(), error_code="WEIGHT_TRAIN_CANCELLED",
                error_message="批次取消，候选未启动即取消。"), run_cap=self._spec.run_cap)
        self._forward(cand, {"type": "cancelled", "data": {
            "runId": cand.run_id, "finishedTs": _now()}})

    async def cancel_candidate(self, run_id: int) -> str:
        """取消单个候选；返回 'cancelling' | 'finished' | 'unknown'（供路由映射 202/200/404）。"""
        job = self._jobs.get(run_id)
        if job is not None:
            return self._manager.cancel(job.job_id)      # running → 协作式；已终态 → finished
        for cand in list(self._pending):                 # 仍 queued（未起作业）→ 就地 finalize cancelled
            if cand.run_id == run_id:
                self._pending.remove(cand)
                await self._finalize_candidate_cancelled(cand)
                return "cancelling"
        return "unknown"

    # ---------- 关停（镜像 WeightTrainManager.shutdown 的批次侧）----------
    async def aclose(self) -> None:
        """关停：置取消标志 + 取消调度 / 转发协程（底层 _TrainJob 由 WeightTrainManager.shutdown 收口）。"""
        self._cancelled = True
        if self._sched_task is not None and not self._sched_task.done():
            self._sched_task.cancel()
            try:
                await self._sched_task
            except asyncio.CancelledError:
                pass
        for task in self._child_tasks:
            if not task.done():
                task.cancel()
        if self._child_tasks:
            await asyncio.gather(*self._child_tasks, return_exceptions=True)
