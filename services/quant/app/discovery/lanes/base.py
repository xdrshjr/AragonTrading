"""Lane 抽象与共用装配（discovery-strategy-axes §2.2）。

Lane = **一条轴的「生成 + 审查 + 验证 + 池」四件事打成一个对象**；``runtime`` 只负责
「调度谁、给多少名额、把结果落库和扇出」。三个理由：① ``runtime.py`` 已逼近仓库 800 行红线；
② 因子轴与策略轴的验证实现差异太大（统计 vs 回测），塞进同一个函数只会长出一串 ``if kind==``；
③ Lane 边界让 BL-8 的等价门禁可执行——因子 Lane 是从 ``runtime`` **逐行平移**过去的。

**BL-14**：``tested`` / ``skeleton_stats`` / ``bans`` 一律由各自的 Lane 持有（逐轴），
runtime 只保留一个用于 UI 汇总的**只读求和**。跨轴共享一个分母既在统计上错误，也会让已发布
的因子轴门槛被新轴的候选抬高。
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from fastapi import HTTPException
from fastapi.concurrency import run_in_threadpool

from .. import repositories as repo
from ..validation import ValidationOutcome


@dataclass(frozen=True)
class LaneContext:
    """一轮验证所需的全部只读输入（每轮构造一次，跨候选复用）。"""

    df_full: object
    candles: list          # 与 ``df_full`` 同源同序（策略轴要原始 Candle 喂 run_backtest）
    htf_frames: dict       # ``{htf_bar: Candle 列表}``，见 §2.4
    bar: str
    split: int             # 唯一切点，来自 ``validation.split_index``（同源律）
    split_ts: int
    config: dict
    settings: object
    memo: dict = field(default_factory=dict)


@runtime_checkable
class Lane(Protocol):
    """一条轴的四件事 + 池管理（``runtime`` 只经这个协议与各轴打交道）。"""

    kind: str

    async def produce(self, rng, *, budget: dict, count: int, tally):
        """生成本轮候选，返回 ``(候选列表, LLM 调用产物)``。"""

    def review(self, candidates: list):
        """确定性规则链，返回 ``(通过者, [(被拒者, 拒绝码)])``。"""

    def validate(self, candidate, ctx: LaneContext, trials: int) -> ValidationOutcome:
        """单候选验证（同步、CPU 密集；由 ``run_validation`` 送进线程池）。"""

    def absorb(self, candidate, outcome, tally) -> None:
        """把单候选结果并入本轴状态（kept 立刻进池，供同轮后续候选去重）。"""

    def pool_size(self) -> int:
        """本轴 kept 池规模（FSA 最小池护栏与 UI 共用）。"""

    def snapshot(self) -> dict:
        """进 ``state`` 事件的本轴摘要。"""


def reject_reason_of(exc: Exception) -> str:
    """异常 → 拒绝码。

    评审 P2-8：``build_members`` / ``validate_members`` / ``validate_combo_params`` 抛的是
    ``fastapi.HTTPException`` 而非 ``ValueError``，收口成 ``eval_error:HTTPException`` 可读性
    极差；单独映射为 ``S0:assemble_rejected:<code>``。
    """
    if isinstance(exc, HTTPException):
        detail = exc.detail if isinstance(exc.detail, dict) else {}
        return f"S0:assemble_rejected:{detail.get('code', 'HTTP')}"
    return f"eval_error:{type(exc).__name__}"


async def run_validation(lane: Lane, candidate, ctx: LaneContext, *, trials: int,
                         semaphore: asyncio.Semaphore, timeout_s: float) -> ValidationOutcome:
    """单候选验证的**唯一**执行壳：信号量 → 线程池 → 单候选超时 → 异常收口。

    信号量与线程池和所有 API 请求共用，无节制并发会让交互请求变慢，用户只会感觉「整个 App
    变卡」。单候选异常绝不拖垮整轮（沿用 ``mine_factors`` 纪律）。
    """
    try:
        async with semaphore:
            return await asyncio.wait_for(
                run_in_threadpool(lane.validate, candidate, ctx, trials), timeout=timeout_s)
    except asyncio.TimeoutError:
        return ValidationOutcome(verdict="rejected", reject_reason="validate_timeout",
                                 direction="long", metrics={}, series={})
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001
        return ValidationOutcome(verdict="rejected", reject_reason=reject_reason_of(exc)[:64],
                                 direction="long", metrics={}, series={})


def rejected_row(lane: Lane, candidate, reason: str, started_ts: int) -> repo.CandidateRow:
    """规则 / LLM 拒绝的候选行（无指标，只留拒绝码）。"""
    return repo.CandidateRow(
        kind=candidate.kind, genome_hash=candidate.genome_hash,
        genome=lane.genome_payload(candidate), expr_text=candidate.expr_text,
        describe_text=candidate.describe, skeleton=candidate.skeleton,
        mechanism=candidate.mechanism, source=candidate.source,
        verdict="rejected", reject_reason=reason, created_ts=started_ts)


def outcome_row(lane: Lane, candidate, outcome, started_ts: int) -> repo.CandidateRow:
    """验证完成的候选行（含全量指标与有界序列）。"""
    return repo.CandidateRow(
        kind=candidate.kind, genome_hash=candidate.genome_hash,
        genome=lane.genome_payload(candidate), expr_text=candidate.expr_text,
        describe_text=candidate.describe, skeleton=candidate.skeleton,
        mechanism=candidate.mechanism, source=candidate.source,
        verdict=outcome.verdict, reject_reason=outcome.reject_reason,
        metrics=outcome.metrics, series=outcome.series,
        trials_at_accept=int(outcome.metrics.get("trials", 0) or 0), created_ts=started_ts)


def trim_pool(pool: list, cap: int) -> None:
    """有界精英池：到顶后只保留分数最高的 ``cap`` 条（**逐轴**上限，D-13）。"""
    limit = max(1, int(cap))
    if len(pool) <= limit:
        return
    pool.sort(key=lambda entry: -float(getattr(entry, "score", 0.0) or 0.0))
    del pool[limit:]
