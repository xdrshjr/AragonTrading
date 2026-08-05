"""单轮迭代编排（discovery-strategy-axes §2.9）—— 四步时序的多轴展开。

自 ``runtime.py`` 拆出（该文件已超 800 行清洁度上限）：``DiscoveryRun`` 只保留生命周期、
取数、池重建、调度与终态；「一轮怎么跑」全部收口在这里。

四步与上游完全同构，只在第 ①③ 步内部按轴展开：
```
① 生成：逐轴 lane.produce(五维预算, 本轴名额)
② 审查：逐轴规则链 → **一次**合并的 LLM 抽样精判（R-9：多轴不得让调用翻四倍）
③ 验证：**轮转**逐候选，共用同一墙钟预算 / 同一信号量 / 同一回测硬顶
④ 提交：合并所有轴的候选行 + 逐轴 tally → 单事务落库 → 事件扇出
```
"""
from __future__ import annotations

import asyncio
import logging
import random
import time

from ..db.session import get_sessionmaker
from . import assets
from . import repositories as repo
from .axis_budget import plan_axis_budget, yield_sentence
from .checkpoint import bump_reject, dumps, iteration_seed
from .evolution import BudgetCounters, adapt_budget, cold_start_budget, normalize_budget
from .fsa import merge_bans, prune_expired, prune_skeleton_stats, scan_skeletons, update_skeleton_stats
from .iteration_stats import (
    IterationTally,
    candidate_event,
    iteration_note,
    merged_tally,
    per_axis_stats,
    rate,
    tally_to_stats,
)
from .lanes import outcome_row, rejected_row, run_validation
from .llm_agents import LlmOutcome, accepted_hashes
from .mechanisms import uncovered_families

logger = logging.getLogger("aragon")

PERSIST_RETRY_DELAY = 0.2      # 瞬态 SQLite 锁多在数百 ms 内自清，单次有界重试
MAX_CANDIDATE_EVENTS = 20      # 每轮下发的候选事件条数上限（kept 优先）


