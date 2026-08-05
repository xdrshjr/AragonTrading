"""发现循环运行时（strategy-factor-discovery §2.9）—— 24/7 长跑的四步迭代。

四条纪律全部照抄仓库既有范式（``WeightTrainManager`` / ``ai_auto`` 会话），不得省略：
1. **CPU 密集全走 ``run_in_threadpool``**（表达式求值 / 评估），绝不阻塞事件循环；且必须先拿到
   ``discovery_validate_concurrency`` 信号量——线程池与所有 API 请求共用，无节制并发会让交互
   请求变慢，用户只会感觉「整个 App 变卡」（R-16）。
2. **每个 run 必达终态**；落库失败也先置终态再重试一次，绝不让 WS 永久悬挂。
3. **协作式取消**：四步之间与每个候选之间检查标志。
4. **绝不合成数据**：OKX 不可达就退避、连续失败转 ``paused(data_unavailable)``，
   **绝不**用旧数据假装新数据。
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from dataclasses import dataclass

from fastapi import HTTPException
from fastapi.concurrency import run_in_threadpool

from ..agent.services import load_range_candles
from ..config import Settings
from ..db.session import get_sessionmaker
from ..engine.backtester import candles_to_df
from ..engine.mtf import bar_to_seconds
from ..okx.rest_client import OKXError
from . import repositories as repo
from .checkpoint import Checkpoint, bans_from_checkpoint, bump_reject, dumps, iteration_seed
from .evolution import (
    BudgetCounters,
    MomentumTracker,
    adapt_budget,
    cold_start_budget,
    normalize_budget,
)
from .fsa import (
    active_bans,
    merge_bans,
    prune_expired,
    prune_skeleton_stats,
    scan_skeletons,
    update_skeleton_stats,
)
from .generation import CandidateFactory
from .iteration_stats import (
    IterationTally,
    candidate_event,
    iteration_note,
    rate,
    tally_to_stats,
)
from .llm_agents import DiscoveryLlmAgents, accepted_hashes
from .mechanisms import uncovered_families
from .review import ReviewContext, filter_rules, review_values
from .searchers import FactorSearcher
from .validation import (
    KeptEntry,
    KeptRow,
    ValidationContext,
    normalize_thresholds,
    rebuild_kept_pool,
    validate_candidate,
)

logger = logging.getLogger("aragon")

_TERMINAL_STATUSES = ("stopped", "succeeded", "failed")
_PERSIST_RETRY_DELAY = 0.2      # 瞬态 SQLite 锁多在数百 ms 内自清，单次有界重试
_DATA_RETRY_MAX_S = 900.0       # 取数失败的指数退避上限
_DATA_FAILURE_LIMIT = 12        # 连续失败上限，超出转 paused(data_unavailable)
_MAX_CANDIDATE_EVENTS = 20      # 每轮下发的候选事件条数上限（kept 优先）
_PAUSE_POLL_S = 0.5


@dataclass(frozen=True)
class RunSpec:
    """启动一个发现循环的全部输入（聚合传参）。"""

    run_id: int
    inst_id: str
    bar: str
    symbols: tuple[str, ...]
    config: dict
    rest: object
    settings: Settings
    resume: bool = False
    llm_client: object | None = None


class DiscoveryRun:
    """单个挖掘任务的运行时：四步循环 + 调度 + 事件扇出 + 协作式取消 + 必达终态。"""

    def __init__(self, spec: RunSpec, *, on_terminal=None):
        self.spec = spec
        self.run_id = spec.run_id
        self.status = "running"
        self.pause_reason = ""
        self._on_terminal = on_terminal
        self._ring: list[dict] = []
        self._terminal_event: dict | None = None
        self._subscribers: set[asyncio.Queue] = set()
        self._cancel = False
        self._paused = False
        self._task: asyncio.Task | None = None
        self._checkpoint = Checkpoint()
        self._counters: dict = {"tested": 0, "kept": 0, "rejected": {}, "llmCalls": 0}
        self._tested: set[str] = set()      # 已测哈希（续跑时由 DB 唯一索引一次性载入，D-3）
        self._pool: list[KeptEntry] = []
        self._df = None
        self._memo: dict = {}
        self._momentum = MomentumTracker()
        self._bans: list = []
        self._skeleton_stats: dict = {}
        self._rebuilding = False
        self._next_iteration_ts = 0
        self._started_mono = time.monotonic()
        self._searcher = FactorSearcher()
        self._llm = DiscoveryLlmAgents(
            spec.llm_client, timeout_s=spec.settings.discovery_llm_timeout_s,
            max_retries=spec.settings.discovery_llm_max_retries,
            budget=_hourly_budget(spec))
        self._validate_sem = asyncio.Semaphore(
            max(1, int(spec.settings.discovery_validate_concurrency)))
        self._factory = CandidateFactory(self._searcher, self._llm, self._momentum)
        self._data_failures = 0
        self._data_backoff = 5.0

    # ============================ 扇出（镜像 paper/session.py） ============================
    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=256)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self._subscribers.discard(queue)

    def snapshot(self) -> list[dict]:
        """订阅即发：**全量 state 快照 + 最近 ``ws_ring`` 条事件**（终态永远在最后）。

        与 ``weight-batch`` 的「全量回放」有意偏离（§4.2）：挖掘 run 可能跑三天、事件数十万条，
        全量回放不可行。终态事件单独持有一个槽位，绝不被环形缓冲挤掉——否则晚订阅的客户端会
        看到一个永远「进行中」的死流。
        """
        head = [{"type": "state", "data": self.state_snapshot()}]
        tail = [self._terminal_event] if self._terminal_event else []
        return head + list(self._ring) + tail

    def _push(self, event: dict) -> None:
        if event.get("type") == "done":
            self._terminal_event = event
        else:
            self._ring.append(event)
            cap = max(50, int(self.spec.settings.discovery_ws_ring))
            if len(self._ring) > cap:
                del self._ring[: len(self._ring) - cap]
        for queue in list(self._subscribers):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                pass

    @property
    def is_terminal(self) -> bool:
        return self.status in _TERMINAL_STATUSES

    def state_snapshot(self) -> dict:
        """全量运行态快照（WS ``state`` 事件与 REST 详情共用真源）。"""
        return {
            "runId": self.run_id, "status": self.status, "pauseReason": self.pause_reason,
            "iteration": int(self._checkpoint.iteration),
            "counters": dict(self._counters), "budget": dict(self._checkpoint.budget),
            "poolSizes": dict(self._checkpoint.pool_sizes),
            "mechanismCoverage": dict(self._checkpoint.mechanism_coverage),
            "fsaBans": [ban.to_dict() for ban in self._bans[:12]],
            "nextIterationTs": int(self._next_iteration_ts) or None,
            "dataWindow": dict(self._checkpoint.data_window),
            "llmUsage": {**self._llm.usage(), "enabled": self._llm.enabled},
            "rebuilding": self._rebuilding,
            "keptCount": int(self._counters.get("kept", 0)),
            "testedCount": int(self._counters.get("tested", 0)),
        }

    # ============================ 生命周期 ============================
    def start(self) -> None:
        self._task = asyncio.create_task(self._run())

    def request_cancel(self) -> None:
        """协作式取消标志置位（步间检查）；已终态幂等无副作用。"""
        if not self.is_terminal:
            self._cancel = True
            self._paused = False

    def request_pause(self, reason: str = "user") -> None:
        """协作式暂停（步间生效）。

        ``status`` 必须**同步**改写：``service.run_summary`` 以内存态优先，只置标志会让任务
        列表与 WS 一直报「挖掘中」，而 DB 行已是 ``paused``——用户点了暂停却看不出暂停。
        """
        if self.is_terminal:
            return
        self._paused = True
        self.pause_reason = reason
        self.status = "paused"
        self._push({"type": "state", "data": self.state_snapshot()})

    def request_resume(self) -> None:
        if self.is_terminal:
            return
        self._paused = False
        self.pause_reason = ""
        self.status = "running"
        self._push({"type": "state", "data": self.state_snapshot()})

    @property
    def is_alive(self) -> bool:
        """循环任务是否仍在跑。

        ``paused`` 有两种物理形态：① 循环仍在暂停轮询里（可就地放行标志恢复）；② 循环已经
        返回（``_prepare`` 阶段取数失败 / 重建失败即属此类）。后者只翻标志毫无作用，必须重新
        装配一个 run，否则接口回 ``running`` 而实际上什么都没跑（§2.14 恢复路径）。
        """
        task = self._task
        return task is not None and not task.done()

    def force_cancel(self) -> None:
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

    # ============================ 主循环 ============================
    async def _run(self) -> None:
        try:
            if not await self._prepare():
                return
            while not self._cancel:
                if self._paused:
                    await asyncio.sleep(_PAUSE_POLL_S)
                    continue
                limit = self._limit_reached()
                if limit:
                    await self._finalize("succeeded", pause_reason=limit)
                    return
                if not await self._ensure_fresh_data():
                    continue
                await self._run_iteration()
                limit = self._limit_reached()
                if limit:                       # 达标即刻收尾，不白等一个完整间隔
                    await self._finalize("succeeded", pause_reason=limit)
                    return
                await self._sleep_until_next()
            await self._finalize("stopped")
        except asyncio.CancelledError:
            await self._finalize("stopped")
            raise
        except Exception as exc:  # noqa: BLE001 —— 任何失败都收口为 failed 终态，绝不卡 running
            logger.exception("discovery run %d 异常终止", self.run_id)
            await self._finalize("failed", error_code="DISCOVERY_FAILED", error=str(exc))

    async def _prepare(self) -> bool:
        """开跑前置：载入检查点 / 已测集合 → 取数 → （续跑）重建 kept 池。

        ⚠️ 续跑**必须**先跑完 ``rebuild_kept_pool``（§2.10 承重）：否则 F10 的值级去重没有基准，
        每次重启后都会开始收一批同质因子，而这恰是本特性的核心卖点。重建失败一律转 ``paused``，
        **绝不**带残缺池开跑。
        """
        await self._load_state()
        self._push({"type": "state", "data": self.state_snapshot()})
        if not await self._refresh_data(initial=True):
            return False
        if self.spec.resume and self._checkpoint.iteration > 0:
            self._rebuilding = True
            self._push({"type": "state", "data": self.state_snapshot()})
            ok = await self._rebuild_pool()
            self._rebuilding = False
            if not ok:
                await self._pause_with("rebuild_failed")
                return False
        self._push({"type": "state", "data": self.state_snapshot()})
        return True

    async def _load_state(self) -> None:
        sm = get_sessionmaker()
        async with sm() as session:
            row = await repo.get_run(session, self.run_id)
            if row is None:
                raise ValueError(f"discovery run {self.run_id} 不存在")
            self._checkpoint = Checkpoint.from_dict(json.loads(row.checkpoint_json or "{}"))
            self._counters = json.loads(row.counters_json or "{}") or dict(self._counters)
            self._tested = await repo.load_tested_hashes(session, self.run_id)
        self._counters.setdefault("rejected", {})
        self._momentum = MomentumTracker.from_list(self._checkpoint.momentum)
        self._bans = bans_from_checkpoint(self._checkpoint)
        self._skeleton_stats = dict(self._checkpoint.skeleton_stats)
        if not self._checkpoint.rng_seed:
            self._checkpoint.rng_seed = int(self.spec.config.get("seed") or int(time.time()))

    async def _rebuild_pool(self, *, demote: bool = True) -> bool:
        """在当前窗口上重建 kept 池的去重基准（与刷新复评**同一个实现**，§2.10）。

        Args:
            demote: 是否按复评结果淘汰并落降级。``False``（``revalidateOnRefresh`` 关）时
                只重建基准、不淘汰任何条目——用户关掉的是「复评」，不是「重建」。
        """
        sm = get_sessionmaker()
        async with sm() as session:
            rows = await repo.list_kept_candidates(session, self.run_id)
        targets = [KeptRow(candidate_id=int(row.id), expr_text=row.expr_text,
                           mechanism=row.mechanism, skeleton=row.skeleton)
                   for row in rows if row.expr_text]
        if not targets:
            self._pool = []
            return True
        async with self._validate_sem:
            result = await run_in_threadpool(rebuild_kept_pool, targets, self._validation_ctx())
        if result.errors and not result.entries:
            logger.error("discovery run %d kept 池重建全部失败：%s", self.run_id, result.errors[:3])
            return False
        if not demote:
            self._pool = list(result.entries)
            return True
        self._pool = [e for e in result.entries
                      if e.candidate_id not in {cid for cid, _r in result.failures}]
        await self._apply_demotions(result.failures)
        return True

    async def _apply_demotions(self, failures: list) -> None:
        if not failures:
            return
        sm = get_sessionmaker()
        async with sm() as session:
            await repo.mark_demoted(session, [cid for cid, _reason in failures])
        for candidate_id, reason in failures:
            self._push({"type": "demoted",
                        "data": {"candidateId": candidate_id, "reason": reason}})

    # ============================ 取数与刷新 ============================
    def _window_bounds(self) -> tuple[int, int]:
        bars = int(self.spec.config.get("windowBars", 4000))
        step = bar_to_seconds(self.spec.bar)
        end_ts = int(time.time()) // step * step - step      # 只取已确认根
        if str(self.spec.config.get("windowMode", "rolling")) == "fixed":
            fixed_end = int(self._checkpoint.data_window.get("endTs") or 0)
            if fixed_end:
                end_ts = fixed_end
        return end_ts - bars * step, end_ts

    async def _refresh_data(self, *, initial: bool = False) -> bool:
        """重新分页拉真实 OKX K 线（run **自持**数据帧，绝不依赖请求级缓存，R-3）。

        捕获面必须同时覆盖 ``OKXError``、``HTTPException``（``load_range_candles`` 在区间无已确认
        K 线时抛的是 400 ``EMPTY_RANGE``，不是 ``OKXError``）、``TimeoutError`` 与 ``OSError``；
        只捕 ``OKXError`` 会让后台循环直接崩成 ``failed``（评审 P2-2）。
        """
        start_ts, end_ts = self._window_bounds()
        try:
            candles = await load_range_candles(
                self.spec.rest, inst_id=self.spec.inst_id, bar=self.spec.bar,
                start_ts=start_ts, end_ts=end_ts,
                max_bars=int(self.spec.settings.discovery_max_window_bars))
        except (OKXError, HTTPException, asyncio.TimeoutError, OSError) as exc:
            return await self._handle_data_failure(exc, initial=initial)
        self._df = candles_to_df(candles)
        self._memo = {}
        self._factory = CandidateFactory(self._searcher, self._llm, self._momentum)
        self._data_failures = 0
        self._data_backoff = 5.0
        self._checkpoint.data_window = {"startTs": int(self._df["ts"].iloc[0]),
                                        "endTs": int(self._df["ts"].iloc[-1]),
                                        "lastRefreshTs": int(time.time())}
        return True

    async def _handle_data_failure(self, exc: Exception, *, initial: bool) -> bool:
        """指数退避重试；连续失败超限 ⇒ ``paused(data_unavailable)`` 而非 ``failed``。"""
        self._data_failures += 1
        message = getattr(exc, "message", None) or str(exc)
        logger.warning("discovery run %d 取数失败（第 %d 次）：%s",
                       self.run_id, self._data_failures, message)
        self._push({"type": "error", "data": {"code": "DISCOVERY_DATA_UNAVAILABLE",
                                              "message": f"行情取数失败：{message}"}})
        if self._data_failures >= _DATA_FAILURE_LIMIT or initial:
            await self._pause_with("data_unavailable")
            return False
        await asyncio.sleep(min(self._data_backoff, _DATA_RETRY_MAX_S))
        self._data_backoff = min(self._data_backoff * 2.0, _DATA_RETRY_MAX_S)
        return False

    async def _ensure_fresh_data(self) -> bool:
        """到点即刷新窗口并对 kept 池做一次轻量复评（跨窗口指标可比性，§2.9 / R-13）。"""
        interval = float(self.spec.config.get("dataRefreshS", 21600) or 0)
        if interval <= 0 or self._df is None:
            return self._df is not None
        last = int(self._checkpoint.data_window.get("lastRefreshTs") or 0)
        if time.time() - last < interval:
            return True
        if not await self._refresh_data():
            return False
        # 重建**无条件**执行：窗口右移后 kept 池里的值序列与新窗口不再等长，F10 的值通道会逐候选
        # 抛 ValueError 并被 `_validate_one` 吞成 `eval_error`——表现为「刷新之后突然一条都收不到」，
        # 而不是报错。`revalidateOnRefresh` 只决定要不要按复评结果淘汰，不决定要不要重建基准（§2.10）。
        if not await self._rebuild_pool(
                demote=bool(self.spec.config.get("revalidateOnRefresh", True))):
            await self._pause_with("rebuild_failed")
            return False
        return True

    # ============================ 单轮迭代 ============================
    def _validation_ctx(self) -> ValidationContext:
        return ValidationContext(
            df_full=self._df, horizon=int(self.spec.config.get("horizon", 6)),
            is_ratio=float(self.spec.config.get("isRatio", 0.7)),
            fee_bps=float(self.spec.settings.default_taker_fee_bps),
            slip_bps=float(self.spec.settings.default_slippage_bps),
            thresholds=normalize_thresholds(self.spec.config.get("thresholds")),
            memo=self._memo)

    def _review_ctx(self) -> ReviewContext:
        evolution = self.spec.config.get("evolution") or {}
        iteration = int(self._checkpoint.iteration)
        kept_counts: dict[str, int] = {}
        for entry in self._pool:
            kept_counts[entry.skeleton] = kept_counts.get(entry.skeleton, 0) + 1
        return ReviewContext(
            tested_hashes=self._tested,
            banned_skeletons=active_bans(
                self._bans, iteration=iteration,
                ttl_iters=int(evolution.get("fsaBanTtlIters", 150))),
            kept_skeleton_counts=kept_counts,
            skeleton_stats=self._skeleton_stats,
            variant_cap=int(evolution.get("variantCap", 6)),
            min_coverage=float(normalize_thresholds(
                self.spec.config.get("thresholds"))["minCoverage"]))

    async def _run_iteration(self) -> None:
        started = int(time.time())
        started_mono = time.monotonic()
        index = int(self._checkpoint.iteration) + 1
        rng = random.Random(iteration_seed(self._checkpoint.rng_seed, index))
        budget = self._plan_budget(index)
        tally = IterationTally()
        candidates, llm_outcome = await self._generate(rng, budget, tally)
        passed, rejected = filter_rules(candidates, self._review_ctx())
        tally.rule_rejected = len(rejected)
        passed, llm_rejected = await self._llm_review(passed, llm_outcome)
        tally.llm_rejected = len(llm_rejected)
        rows = self._rows_for(rejected + llm_rejected, started)
        rows += await self._validate_batch(passed, tally, started, started_mono)
        elapsed_ms = int((time.monotonic() - started_mono) * 1000)
        tally.overrun = elapsed_ms / 1000.0 > float(
            self.spec.config.get("iterationIntervalS", 300))
        await self._commit(index, rows, tally, budget, llm_outcome, started, elapsed_ms)

    def _plan_budget(self, index: int) -> dict:
        """冷启动 → 固定预算；此后每 ``adaptEvery`` 轮跑一次纯函数 ``adapt_budget``。"""
        evolution = self.spec.config.get("evolution") or {}
        if len(self._pool) < int(evolution.get("coldStartMin", 5)):
            return cold_start_budget()
        current = normalize_budget(self._checkpoint.budget)
        every = max(1, int(evolution.get("adaptEvery", 10)))
        if index % every != 0:
            return current
        return adapt_budget(current, self._budget_counters(index))

    def _budget_counters(self, index: int) -> BudgetCounters:
        rejected = self._counters.get("rejected") or {}
        total_rejected = sum(int(v or 0) for v in rejected.values()) or 1
        tested = max(1, int(self._counters.get("tested", 0)))
        coverage = self._checkpoint.mechanism_coverage
        return BudgetCounters(
            near_kept_rate=float(self._counters.get("nearKeptRate", 0.0) or 0.0),
            global_kept_rate=int(self._counters.get("kept", 0)) / tested,
            dedupe_reject_rate=int(rejected.get("F10", 0)) / total_rejected,
            uncovered_families=len(uncovered_families(coverage)),
            covered_families=sum(1 for v in coverage.values() if int(v or 0) > 0),
            iteration=index,
            kept_rate_trend=tuple(self._checkpoint.kept_rate_trend))

    # ---------- ① 生成（编排下沉到 generation.CandidateFactory） ----------
    async def _generate(self, rng, budget: dict, tally: IterationTally):
        return await self._factory.produce(
            rng, budget=budget,
            count=int(self.spec.config.get("candidatesPerIteration", 40)),
            pool=self._pool, coverage=self._checkpoint.mechanism_coverage,
            bans=self._bans, skeleton_stats=self._skeleton_stats, tally=tally)

    # ---------- ② LLM 精判 ----------
    async def _llm_review(self, passed: list, generate_outcome) -> tuple[list, list]:
        """抽样交审查子代理；**fail-open**：降级即视为全部 accept（裁决只减不增）。"""
        sample_size = int((self.spec.config.get("llm") or {}).get("reviewSample", 5))
        if not passed or sample_size <= 0 or not self._llm.enabled:
            return passed, []
        sample = passed[:sample_size]
        outcome = await self._llm.review({
            "candidates": [{"hash": c.genome_hash, "expr": c.expr_text,
                            "describe": c.describe, "mechanism": c.mechanism}
                           for c in sample],
            "mechanismCoverage": self._checkpoint.mechanism_coverage})
        generate_outcome.calls += outcome.calls
        if outcome.degraded and not generate_outcome.degraded:
            generate_outcome.degraded = True
            generate_outcome.degraded_reason = outcome.degraded_reason
        vetoed, reasons = accepted_hashes(outcome, [c.genome_hash for c in sample])
        if not vetoed:
            return passed, []
        kept = [c for c in passed if c.genome_hash not in vetoed]
        rejected = [(c, f"llm_review:{reasons.get(c.genome_hash, '')}"[:64])
                    for c in passed if c.genome_hash in vetoed]
        return kept, rejected

    # ---------- ③ 验证 ----------
    async def _validate_batch(self, candidates: list, tally: IterationTally,
                              started_ts: int, started_mono: float) -> list:
        """逐候选验证（线程池 + 信号量 + 单候选超时 + 单轮墙钟预算）。

        一旦 ``kept``，**立刻**把它加进内存 kept 池——使同轮后续候选就能与它做相关性去重，
        避免同轮双胞胎双双入库。
        """
        budget_s = float(self.spec.settings.discovery_iteration_timeout_s)
        timeout_s = float(self.spec.settings.discovery_candidate_timeout_s)
        rows: list = []
        for candidate in candidates:
            if self._cancel:
                break
            if time.monotonic() - started_mono > budget_s:
                tally.truncated_by_timeout = len(candidates) - len(rows)
                break
            outcome = await self._validate_one(candidate, timeout_s)
            tally.validated += 1
            self._counters["tested"] = int(self._counters.get("tested", 0)) + 1
            self._tested.add(candidate.genome_hash)
            update_skeleton_stats(self._skeleton_stats, candidate.skeleton,
                                  kept=outcome.verdict == "kept")
            rows.append(self._candidate_row(candidate, outcome, started_ts))
            self._absorb(candidate, outcome, tally)
        return rows

    async def _validate_one(self, candidate, timeout_s: float):
        """单候选验证：先跑末位的 ``constant_or_empty``（共用记忆化求值），再跑 11 条闸。"""
        from .validation import ValidationOutcome

        trials = int(self._counters.get("tested", 0)) + 1
        try:
            async with self._validate_sem:
                return await asyncio.wait_for(
                    run_in_threadpool(self._validate_sync, candidate, trials),
                    timeout=timeout_s)
        except asyncio.TimeoutError:
            return ValidationOutcome(verdict="rejected", reject_reason="validate_timeout",
                                     direction="long", metrics={}, series={})
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 —— 单候选异常绝不拖垮整轮（沿用 mine_factors 纪律）
            return ValidationOutcome(verdict="rejected",
                                     reject_reason=f"eval_error:{type(exc).__name__}"[:64],
                                     direction="long", metrics={}, series={})

    def _validate_sync(self, candidate, trials: int):
        from .expression import evaluate
        from .validation import ValidationOutcome

        ctx = self._validation_ctx()
        values = evaluate(candidate.genome.node, _fields_of(ctx), ctx.memo)
        reason = review_values(values, self._review_ctx())
        if reason:
            return ValidationOutcome(verdict="rejected", reject_reason=reason,
                                     direction="long", metrics={}, series={})
        return validate_candidate(candidate.genome.node, ctx, pool=self._pool, trials=trials)

    def _absorb(self, candidate, outcome, tally: IterationTally) -> None:
        """把单候选结果并入 run 级状态（kept 立刻进池，供同轮后续候选去重）。"""
        if outcome.verdict == "kept" and outcome.values is not None:
            tally.kept += 1
            self._counters["kept"] = int(self._counters.get("kept", 0)) + 1
            coverage = self._checkpoint.mechanism_coverage
            coverage[candidate.mechanism] = int(coverage.get(candidate.mechanism, 0)) + 1
            self._pool.append(KeptEntry(
                candidate_id=0, expr_text=candidate.expr_text, direction=outcome.direction,
                values=outcome.values, ic_series=outcome.ic_series, score=outcome.score,
                mechanism=candidate.mechanism, skeleton=candidate.skeleton))
            self._trim_pool()
            return
        reason = outcome.reject_reason or "unknown"
        tally.count_reject(reason)
        bump_reject(self._counters, reason)

    def _trim_pool(self) -> None:
        """有界精英池：到顶后只保留分数最高的 ``maxKept`` 条（§2.9 资源封顶）。"""
        cap = max(1, int(self.spec.config.get("maxKept", 200)))
        if len(self._pool) <= cap:
            return
        self._pool.sort(key=lambda entry: -float(entry.score or 0.0))
        del self._pool[cap:]

    # ---------- ④ 提交 ----------
    def _rows_for(self, rejected: list, started_ts: int) -> list:
        rows = []
        for candidate, reason in rejected:
            self._tested.add(candidate.genome_hash)
            bump_reject(self._counters, reason.split(":")[0])
            rows.append(repo.CandidateRow(
                kind=candidate.kind, genome_hash=candidate.genome_hash,
                genome={"expr": candidate.expr_text}, expr_text=candidate.expr_text,
                describe_text=candidate.describe, skeleton=candidate.skeleton,
                mechanism=candidate.mechanism, source=candidate.source,
                verdict="rejected", reject_reason=reason, created_ts=started_ts))
        return rows

    def _candidate_row(self, candidate, outcome, started_ts: int) -> repo.CandidateRow:
        return repo.CandidateRow(
            kind=candidate.kind, genome_hash=candidate.genome_hash,
            genome={"expr": candidate.expr_text}, expr_text=candidate.expr_text,
            describe_text=candidate.describe, skeleton=candidate.skeleton,
            mechanism=candidate.mechanism, source=candidate.source,
            verdict=outcome.verdict, reject_reason=outcome.reject_reason,
            metrics=outcome.metrics, series=outcome.series,
            trials_at_accept=int(outcome.metrics.get("trials", 0) or 0),
            created_ts=started_ts)

    async def _commit(self, index: int, rows: list, tally: IterationTally, budget: dict,
                      llm_outcome, started_ts: int, elapsed_ms: int) -> None:
        """单事务写候选 + 迭代 + 检查点，再扇出事件（落库失败绝不吞，转 failed 终态）。"""
        self._run_fsa(index, tally)
        self._checkpoint.iteration = index
        self._checkpoint.budget = normalize_budget(budget)
        self._checkpoint.momentum = self._momentum.to_list()
        self._checkpoint.fsa_bans = [ban.to_dict() for ban in self._bans]
        self._checkpoint.skeleton_stats = prune_skeleton_stats(self._skeleton_stats)
        self._checkpoint.pool_sizes = {**self._checkpoint.pool_sizes,
                                       "factor": len(self._pool)}
        self._checkpoint.kept_rate_trend = (
            list(self._checkpoint.kept_rate_trend) + [rate(tally.kept, tally.validated)])[-3:]
        self._counters["llmCalls"] = int(self._counters.get("llmCalls", 0)) + llm_outcome.calls
        stats = tally_to_stats(tally)
        iteration_row = repo.IterationRow(
            idx=index, started_ts=started_ts, finished_ts=int(time.time()), budget=budget,
            stats={**stats, "durationMs": elapsed_ms},
            llm={"calls": llm_outcome.calls, "latencyMs": llm_outcome.latency_ms,
                 "degraded": llm_outcome.degraded,
                 "degradedReason": llm_outcome.degraded_reason,
                 "promptChars": llm_outcome.prompt_chars},
            note=iteration_note(tally))
        pruned = await self._persist_iteration(iteration_row, rows)
        self._counters["prunedCandidates"] = int(
            self._counters.get("prunedCandidates", 0)) + int(pruned.get("prunedCandidates", 0))
        self._counters["prunedIterations"] = int(
            self._counters.get("prunedIterations", 0)) + int(pruned.get("prunedIterations", 0))
        self._emit_iteration(iteration_row, rows)

    def _run_fsa(self, index: int, tally: IterationTally) -> None:
        evolution = self.spec.config.get("evolution") or {}
        every = max(1, int(evolution.get("fsaEvery", 25)))
        ttl = int(evolution.get("fsaBanTtlIters", 150))
        if index % every != 0:
            return
        fresh = scan_skeletons([entry.skeleton for entry in self._pool], iteration=index,
                               threshold=float(evolution.get("fsaThreshold", 0.15)))
        if fresh:
            self._bans = merge_bans(self._bans, fresh)
            tally.skipped = tally.skipped or f"fsa_frozen:{len(fresh)}"
        self._bans = prune_expired(self._bans, iteration=index, ttl_iters=ttl)

    async def _persist_iteration(self, iteration_row, rows: list) -> dict:
        """落库（有界重试一次）；两次都失败即抛，由 ``_run`` 收口为 ``failed`` 终态。"""
        for attempt in range(2):
            try:
                sm = get_sessionmaker()
                async with sm() as session:
                    return await repo.commit_iteration(
                        session, self.run_id, candidates=rows, iteration=iteration_row,
                        checkpoint_json=dumps(self._checkpoint), counters=self._counters,
                        candidate_row_cap=int(self.spec.settings.discovery_max_candidate_rows),
                        iteration_row_cap=int(self.spec.settings.discovery_max_iteration_rows))
            except Exception as exc:  # noqa: BLE001
                logger.warning("discovery run %d 第 %d 轮落库失败（尝试 %d）：%s",
                               self.run_id, iteration_row.idx, attempt + 1, exc)
                if attempt == 0:
                    await asyncio.sleep(_PERSIST_RETRY_DELAY)
                else:
                    raise
        return {}

    def _emit_iteration(self, iteration_row, rows: list) -> None:
        self._push({"type": "iteration", "data": {
            "index": iteration_row.idx, "startedTs": iteration_row.started_ts,
            "finishedTs": iteration_row.finished_ts, "budget": iteration_row.budget,
            **iteration_row.stats, "llm": iteration_row.llm, "note": iteration_row.note}})
        kept = [row for row in rows if row.verdict == "kept"]
        others = [row for row in rows if row.verdict != "kept"]
        for row in (kept + others)[:_MAX_CANDIDATE_EVENTS]:
            self._push({"type": "candidate", "data": candidate_event(row)})
        self._push({"type": "state", "data": self.state_snapshot()})

    # ============================ 调度与终态 ============================
    async def _sleep_until_next(self) -> None:
        interval = float(self.spec.config.get("iterationIntervalS", 300))
        self._next_iteration_ts = int(time.time() + interval)
        deadline = time.monotonic() + interval
        while not self._cancel and time.monotonic() < deadline:
            if self._paused:
                return
            await asyncio.sleep(min(_PAUSE_POLL_S, max(0.0, deadline - time.monotonic())))

    def _limit_reached(self) -> str:
        max_iterations = int(self.spec.config.get("maxIterations", 0) or 0)
        max_hours = float(self.spec.config.get("maxRuntimeHours", 0) or 0.0)
        if max_iterations and self._checkpoint.iteration >= max_iterations:
            return "limit_reached"
        if max_hours and (time.monotonic() - self._started_mono) >= max_hours * 3600.0:
            return "limit_reached"
        return ""

    async def _pause_with(self, reason: str) -> None:
        """转 ``paused`` 并如实上报原因。

        **绝不推 ``done``**：``paused`` 不是终态（§4.2 里 ``done`` 专指终态），而
        ``api/discovery.py`` 见 ``done`` 即关流、``snapshot()`` 又会把它永久钉在缓冲尾部——
        一条还能恢复的任务会因此变成「怎么连都立刻断」的死流。状态经 ``state`` 事件下发即可，
        取数失败另有 ``error`` 事件说明原因。
        """
        self._paused = True
        self.status = "paused"
        self.pause_reason = reason
        await self._write_status("paused", reason)
        self._push({"type": "state", "data": self.state_snapshot()})

    async def _finalize(self, status: str, *, pause_reason: str = "", error_code: str = "",
                        error: str = "") -> None:
        """必达终态：**无论落库成败**都转终态并推终态事件（H-B1 教训，绝不让 WS 永久悬挂）。"""
        if self.is_terminal:
            return
        self.status = status
        self.pause_reason = pause_reason
        await self._write_status(status, pause_reason, error_code=error_code, error=error)
        self._push({"type": "state", "data": self.state_snapshot()})
        self._push({"type": "done", "data": {
            "status": status, "summary": {"iteration": self._checkpoint.iteration,
                                          "kept": int(self._counters.get("kept", 0)),
                                          "tested": int(self._counters.get("tested", 0)),
                                          "reason": pause_reason or error_code}}})
        logger.info("discovery run %d 终态 %s（%s）", self.run_id, status,
                    pause_reason or error_code or "-")
        self._notify_terminal()

    def _notify_terminal(self) -> None:
        if self._on_terminal is None:
            return
        try:
            self._on_terminal()
        except Exception as exc:  # noqa: BLE001 —— 通知失败绝不影响本 run 已达终态
            logger.warning("discovery run %d on_terminal 回调失败：%s", self.run_id, exc)

    async def _write_status(self, status: str, pause_reason: str, *, error_code: str = "",
                            error: str = "") -> None:
        try:
            sm = get_sessionmaker()
            async with sm() as session:
                await repo.set_run_status(
                    session, self.run_id, status=status, pause_reason=pause_reason,
                    finished_ts=int(time.time()) if status in _TERMINAL_STATUSES else None,
                    error_code=error_code, error_message=error)
        except Exception as exc:  # noqa: BLE001 —— 落库失败也要转终态，绝不卡 running
            logger.error("discovery run %d 状态落库失败（仍已转终态）：%s", self.run_id, exc)


# ============================ 模块级纯函数 ============================
def _fields_of(ctx: ValidationContext) -> dict:
    from .validation import _context_of

    return _context_of(ctx)


def _hourly_budget(spec: RunSpec):
    from .llm_agents import HourlyCallBudget

    return HourlyCallBudget(int(spec.settings.discovery_llm_max_calls_per_hour))
