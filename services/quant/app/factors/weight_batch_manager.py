"""多模型训练批次管理器（weight-multi-model-training §3.1，镜像 ``WeightTrainManager`` 注册表）。

进程内单例 ``WeightBatchManager`` **只持批次运行时注册表**（``WeightBatchRuntime`` 承载 fan-out + 调度泵，
见 ``weight_batch_runtime.py``）。终态批次按 TTL + 数量上限回收（复用 ``main._weight_train_sweeper`` 周期
清扫）；关停先停批次泵、再由 ``WeightTrainManager.shutdown`` 收口底层作业（``main.lifespan`` 保证顺序）。
"""
from __future__ import annotations

import logging
import time
from collections.abc import Callable

from .weight_batch_runtime import WeightBatchRuntime

logger = logging.getLogger("aragon")

_FINISHED_TTL_SECONDS = 600.0   # 终态批次在注册表保留时长（供晚订阅 / 重连回放后回收）
_FINISHED_BATCHES_CAP = 32      # 终态批次保留数上限（超出按完成时间淘汰最旧）


class WeightBatchManager:
    """批次运行时注册表（进程内单例，TTL/数量回收，lifespan 关闭）。"""

    def __init__(self, *, clock: Callable[[], float] = time.monotonic):
        self._runtimes: dict[int, WeightBatchRuntime] = {}
        self._clock = clock

    def register(self, runtime: WeightBatchRuntime) -> WeightBatchRuntime:
        """登记并启动一个批次运行时（此刻起即开缓冲 + 调度泵；service 层守门已全过）。"""
        self._prune_finished()
        self._runtimes[runtime.batch_id] = runtime
        runtime.start()
        return runtime

    def get(self, batch_id: int) -> WeightBatchRuntime | None:
        return self._runtimes.get(batch_id)

    def discard(self, batch_id: int) -> None:
        """删批次后同步移除内存运行时（否则删后 WS 重连会回放已删批次的陈旧缓冲，直到 TTL 才回收）。
        删除守门已保证无 running/queued 候选、批次终态，故直接移除即可（对齐 ``_prune_finished`` 的 pop）。"""
        self._runtimes.pop(batch_id, None)

    async def cancel_batch(self, batch_id: int) -> bool:
        """整批取消；批次不在注册表（已回收 / 从未在本进程）→ ``False``（路由据此判 200 幂等 vs 404）。"""
        runtime = self._runtimes.get(batch_id)
        if runtime is None:
            return False
        await runtime.request_cancel()
        return True

    async def cancel_candidate(self, batch_id: int, run_id: int) -> str:
        """取消某批次单候选；批次不在注册表 → ``"unknown"``。"""
        runtime = self._runtimes.get(batch_id)
        if runtime is None:
            return "unknown"
        return await runtime.cancel_candidate(run_id)

    async def shutdown(self) -> None:
        """关停：逐个 ``aclose`` 批次运行时（停调度 / 转发协程）；底层 _TrainJob 由 WeightTrainManager 收口。"""
        for runtime in list(self._runtimes.values()):
            try:
                await runtime.aclose()
            except Exception as exc:  # noqa: BLE001 —— 单个批次关停失败不得阻塞整体关停
                logger.warning("weight batch %d 关停失败：%s", runtime.batch_id, exc)
        self._runtimes.clear()

    def _prune_finished(self) -> None:
        """淘汰过期终态批次（TTL + 数量上限）；running 批次绝不淘汰（调度泵仍在跑，WS 可续显）。"""
        now = self._clock()
        for batch_id, runtime in list(self._runtimes.items()):
            if (runtime.is_terminal and runtime.finished_mono is not None
                    and now - runtime.finished_mono > _FINISHED_TTL_SECONDS):
                self._runtimes.pop(batch_id, None)
        finished = [(bid, rt) for bid, rt in self._runtimes.items()
                    if rt.is_terminal and rt.finished_mono is not None]
        if len(finished) > _FINISHED_BATCHES_CAP:
            finished.sort(key=lambda item: item[1].finished_mono or 0.0)
            for bid, _ in finished[:len(finished) - _FINISHED_BATCHES_CAP]:
                self._runtimes.pop(bid, None)


_manager: WeightBatchManager | None = None


def get_weight_batch_manager() -> WeightBatchManager:
    global _manager
    if _manager is None:
        _manager = WeightBatchManager()
    return _manager