class IterationRunner:
    """跑完一轮并落库；每轮**新建一个**实例，无跨轮状态（跨轮状态都在 run 与 Lane 上）。"""

    def __init__(self, run) -> None:
        self._run = run
        self._pending: dict = {}

    # ============================ 主时序 ============================
    async def run_once(self) -> None:
        run = self._run
        started = int(time.time())
        started_mono = time.monotonic()
        index = int(run._checkpoint.iteration) + 1
        rng = random.Random(iteration_seed(run._checkpoint.rng_seed, index))
        budget = self._plan_budget(index)
        for lane in run._lanes.values():
            lane.iteration = int(run._checkpoint.iteration)
            if hasattr(lane, "backtests"):
                lane.backtests = 0          # 回测硬顶按**轮**计（§六的计数单位）
        run.sync_axis_inputs()
        plan = plan_axis_budget(
            run.axes(), run.spec.config.get("axisWeights") or {},
            int(run.spec.config.get("candidatesPerIteration", 40)),
            {axis: _readiness(lane) for axis, lane in run._lanes.items()})
        tallies = {axis: IterationTally() for axis in run._lanes}
        rows, llm_outcome = await self._produce_and_review(rng, budget, plan, tallies, started)
        rows += await self._validate_round_robin(tallies, started, started_mono)
        total = merged_tally(tallies)
        total.overrun = (time.monotonic() - started_mono) > float(
            run.spec.config.get("iterationIntervalS", 300))
        if plan.yielded:
            total.skipped = total.skipped or "；".join(
                yield_sentence(axis, entry) for axis, entry in plan.yielded.items())
        elapsed_ms = int((time.monotonic() - started_mono) * 1000)
        await self._commit(index, rows, total, tallies, plan, budget, llm_outcome, started,
                           elapsed_ms)

    # ============================ ① 生成 + ② 审查 ============================
    async def _produce_and_review(self, rng, budget: dict, plan, tallies: dict,
                                  started: int) -> tuple[list, object]:
        run = self._run
        llm_outcome = None
        rows: list = []
        for axis, slots in plan.shares.items():
            lane = run._lanes[axis]
            if slots <= 0 or run._cancel:
                self._pending[axis] = []
                continue
            candidates, outcome = await lane.produce(rng, budget=budget, count=int(slots),
                                                     tally=tallies[axis])
            llm_outcome = _merge_llm(llm_outcome, outcome)
            passed, rejected = lane.review(candidates)
            tallies[axis].rule_rejected = len(rejected)
            rows += self._reject_rows(lane, rejected, started)
            self._pending[axis] = passed
        llm_outcome = llm_outcome or LlmOutcome()
        vetoed = await self._llm_review(llm_outcome)
        for axis, rejected in vetoed.items():
            tallies[axis].llm_rejected = len(rejected)
            rows += self._reject_rows(run._lanes[axis], rejected, started)
        return rows, llm_outcome

    def _reject_rows(self, lane, rejected: list, started_ts: int) -> list:
        rows = []
        for candidate, reason in rejected:
            lane.mark_tested(candidate.genome_hash)
            bump_reject(self._run._counters, reason.split(":")[0])
            rows.append(rejected_row(lane, candidate, reason, started_ts))
        return rows

    async def _llm_review(self, generate_outcome) -> dict:
        """抽样交审查子代理（**全轴合并成一次调用**，R-9）；fail-open：降级即视为全部 accept。

        抽样**跨轴轮转**：审查 prompt 已按 ``kind`` 分出「表达式」与「策略」两套判据，若还按
        轴序取前 N 条，只要因子轴产得够多，三条策略轴就一条也抽不到——那套判据成了死代码。
        """
        run = self._run
        sample_size = int((run.spec.config.get("llm") or {}).get("reviewSample", 5))
        sample = _sample_across_axes(self._pending, sample_size)
        if not sample or not run._llm.enabled:
            return {}
        outcome = await run._llm.review({
            "candidates": [{"hash": c.genome_hash, "expr": c.expr_text, "kind": c.kind,
                            "describe": c.describe, "mechanism": c.mechanism}
                           for c in sample],
            "mechanismCoverage": run._checkpoint.mechanism_coverage})
        generate_outcome.calls += outcome.calls
        if outcome.degraded and not generate_outcome.degraded:
            generate_outcome.degraded = True
            generate_outcome.degraded_reason = outcome.degraded_reason
        vetoed, reasons = accepted_hashes(outcome, [c.genome_hash for c in sample])
        if not vetoed:
            return {}
        out: dict = {}
        for axis, items in self._pending.items():
            rejected = [(c, f"llm_review:{reasons.get(c.genome_hash, '')}"[:64])
                        for c in items if c.genome_hash in vetoed]
            if rejected:
                out[axis] = rejected
                self._pending[axis] = [c for c in items if c.genome_hash not in vetoed]
        return out

    # ============================ ③ 验证（轮转） ============================
    async def _validate_round_robin(self, tallies: dict, started_ts: int,
                                    started_mono: float) -> list:
        """**轮转**取候选：不按轴串行——否则一个慢轴会吃光整轮墙钟，后面的轴一条都验不上。"""
        run = self._run
        budget_s = float(run.spec.settings.discovery_iteration_timeout_s)
        cap = int(run.spec.settings.discovery_max_backtests_per_iteration)
        queues = {axis: list(items) for axis, items in self._pending.items() if items}
        rows: list = []
        while queues and not run._cancel:
            for axis in list(queues):
                if not queues[axis]:
                    queues.pop(axis)
                    continue
                if time.monotonic() - started_mono > budget_s:
                    _truncate(queues, tallies, "timeout")
                    return rows
                if axis != "factor" and self._backtests_used() >= cap:
                    _truncate({axis: queues[axis]}, tallies, "backtest_cap")
                    queues.pop(axis)
                    continue
                rows.append(await self._validate_one(axis, queues[axis].pop(0), tallies,
                                                     started_ts))
        return rows

    def _backtests_used(self) -> int:
        return sum(int(getattr(lane, "backtests", 0)) for lane in self._run._lanes.values())

    async def _validate_one(self, axis: str, candidate, tallies: dict, started_ts: int):
        run = self._run
        lane = run._lanes[axis]
        settings = run.spec.settings
        timeout_s = float(settings.discovery_candidate_timeout_s if axis == "factor"
                          else settings.discovery_backtest_timeout_s)
        outcome = await run_validation(lane, candidate, run.lane_context(),
                                       trials=lane.tested + 1,
                                       semaphore=run._validate_sem, timeout_s=timeout_s)
        tallies[axis].validated += 1
        lane.tested += 1
        lane.mark_tested(candidate.genome_hash)
        update_skeleton_stats(lane.skeleton_stats, candidate.skeleton,
                              kept=outcome.verdict == "kept")
        lane.absorb(candidate, outcome, tallies[axis])
        if outcome.verdict == "kept":
            run._counters["kept"] = int(run._counters.get("kept", 0)) + 1
        else:
            reason = outcome.reject_reason or "unknown"
            tallies[axis].count_reject(reason)
            bump_reject(run._counters, reason)
        run._counters["tested"] = int(run._counters.get("tested", 0)) + 1
        return outcome_row(lane, candidate, outcome, started_ts)

    # ============================ ④ 提交 ============================
    async def _commit(self, index: int, rows: list, tally: IterationTally, tallies: dict,
                      plan, budget: dict, llm_outcome, started_ts: int,
                      elapsed_ms: int) -> None:
        """单事务写候选 + 迭代 + 检查点，再扇出事件（落库失败绝不吞，转 failed 终态）。"""
        run = self._run
        checkpoint = run._checkpoint
        self._run_fsa(index, tally)
        checkpoint.iteration = index
        checkpoint.budget = normalize_budget(budget)
        checkpoint.momentum = run._momentum.to_list()
        checkpoint.axis_shares = dict(plan.shares)
        self._sync_axis_checkpoint()
        checkpoint.kept_rate_trend = (
            list(checkpoint.kept_rate_trend) + [rate(tally.kept, tally.validated)])[-3:]
        run._counters["llmCalls"] = int(run._counters.get("llmCalls", 0)) + llm_outcome.calls
        iteration_row = repo.IterationRow(
            idx=index, started_ts=started_ts, finished_ts=int(time.time()), budget=budget,
            stats={**tally_to_stats(tally), "durationMs": elapsed_ms,
                   "perAxis": per_axis_stats(tallies), "axisShares": dict(plan.shares),
                   "yieldedByPool": dict(plan.yielded)},
            llm={"calls": llm_outcome.calls, "latencyMs": llm_outcome.latency_ms,
                 "degraded": llm_outcome.degraded,
                 "degradedReason": llm_outcome.degraded_reason,
                 "promptChars": llm_outcome.prompt_chars},
            note=iteration_note(tally))
        pruned = await self._persist(iteration_row, rows)
        for key in ("prunedCandidates", "prunedIterations"):
            run._counters[key] = int(run._counters.get(key, 0)) + int(pruned.get(key, 0))
        await self._register_assets(index, rows)
        self._emit(iteration_row, rows)

    async def _register_assets(self, index: int, rows: list) -> None:
        """把本轮 kept 候选登记进成果资产台账（discovery-asset-ledger §2.2.2 · AL-5）。

        位置必须精确：**在 ``_persist`` 之后**（候选行才有 id，``sources_json`` 要记
        ``candidateId``）、**不在 ``_persist`` 内部**（那里有一次有界重试，登记抛错会把已提交
        成功的 ``commit_iteration`` 再跑一遍）、**在 ``_emit`` 之前**（两个计数才搭得上同一轮的
        ``state`` 事件）。

        两个计数**落库晚一轮**——``counters_json`` 是在下一轮的 ``commit_iteration`` 里写的。
        这是有意接受的代价（换取不动它的单事务语义）：看到「刚登记完刷新页面计数是 0」不是 bug。
        """
        run = self._run
        kept_rows = [row for row in rows if row.verdict == "kept"]
        if not kept_rows:
            return
        try:
            stats = await assets.register_kept(run.spec, rows=kept_rows,
                                               window=assets.MinedWindow.of(run),
                                               iteration_idx=index)
            run._counters["ledgerRegistered"] = (
                int(run._counters.get("ledgerRegistered", 0)) + stats.created + stats.merged)
        except Exception as exc:  # noqa: BLE001 —— 台账是观测面，绝不拖垮迭代（fail-open）
            run._counters["ledgerErrors"] = int(run._counters.get("ledgerErrors", 0)) + 1
            logger.warning("asset ledger register failed for run %d iter %d: %s",
                           run.run_id, index, exc)

    def _sync_axis_checkpoint(self) -> None:
        """把逐轴计数写回检查点（BL-14：三者一律逐轴，且各自过 256 条上界）。"""
        run = self._run
        lanes = run._lanes
        checkpoint = run._checkpoint
        checkpoint.pool_sizes = {**checkpoint.pool_sizes,
                                 **{axis: lane.pool_size() for axis, lane in lanes.items()}}
        checkpoint.tested_by_axis = {axis: int(lane.tested) for axis, lane in lanes.items()}
        checkpoint.skeleton_stats_by_axis = {
            axis: prune_skeleton_stats(lane.skeleton_stats) for axis, lane in lanes.items()}
        checkpoint.fsa_bans_by_axis = {
            axis: [ban.to_dict() for ban in lane.bans] for axis, lane in lanes.items()}
        factor = lanes.get("factor")
        if factor is not None:                 # 老字段保持可读（UI 与旧检查点兼容）
            checkpoint.skeleton_stats = prune_skeleton_stats(factor.skeleton_stats)
            checkpoint.fsa_bans = [ban.to_dict() for ban in factor.bans]

    def _run_fsa(self, index: int, tally: IterationTally) -> None:
        """逐轴各扫一次骨架频次；池子太小的轴整轮跳过并**留痕**（BL-10 后半）。

        池里只有 3 条时每条都占 33% > 15%，会被**全部**冻结——回测型的三条策略轴入库速度
        比因子轴低一到两个数量级，早期必然踩中。
        """
        run = self._run
        evolution = run.spec.config.get("evolution") or {}
        every = max(1, int(evolution.get("fsaEvery", 25)))
        if index % every != 0:
            return
        ttl = int(evolution.get("fsaBanTtlIters", 150))
        floor = int(run.spec.settings.discovery_fsa_min_pool)
        base = float(evolution.get("fsaThreshold", 0.15))
        skipped: list[str] = []
        frozen = 0
        for axis, lane in run._lanes.items():
            if lane.pool_size() < floor:
                skipped.append(axis)
                continue
            threshold = lane.fsa_threshold(base) if hasattr(lane, "fsa_threshold") else base
            fresh = scan_skeletons([entry.skeleton for entry in lane.pool],
                                   iteration=index, threshold=threshold)
            if fresh:
                lane.bans = merge_bans(lane.bans, fresh)
                frozen += len(fresh)
            lane.bans = prune_expired(lane.bans, iteration=index, ttl_iters=ttl)
        if frozen:
            tally.skipped = tally.skipped or f"fsa_frozen:{frozen}"
        elif skipped:
            tally.skipped = tally.skipped or f"fsa_skipped:{','.join(skipped)}"

    async def _persist(self, iteration_row, rows: list) -> dict:
        """落库（有界重试一次）；两次都失败即抛，由 ``_run`` 收口为 ``failed`` 终态。"""
        run = self._run
        settings = run.spec.settings
        for attempt in range(2):
            try:
                sm = get_sessionmaker()
                async with sm() as session:
                    return await repo.commit_iteration(
                        session, run.run_id, candidates=rows, iteration=iteration_row,
                        checkpoint_json=dumps(run._checkpoint), counters=run._counters,
                        candidate_row_cap=int(settings.discovery_max_candidate_rows),
                        iteration_row_cap=int(settings.discovery_max_iteration_rows))
            except Exception as exc:  # noqa: BLE001
                logger.warning("discovery run %d 第 %d 轮落库失败（尝试 %d）：%s",
                               run.run_id, iteration_row.idx, attempt + 1, exc)
                if attempt == 0:
                    await asyncio.sleep(PERSIST_RETRY_DELAY)
                else:
                    raise
        return {}

    def _emit(self, iteration_row, rows: list) -> None:
        run = self._run
        run._push({"type": "iteration", "data": {
            "index": iteration_row.idx, "startedTs": iteration_row.started_ts,
            "finishedTs": iteration_row.finished_ts, "budget": iteration_row.budget,
            **iteration_row.stats, "llm": iteration_row.llm, "note": iteration_row.note}})
        kept = [row for row in rows if row.verdict == "kept"]
        others = [row for row in rows if row.verdict != "kept"]
        for row in (kept + others)[:MAX_CANDIDATE_EVENTS]:
            run._push({"type": "candidate", "data": candidate_event(row)})
        run._push({"type": "state", "data": run.state_snapshot()})

    # ============================ 预算 ============================
    def _plan_budget(self, index: int) -> dict:
        """冷启动 → 固定预算；此后每 ``adaptEvery`` 轮跑一次纯函数 ``adapt_budget``。"""
        run = self._run
        evolution = run.spec.config.get("evolution") or {}
        pool = sum(lane.pool_size() for lane in run._lanes.values())
        if pool < int(evolution.get("coldStartMin", 5)):
            return cold_start_budget()
        current = normalize_budget(run._checkpoint.budget)
        every = max(1, int(evolution.get("adaptEvery", 10)))
        if index % every != 0:
            return current
        return adapt_budget(current, self._budget_counters(index))

    def _budget_counters(self, index: int) -> BudgetCounters:
        run = self._run
        rejected = run._counters.get("rejected") or {}
        total_rejected = sum(int(v or 0) for v in rejected.values()) or 1
        tested = max(1, int(run._counters.get("tested", 0)))
        coverage = run._checkpoint.mechanism_coverage
        return BudgetCounters(
            near_kept_rate=float(run._counters.get("nearKeptRate", 0.0) or 0.0),
            global_kept_rate=int(run._counters.get("kept", 0)) / tested,
            dedupe_reject_rate=int(rejected.get("F10", 0)) / total_rejected,
            uncovered_families=len(uncovered_families(coverage)),
            covered_families=sum(1 for v in coverage.values() if int(v or 0) > 0),
            iteration=index,
            kept_rate_trend=tuple(run._checkpoint.kept_rate_trend))


