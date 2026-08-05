"""发现循环运行时（strategy-factor-discovery §2.9 / discovery-strategy-axes §2.9）。

24/7 长跑的四步迭代，本轮起**按轴展开**：每条轴一个 ``Lane``（生成 + 审查 + 验证 + 池），
runtime 只负责「调度谁、给多少名额、把结果落库和扇出」。

四条纪律全部照抄仓库既有范式（``WeightTrainManager`` / ``ai_auto`` 会话），不得省略：
1. **CPU 密集全走 ``run_in_threadpool``**（表达式求值 / 回测），绝不阻塞事件循环；且必须先拿到
   ``discovery_validate_concurrency`` 信号量——线程池与所有 API 请求共用，无节制并发会让交互
   请求变慢，用户只会感觉「整个 App 变卡」（R-16 / R-12）。
2. **每个 run 必达终态**；落库失败也先置终态再重试一次，绝不让 WS 永久悬挂。
3. **协作式取消**：四步之间与每个候选之间检查标志。
4. **绝不合成数据**：OKX 不可达就退避、连续失败转 ``paused(data_unavailable)``，
   **绝不**用旧数据假装新数据。
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass

from fastapi import HTTPException
from fastapi.concurrency import run_in_threadpool

from ..agent.services import load_htf_frames, load_range_candles
from ..config import Settings
from ..db.session import get_sessionmaker
from ..engine.backtester import candles_to_df
from ..engine.mtf import bar_to_seconds
from ..okx.rest_client import OKXError
from . import repositories as repo
from .catalogs import SearchInputs, compose_members, library_members, minable_templates
from .checkpoint import Checkpoint, bans_of
from .evolution import MomentumTracker
from .genomes import ALL_AXES, canonical_json
from .iteration import IterationRunner
from .lanes import LaneContext, build_lanes
from .llm_agents import DiscoveryLlmAgents
from .validation import (
    KeptRow,
    ValidationContext,
    normalize_thresholds,
    rebuild_kept_pool,
    split_index,
)

logger = logging.getLogger("aragon")

_TERMINAL_STATUSES = ("stopped", "succeeded", "failed")
_DATA_RETRY_MAX_S = 900.0       # 取数失败的指数退避上限
_DATA_FAILURE_LIMIT = 12        # 连续失败上限，超出转 paused(data_unavailable)
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
    """单个挖掘任务的运行时：四步循环 + 多轴调度 + 事件扇出 + 协作式取消 + 必达终态。"""

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
        self._df = None
        self._candles: list = []
        self._htf_frames: dict = {}
        self._memo: dict = {}
        self._momentum = MomentumTracker()
        self._rebuilding = False
        self._next_iteration_ts = 0
        self._started_mono = time.monotonic()
        self._lanes: dict = {}
        self._pending: dict = {}
        self._tested: set[str] = set()
        self._template_catalog = None
        self._member_catalog = None
        self._library_members: tuple = ()
        self._llm = DiscoveryLlmAgents(
            spec.llm_client, timeout_s=spec.settings.discovery_llm_timeout_s,
            max_retries=spec.settings.discovery_llm_max_retries,
            budget=_hourly_budget(spec))
        self._validate_sem = asyncio.Semaphore(
            max(1, int(spec.settings.discovery_validate_concurrency)))
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
        """订阅即发：**全量 state 快照 + 最近 ``ws_ring`` 条事件**（终态永远在最后）。"""
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
            "poolSizes": {axis: lane.pool_size() for axis, lane in self._lanes.items()},
            "axisPlan": dict(self._checkpoint.axis_shares),
            "axes": list(self.axes()),
            "axisSnapshots": {axis: lane.snapshot() for axis, lane in self._lanes.items()},
            "mechanismCoverage": dict(self._checkpoint.mechanism_coverage),
            "fsaBans": [ban.to_dict() for ban in self._all_bans()[:12]],
            "nextIterationTs": int(self._next_iteration_ts) or None,
            "dataWindow": dict(self._checkpoint.data_window),
            "llmUsage": {**self._llm.usage(), "enabled": self._llm.enabled},
            "rebuilding": self._rebuilding,
            "keptCount": int(self._counters.get("kept", 0)),
            "testedCount": int(self._counters.get("tested", 0)),
        }

    def axes(self) -> list[str]:
        raw = self.spec.config.get("axes") or ["factor"]
        return [axis for axis in ALL_AXES if axis in set(raw)]

    def _all_bans(self) -> list:
        return [ban for lane in self._lanes.values() for ban in lane.bans]

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
        """开跑前置：载入检查点 → 取数（含高周期） → 装配 Lane → （续跑）重建各轴池。

        ⚠️ 续跑**必须**先跑完池重建（§2.10 / BL-5 承重）：否则 F10 / S8 的去重没有基准，
        每次重启后都会开始收一批同质产物，而这恰是本特性的核心卖点。重建失败一律转
        ``paused``，**绝不**带残缺池开跑。
        """
        await self._load_state()
        self._push({"type": "state", "data": self.state_snapshot()})
        self._template_catalog = self._build_template_catalog()
        if not await self._refresh_data(initial=True):
            return False
        await self._build_lanes()
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
        if not self._checkpoint.rng_seed:
            self._checkpoint.rng_seed = int(self.spec.config.get("seed") or int(time.time()))

    def _build_template_catalog(self):
        """只在真正需要策略模板的轴被选中时才建目录。

        因子单轴的 run 不得因此多取一根高周期 K 线——「未打开过挖掘视图的用户零额外开销」
        这条承诺同样适用于「没选策略轴的 run」（DoD #12 的同一精神）。
        """
        if not {"strategy_param", "strategy_combo"}.intersection(self.axes()):
            return None
        return minable_templates(
            self.spec.bar, max_htf_frames=int(self.spec.settings.discovery_max_htf_frames))

    async def _build_lanes(self) -> None:
        """装配各轴 Lane 并从检查点恢复**逐轴**计数（BL-14）。"""
        inputs = await self._build_search_inputs()
        self._lanes = build_lanes(self.axes(), config=self.spec.config,
                                  momentum=self._momentum, llm=self._llm,
                                  coverage=self._checkpoint.mechanism_coverage,
                                  catalog=inputs)
        for axis, lane in self._lanes.items():
            lane.tested = int(self._checkpoint.tested_by_axis.get(axis, 0) or 0)
            lane.skeleton_stats = dict(self._checkpoint.skeleton_stats_by_axis.get(axis) or {})
            lane.bans = bans_of(self._checkpoint.fsa_bans_by_axis.get(axis) or [])
            lane.tested_hashes = self._tested

    async def _build_search_inputs(self) -> SearchInputs:
        """模板目录 + 因子成员目录 + 组合规模区间（数据刷新后必须重建，方向要重新冻结）。"""
        config = self.spec.config
        if "factor_combo" in self.axes():
            self._library_members = library_members(
                self.lane_context(), mined_exprs=await self._mined_exprs(),
                include_library=bool(config.get("includeLibraryFactors", True)))
        templates = self._template_catalog.templates if self._template_catalog else ()
        self._checkpoint.template_signature = _template_signature(templates)
        return SearchInputs(
            templates=templates, members=self._compose_members(),
            combo_size=tuple(_size_range(config.get("comboSize"), 2, 5)),
            rotation_size=tuple(_size_range(config.get("rotationSize"), 2, 4)))

    def _compose_members(self) -> tuple:
        """把本 run 新入库的因子拼进成员目录（每轮一次，便宜；库那半边只在刷新后重算）。"""
        if "factor_combo" not in self.axes():
            return ()
        catalog = compose_members(
            self._factor_kept_exprs(), self._library_members,
            int(self.spec.settings.discovery_member_catalog_max))
        self._member_catalog = catalog
        self._checkpoint.member_catalog_hash = catalog.content_hash
        return catalog.members

    async def _refresh_catalogs(self) -> None:
        """数据刷新后重建目录并下发给各轴（成员方向必须在新窗口上重新冻结）。"""
        inputs = await self._build_search_inputs()
        for lane in self._lanes.values():
            if hasattr(lane, "inputs"):
                lane.inputs = inputs

    def _factor_kept_exprs(self) -> list:
        lane = self._lanes.get("factor")
        return lane.parent_genomes() if lane is not None else []

    async def _mined_exprs(self) -> list:
        """既有挖掘因子（``factors`` 表 ``factor_key=="expr" and source=="mined"``）。"""
        from sqlalchemy import select

        from ..db.models import Factor

        sm = get_sessionmaker()
        out: list = []
        async with sm() as session:
            rows = (await session.execute(select(Factor).where(
                Factor.factor_key == "expr", Factor.source == "mined"))).scalars().all()
        for row in rows:
            try:
                params = json.loads(row.params_json or "{}")
            except (json.JSONDecodeError, TypeError):
                continue
            expr = str((params or {}).get("expr") or "")
            if expr:
                out.append((expr, str(row.direction or "long")))
        return out

    async def _rebuild_pool(self, *, demote: bool = True) -> bool:
        """在当前窗口上重建各轴 kept 池的去重基准（与刷新复评**同一个实现**，§2.10 / BL-5）。

        Args:
            demote: 是否按复评结果淘汰并落降级。``False``（``revalidateOnRefresh`` 关）时
                只重建基准、不淘汰任何条目——用户关掉的是「复评」，不是「重建」。
        """
        sm = get_sessionmaker()
        async with sm() as session:
            rows = await repo.list_kept_candidates(session, self.run_id)
        failures: list = []
        for axis, lane in self._lanes.items():
            ok, axis_failures = await self._rebuild_lane(lane, axis, rows)
            if not ok:
                return False
            failures.extend(axis_failures)
        if demote:
            await self._apply_demotions(failures)
            for lane in self._lanes.values():
                dropped = {cid for cid, _r in failures}
                lane.adopt([e for e in lane.pool if e.candidate_id not in dropped])
        return True

    async def _rebuild_lane(self, lane, axis: str, rows: list) -> tuple[bool, list]:
        ctx = self.lane_context()
        if axis == "factor":
            targets = [KeptRow(candidate_id=int(row.id), expr_text=row.expr_text,
                               mechanism=row.mechanism, skeleton=row.skeleton)
                       for row in rows if row.kind == "factor" and row.expr_text]
            if not targets:
                lane.adopt([])
                return True, []
            async with self._validate_sem:
                result = await run_in_threadpool(rebuild_kept_pool, targets,
                                                 self._factor_validation_ctx(ctx))
        else:
            if not any(row.kind == axis for row in rows):
                lane.adopt([])
                return True, []
            async with self._validate_sem:
                result = await run_in_threadpool(lane.rebuild, rows, ctx)
        if result.errors and not result.entries:
            logger.error("discovery run %d 轴 %s 池重建全部失败：%s",
                         self.run_id, axis, result.errors[:3])
            return False, []
        lane.adopt(result.entries)
        return True, list(result.failures)

    def _factor_validation_ctx(self, ctx: LaneContext) -> ValidationContext:
        return ValidationContext(
            df_full=ctx.df_full, horizon=int(self.spec.config.get("horizon", 6)),
            is_ratio=float(self.spec.config.get("isRatio", 0.7)),
            fee_bps=float(self.spec.settings.default_taker_fee_bps),
            slip_bps=float(self.spec.settings.default_slippage_bps),
            thresholds=normalize_thresholds(self.spec.config.get("thresholds")),
            memo=ctx.memo)

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
            frames = await self._load_frames(start_ts, end_ts)
        except (OKXError, HTTPException, asyncio.TimeoutError, OSError) as exc:
            return await self._handle_data_failure(exc, initial=initial)
        self._candles = list(candles)
        self._df = candles_to_df(candles)
        self._htf_frames = frames
        self._memo = {}
        for lane in self._lanes.values():
            lane.on_data_refresh()
        self._data_failures = 0
        self._data_backoff = 5.0
        self._checkpoint.data_window = {"startTs": int(self._df["ts"].iloc[0]),
                                        "endTs": int(self._df["ts"].iloc[-1]),
                                        "lastRefreshTs": int(time.time())}
        return True

    async def _load_frames(self, start_ts: int, end_ts: int) -> dict:
        """按模板目录的高周期并集一次性取帧（BL-6 的资源侧护栏在目录层已封顶）。"""
        catalog = self._template_catalog
        if catalog is None or not catalog.htf_bars:
            return {}
        return await load_htf_frames(
            self.spec.rest, inst_id=self.spec.inst_id, start_ts=start_ts, end_ts=end_ts,
            base_seconds=bar_to_seconds(self.spec.bar), timeframes=list(catalog.htf_bars),
            htf_warmup_bars=int(catalog.htf_warmup_bars))

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
        """到点即刷新窗口并对各轴池做一次轻量复评（跨窗口指标可比性，§2.9 / R-13）。"""
        interval = float(self.spec.config.get("dataRefreshS", 21600) or 0)
        if interval <= 0 or self._df is None:
            return self._df is not None
        last = int(self._checkpoint.data_window.get("lastRefreshTs") or 0)
        if time.time() - last < interval:
            return True
        if not await self._refresh_data():
            return False
        await self._refresh_catalogs()      # 方向要在新窗口上重新冻结（§2.4.2）
        # 重建**无条件**执行：窗口右移后池里的基准与新窗口不再等长，去重通道会逐候选抛错并被
        # 吞成 `eval_error`——表现为「刷新之后突然一条都收不到」，而不是报错。
        # `revalidateOnRefresh` 只决定要不要按复评结果淘汰，不决定要不要重建基准（§2.10）。
        if not await self._rebuild_pool(
                demote=bool(self.spec.config.get("revalidateOnRefresh", True))):
            await self._pause_with("rebuild_failed")
            return False
        return True

    # ============================ 单轮迭代 ============================
    def lane_context(self) -> LaneContext:
        split = split_index(len(self._df), float(self.spec.config.get("isRatio", 0.7)))
        return LaneContext(
            df_full=self._df, candles=self._candles, htf_frames=self._htf_frames,
            bar=self.spec.bar, split=split, split_ts=int(self._df["ts"].iloc[split]),
            config=self.spec.config, settings=self.spec.settings, memo=self._memo)

    async def _run_iteration(self) -> None:
        """一轮的编排下沉到 ``iteration.IterationRunner``（本文件已逼近 800 行清洁度上限）。"""
        await IterationRunner(self).run_once()

    def sync_axis_inputs(self) -> None:
        """每轮把上游产出同步进下游轴的取材池（就绪判据与让渡据此变化，BL-9）。

        ``strategy_combo`` 的成员**只**来自本 run ``strategy_param`` 轴的 kept 池（D-18）；
        ``factor_combo`` 的成员目录每轮把因子轴新入库的表达式拼进来。
        """
        inputs = next((lane.inputs for lane in self._lanes.values()
                       if hasattr(lane, "inputs")), None)
        if inputs is None:
            return
        inputs.members = self._compose_members()
        source = self._lanes.get("strategy_param")
        inputs.rotation_pool = tuple(source.kept_genomes()) if source is not None else ()

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
        一条还能恢复的任务会因此变成「怎么连都立刻断」的死流。
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
def _size_range(raw: object, low: int, high: int) -> tuple[int, int]:
    data = raw if isinstance(raw, dict) else {}
    lo = max(low, min(high, int(data.get("min", low) or low)))
    hi = max(lo, min(high, int(data.get("max", high) or high)))
    return lo, hi


def _template_signature(templates: tuple) -> str:
    """可挖模板目录签名（key + 参数梯子）。升级后模板改了 min/max，旧基因可能已越界；
    **不检查就会静默把越界参数当成合法基因继续繁殖**。"""
    import hashlib

    payload = [[spec.key, [[p.key, list(p.ladder)] for p in spec.params]]
               for spec in templates if spec.minable]
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()[:16]


def _hourly_budget(spec: RunSpec):
    from .llm_agents import HourlyCallBudget

    return HourlyCallBudget(int(spec.settings.discovery_llm_max_calls_per_hour))