# ============================ 模块级纯函数 ============================
def _readiness(lane) -> tuple[bool, str]:
    return lane.ready() if hasattr(lane, "ready") else (True, "")


def _sample_across_axes(pending: dict, size: int) -> list:
    """跨轴轮转抽样（单轴时与「取前 N 条」逐条相同，故因子轴行为不变，BL-8）。"""
    limit = max(0, int(size))
    queues = [list(items) for items in pending.values() if items]
    picked: list = []
    index = 0
    while queues and len(picked) < limit:
        slot = index % len(queues)
        picked.append(queues[slot].pop(0))
        if not queues[slot]:
            queues.pop(slot)
        else:
            index += 1
    return picked


def _truncate(queues: dict, tallies: dict, cause: str) -> None:
    """截断如实计数：墙钟到点与回测触顶是两件事，混成一个数用户就分不清该调哪个旋钮。"""
    for axis, items in queues.items():
        if cause == "timeout":
            tallies[axis].truncated_by_timeout += len(items)
        else:
            tallies[axis].truncated_by_backtest_cap += len(items)


def _merge_llm(current, outcome):
    if current is None:
        return outcome
    if outcome is None:
        return current
    current.calls += outcome.calls
    if outcome.degraded and not current.degraded:
        current.degraded = True
        current.degraded_reason = outcome.degraded_reason
    return current
