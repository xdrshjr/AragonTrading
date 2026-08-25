"""增量物化的编排（W-B）：epoch 状态 / 五条判据 / 水位线推进 / 成本分位漂移判定。

需求原文说「经验是累积的，随着交易的进行能够进行不断的更新」，而 `8e05dd9` 的实现是**每一次
物化都从事件表全量重建**：读全表 → 铺 44 类桶 → 逐桶统计 → 重算全部边，`materialize_max_events`
（20 万）越界之后**直接停止自动物化**并在 UI 上打一行「规模墙」。也就是说那句承诺在库变大之后
是会停下来的——而且是以一种「已经说清楚了」的姿态停下来。本模块把它做掉。

**全量重建一行不删**：它继续作为兜底与**正确性预言机**存在，两条路径的产物必须逐值相等
（门禁 G-10 / G-10b 机械钉死）。增量算错了而无人察觉，是本轮最危险的失败形态。

`costRegime` 的三分位是唯一的全局耦合（分位一变、历史桶全错），解法是把它**冻结成一个 epoch**：
每次全量重建开一个新版本，增量只在分位没漂的前提下往当前版本上累加。

**本模块不新增任何一把锁、也不新增任何后台任务**（`v1` 在这两处各踩过一个 P0）：它跑在
`service._run_materialize` 已经持有的那把物化锁里。
"""
from __future__ import annotations

import logging
import math
import time

from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from ...db import repositories as repo
from . import taxonomy as tax
from .accumulate import BucketAccumulator, HistSpec, fold_event, from_row, row
from .aggregate import Bucket, EventView, _combos_for, label_cost_regime
from .graph import (
    AccumulatorPlan,
    ContextCounts,
    MaterializeOptions,
    _event_axes,
    materialize_from_accumulators,
)

logger = logging.getLogger(__name__)

EPOCH_KEY = "experience.materialize_epoch"
HEALTH_KEY = "experience.materialize_health"      # 物化健康读数（九个键，I-8）

# 成本直方图的分箱（判据 B-2 用）。成本 bps 是小的正数，0.1 的分辨率足以判定「漂了没有」，
# 而 `cuts_drift_bps` 的默认容忍是 1.0 —— 分辨率必须明显细于容忍，否则这道判据会漏报。
_COST_BIN_BPS = 0.1
_COST_CLAMP_BPS = 20000.0
_GLOBAL_GROUP = "__global__"

# 打回全量的五个原因（每一个都在 `materialize_health.fullReason` 上有渲染落点）。
REASON_TAXONOMY = "taxonomy"
REASON_CUTS_DRIFT = "cuts_drift"
REASON_NODE_CAP = "node_cap"
REASON_NO_EPOCH = "no_epoch"
REASON_COUNT_MISMATCH = "count_mismatch"
REASON_DISABLED = "disabled"
REASON_UP_TO_DATE = "up_to_date"


def empty_epoch(*, epoch: int, cuts: dict) -> dict:
    """一个「什么都还没折进来」的 epoch 状态（全量重建的收尾据它重写累加器底座）。"""
    return {"epoch": int(epoch), "watermarkEventId": 0, "taxonomyVersion": tax.TAXONOMY_VERSION,
            "cuts": dict(cuts or {}), "costHist": {}, "nodeCount": 0, "eventCount": 0,
            "signRollTick": 0, "builtTs": int(time.time()), "lastMode": "full"}


async def read_epoch(session: AsyncSession) -> dict | None:
    """读 epoch 状态；缺席 / 坏形状 ⇒ `None`（判据 B-4 据此打回全量）。"""
    raw = await repo.get_app_setting(session, EPOCH_KEY, None)
    return raw if isinstance(raw, dict) and int(raw.get("epoch") or 0) > 0 else None


async def write_epoch(session: AsyncSession, state: dict) -> None:
    """写 epoch 状态（与累加器表**在同一把物化锁、同一次事务序列**内，见 R-2）。"""
    await repo.set_app_setting(session, EPOCH_KEY, state)


async def plan(session: AsyncSession, settings) -> dict:
    """**五条判据**：本次该走增量还是全量。纯读，不碰 CPU 段。

    任一判据不满足即回全量并 `epoch+1`（自愈）。判据本身必须可见——`fullReason` 在
    `materialize_health` 上有渲染落点，否则用户无法判断「为什么这次又走了全量」。

    | # | 判据 | 不做的后果 |
    |---|---|---|
    | B-1 | `taxonomyVersion` 与代码常量一致 | 轴集 / 折叠规则改了却按老桶继续累加 ⇒ 图与代码语义永久错位，完全静默 |
    | B-2 | 折进新事件后重算的三分位与冻结值相差 ≤ `cuts_drift_bps`，且没有新分组跨过 `cost_regime_min_samples` | 分位漂了却继续用旧标签 ⇒「今天的 low 和半年前的 low 不是一回事」，缓慢失真且不可察觉 |
    | B-3 | `nodeCount` ≤ `graph_node_cap` | 增量只加节点、不施加全局 top-N 截断 ⇒ 图无界增长 |
    | B-4 | epoch 存在、版本可识别、（已折进过事件时）累加器表非空 | 冷启动 / 手工清库后从一个空底座上继续加 ⇒ 图**永久残缺** |
    | B-5 | `COUNT(id ≤ watermark AND origin IN STAT_ORIGINS) == eventCount` | id 在 INSERT 时分配、在 COMMIT 时可见，多成员会话交错提交完全可能让「id 100–105 比 106–110 后提交」；水位线一旦推到 110，100–105 **永久不被折进累加器**且无人发现——一条静默丢样本的路径 |

    Args:
        session: DB 会话。
        settings: 已经过 `resolve_experience_settings` 派生的 `Settings`。

    Returns:
        `{"mode": "full"|"incremental", "reason": str, "epoch": dict|None, "rows": list,
          "pending": int}`；`mode=="incremental"` 时 `rows` 是**已读好**的本批事件行
        （避免调用方再读一遍；`plan` 与 `step` 因此看到的一定是同一批）。
    """
    if not bool(settings.ai_trade_experience_incremental_enabled):
        return _full(REASON_DISABLED)
    state = await read_epoch(session)
    if state is None:
        return _full(REASON_NO_EPOCH)
    if int(state.get("taxonomyVersion") or 0) != tax.TAXONOMY_VERSION:
        return _full(REASON_TAXONOMY)
    if int(state.get("nodeCount") or 0) > int(settings.ai_trade_experience_graph_node_cap):
        return _full(REASON_NODE_CAP)
    event_count = int(state.get("eventCount") or 0)
    if event_count > 0 and await repo.count_experience_buckets(
            session, int(state["epoch"])) <= 0:
        return _full(REASON_NO_EPOCH)
    watermark = int(state.get("watermarkEventId") or 0)
    settled = await repo.count_experience_events(session, up_to_id=watermark)
    if settled != event_count:
        logger.info("ai-trade experience watermark reconciliation failed: %d != %d",
                    settled, event_count)
        return _full(REASON_COUNT_MISMATCH)
    batch = int(settings.ai_trade_experience_incremental_batch)
    rows = await repo.load_experience_events(
        session, repo.ExperienceEventQuery(after_id=watermark, limit=batch))
    if not rows:
        return {"mode": "incremental", "reason": REASON_UP_TO_DATE, "epoch": state,
                "rows": [], "pending": 0}
    drift = _cuts_drift(state, rows, settings)
    if drift is not None:
        logger.info("ai-trade experience cost cuts drifted (%s) — falling back to full", drift)
        return _full(REASON_CUTS_DRIFT)
    # 满批 ⇒ 本次只吃这一批，脏标志由调用方保留、下一次继续。`pendingEvents` 必须**如实**回报：
    # 一个悄悄吃不完的队列在 UI 上与「已经跟上了」长得一模一样。
    pending = (0 if len(rows) < batch
               else max(0, await repo.count_experience_events(session)
                        - event_count - len(rows)))
    return {"mode": "incremental", "reason": "ok", "epoch": state, "rows": rows,
            "pending": pending}


def _full(reason: str) -> dict:
    return {"mode": "full", "reason": reason, "epoch": None, "rows": [], "pending": 0}


async def step(session: AsyncSession, settings, planned: dict, *,
               events: list[EventView], existing: dict) -> dict:
    """跑一次增量：折进本批事件 → 落累加器 → 重算被碰过的桶 → 全量重算四类边 → 推进水位线。

    **绝不回看历史事件**：`applies_in` 由签名产（★ E-1）、`co_occurs` 与 `context.support` 由
    语境计数器产（★ E-10）、`contradicts` 由节点表两两比。这就是「成本与库大小近似无关」。

    Args:
        session: DB 会话（调用方持物化锁）。
        settings: 派生后的 `Settings`。
        planned: `plan()` 的产物（含 epoch 状态与**已读好**的本批事件行）。
        events: `planned["rows"]` 经调用方转成的事件视图。
        existing: 库里既有的经验节点（按 key → `GraphNode`）。

    Returns:
        `{"nodes","edges","stale","unmuted","inserted","updated","events","elapsedMs",
          "mode","epoch","pendingEvents"}`。
    """
    started = time.time()
    state = dict(planned["epoch"])
    epoch = int(state["epoch"])
    spec = HistSpec.from_settings(settings)
    cuts = dict(state.get("cuts") or {})
    base = {"buckets": await repo.load_experience_buckets(session, epoch),
            "counts": await repo.load_experience_context_counts(session, epoch),
            # ⚠️ 历史直方图**必须**传进去。只折本批会让 B-2 每次都拿一个「只有最近 2 万条」的
            # 分布去和冻结分位比——分位看起来永远没漂，那道判据就成了一句摆设。
            "costHist": state.get("costHist") or {}}
    folded = await run_in_threadpool(_fold_batch, events, base, cuts, spec)
    # 两张底座表**合成一次提交**：它们同源同刻，分两次只是白付一次 SQLite 的提交延迟
    # （G-15 实测：一次增量的耗时里绝大部分是提交而不是计算）。
    await repo.upsert_experience_buckets(session, epoch, folded["bucket_rows"], commit=False)
    await repo.upsert_experience_context_counts(session, epoch, folded["count_rows"],
                                                commit=False)
    await session.commit()
    state["eventCount"] = int(state.get("eventCount") or 0) + len(events)
    state["costHist"] = folded["cost_hist"]
    counts = ContextCounts(single=folded["single"], pairs=folded["pairs"],
                           total=int(state["eventCount"]))
    graph_plan = await run_in_threadpool(
        materialize_from_accumulators,
        AccumulatorPlan(entries=folded["entries"], counts=counts, spec=spec,
                        roll_keys=folded["touched"]),
        _options(settings), existing)
    graph_plan.cost_regime_cuts = cuts
    # ★ E-1（评审 R-1）· 传给 `mark_stale_lessons` / `unmute_reappeared` 的必须是
    # **本次算出的全部经验节点 key**（`seen_lesson_keys`），与全量路径逐字同一个集合。
    #
    # 两个坑各踩一次就够了：
    # ① 传 `touched`（本批碰过的那十几个桶）⇒ 那两个函数是**全表扫 + 反向标记**，一个 tick
    #    之内整库经验会被置成 `muted=stale` ⇒ `retrieval._collect` 过滤 muted ⇒ L0/L1 当场变空，
    #    而 tsc / ruff / 全量用例全绿、日志一行都没有（A-39 是它的十秒复现法）；
    # ② 传累加器的 key（**桶签名**）⇒ 它与经验节点的 key（`lesson:<hex>`）**不是一个命名空间**，
    #    于是没有一条能匹配上，后果与 ① 逐字相同、但更难看出来。
    written = await _persist(session, graph_plan, existing, set(graph_plan.seen_lesson_keys))
    state.update({
        # 空批（`up_to_date`）合法：手动重建会走到这里——它要的是「按新阈值重新推导一遍图」，
        # 而不是「吃新事件」。此时水位线原地不动，`max(...)` 对空序列会抛，故必须给 default。
        "watermarkEventId": max(int(state.get("watermarkEventId") or 0),
                                max((int(r.get("id") or 0) for r in planned["rows"]),
                                    default=0)),
        "nodeCount": len(graph_plan.seen_lesson_keys),
        "signRollTick": int(state.get("signRollTick") or 0) + 1,
        "builtTs": int(time.time()), "lastMode": "incremental"})
    await write_epoch(session, state)
    return {**written, "events": len(events), "mode": "incremental", "epoch": epoch,
            "pendingEvents": int(planned.get("pending") or 0),
            "elapsedMs": round((time.time() - started) * 1000, 1)}


async def seed_from_full(session: AsyncSession, settings, events: list[EventView], *,
                         outcome: dict) -> dict:
    """全量重建的收尾：把本次算出的累加器与语境计数器写成**下一次增量的底座**。

    没有这一步，全量重建之后的第一次物化会因判据 B-4 再次打回全量——增量永远起不来。
    每次全量都开一个**新 epoch** 并清掉旧版本（旧分位再也不会被用来解释任何一条经验了）。

    Args:
        session: DB 会话（调用方持物化锁）。
        settings: 派生后的 `Settings`。
        events: 本次全量重建吃进去的全部事件。
        outcome: `{"cuts": 本次算出的成本分位（冻结进 epoch）,
            "lastId": 本次吃进去的最大事件 id（水位线）, "nodeCount": 本次产出的经验节点数}`。

    Returns:
        写好的 epoch 状态。
    """
    cuts = dict(outcome.get("cuts") or {})
    prior = await read_epoch(session)
    epoch = int((prior or {}).get("epoch") or 0) + 1
    spec = HistSpec.from_settings(settings)
    folded = await run_in_threadpool(_fold_batch, events, {}, cuts, spec)
    await repo.upsert_experience_buckets(session, epoch, folded["bucket_rows"])
    await repo.upsert_experience_context_counts(session, epoch, folded["count_rows"])
    await repo.purge_experience_epochs(session, epoch)
    state = empty_epoch(epoch=epoch, cuts=cuts)
    state.update({"watermarkEventId": int(outcome.get("lastId") or 0),
                  "eventCount": len(events),
                  "costHist": folded["cost_hist"],
                  "nodeCount": int(outcome.get("nodeCount") or 0),
                  "signRollTick": int((prior or {}).get("signRollTick") or 0) + 1})
    await write_epoch(session, state)
    return state


async def run_locked(session: AsyncSession, settings, *, planned: dict | None = None) -> dict:
    """在**调用方已持有的**物化锁内跑一次物化（增量或全量）+ 效用刷新。

    ★ **E-13 · 锁的作用域上提到调用方**：`utility.refresh` 要写 `muted` / `mute_reason`，与
    `mark_stale_lessons` / `unmute_reappeared` 写的是同一批列。跑在锁外会丢更新（把
    `ineffective` 覆盖成 `stale`，或反过来），而且是那种「数字不对但不报错」的丢法。
    本函数**不新增第二把锁、不新增后台任务**——它只是把两件本来就该串行的事放进同一个临界区。

    进临界区的第一件事是 `_revalidate`：`materialize_bounded` 的 `plan()` 跑在锁**外**，
    等锁期间那份计划可能已经被一次手动「强制全量」作废（见该函数的 docstring）。重算出来的
    计划若判为全量，就照全量跑——此时规模闸已经在外层放行过一次，而「epoch 刚动过」本身就是
    一个必须重建的信号。

    Args:
        session: DB 会话。
        settings: 派生后的 `Settings`。
        planned: `plan()` 的产物；`None` 或 `mode=="full"` ⇒ 走全量重建。

    Returns:
        物化回执（含 `mode` / `fullReason` / `pendingEvents` 三个健康键）。
    """
    from . import utility
    from .service import _event_views, rebuild

    if planned is not None and planned["mode"] == "incremental":
        planned = await _revalidate(session, settings, planned)
    if planned is not None and planned["mode"] == "incremental":
        existing = {row_["key"]: _node_from_row(row_) for row_ in
                    await repo.load_experience_nodes(session, kinds=("lesson",))}
        result = await step(session, settings, planned,
                            events=_event_views(planned["rows"]), existing=existing)
        result["fullReason"] = None
    else:
        result = await rebuild(session, settings)
        result.update({"mode": "full", "pendingEvents": 0,
                       "fullReason": (planned or {}).get("reason")})
    if bool(settings.ai_trade_experience_utility_enabled):
        result["utility"] = await utility.refresh(session, settings)
    return result


async def _revalidate(session: AsyncSession, settings, planned: dict) -> dict:
    """进入临界区之后**重新对一次表**：锁外算出的计划可能已经过期。

    `materialize_bounded` 为了让规模闸看得见 `mode`，把 `plan()` 跑在物化锁**外**；而
    `POST /api/experience/rebuild`（「强制全量」）不受 `_MATERIALIZE_BUSY` 那个进程级单槽约束，
    完全可能在这中间抢到锁、`epoch+1` 并 `purge_experience_epochs` 掉旧版本的累加器。此时照着
    旧计划往下走 = **在一个刚被清空的底座上折进本批** ⇒ 整张图被一批只含最后一批事件的节点
    替换、其余全部被 `mark_stale_lessons` 判为沉降，而且一行日志都没有。B-5 的对账会在下一个
    tick 自愈，但那之间图是错的——本轮最危险的失败形态（R-1）恰好长这个样子。

    判据取 `(epoch, watermarkEventId, eventCount)` 三元组：它们是 `step()` 唯一会前滚的三个量，
    任何一个变了就说明这份计划描述的已经不是当前这张表了。

    Args:
        session: DB 会话（调用方持物化锁）。
        settings: 派生后的 `Settings`。
        planned: 锁外算出的计划。

    Returns:
        仍然成立 ⇒ 原样返回；已过期 ⇒ 在锁内重新 `plan()` 一次的产物。
    """
    stale = planned.get("epoch") or {}
    fresh = await read_epoch(session)
    if fresh is not None and all(
            int(fresh.get(key) or 0) == int(stale.get(key) or 0)
            for key in ("epoch", "watermarkEventId", "eventCount")):
        return planned
    logger.info("ai-trade experience incremental plan went stale (epoch moved) — replanning")
    return await plan(session, settings)


def _node_from_row(row_: dict):
    """行 → `GraphNode`（转发 `graph.node_from_row`，避免本模块顶层再引一个符号）。"""
    from .graph import node_from_row

    return node_from_row(row_)


async def _note_materialized(session: AsyncSession, settings, result: dict) -> None:
    """一次**成功**物化的收尾：落健康读数 + 清脏标志（后台与手动重建共用）。"""
    elapsed = float(result.get("elapsedMs") or 0.0)
    pending = int(result.get("pendingEvents") or 0)
    await _write_health(session, {
        "lastTs": int(time.time()), "lastMs": elapsed, "events": int(result.get("events") or 0),
        "slow": elapsed >= float(settings.ai_trade_experience_materialize_slow_ms),
        "torn": False, "overflow": False,
        # ★ W-B 的三个可见性键：不给它们落点，用户无法判断「为什么这次又走了全量」，也看不出
        # 一个悄悄吃不完的队列（`pendingEvents`）与「已经跟上了」的区别。
        "mode": str(result.get("mode") or "full"), "fullReason": result.get("fullReason"),
        "pendingEvents": pending})
    # 满批 ⇒ 还有剩余 ⇒ 脏标志**保留**，下一次接着吃（`_mark_dirty(False)` 会让它停在半路）。
    from .service import _mark_dirty

    await _mark_dirty(pending > 0, session=session)


async def _note_torn(session: AsyncSession, started: float) -> None:
    """★ E-14 · 撕裂如实落库。

    先 `rollback`：`rebuild` 的四次提交里任何一次失败都会让这个 `AsyncSession` 停在失败事务上，
    此后第一条语句必抛 `PendingRollbackError` —— 那会把「让撕裂可见」变成「连撕裂都记不下来」。
    """
    try:
        await session.rollback()
        await _write_health(session, {
            "lastTs": int(time.time()), "lastMs": round((time.time() - started) * 1000, 1),
            "events": 0, "slow": False, "torn": True, "overflow": False})
    except Exception as exc:  # noqa: BLE001 —— 记录失败不能盖住真正的那个异常
        logger.warning("ai-trade experience torn flag not persisted: %s", exc)


async def _debounce_ok(session: AsyncSession, settings) -> bool:
    """距上次成功物化是否已超过去抖窗口（`materialize_min_interval_s`，默认 60 秒）。"""
    health = await _read_health(session)
    interval = float(settings.ai_trade_experience_materialize_min_interval_s)
    return (time.time() - float(health.get("lastTs") or 0)) >= interval


async def _read_health(session: AsyncSession) -> dict:
    raw = await repo.get_app_setting(session, HEALTH_KEY, {})
    return raw if isinstance(raw, dict) else {}


async def _write_health(session: AsyncSession, health: dict) -> None:
    await repo.set_app_setting(session, HEALTH_KEY, health)


def health_wire(health: dict) -> dict:
    """`materialize_health` 的 wire（**唯一**换算点：概览条与 `/health` 轮询接口共用）。"""
    return {"lastTs": int(health.get("lastTs") or 0), "lastMs": float(health.get("lastMs") or 0.0),
            "events": int(health.get("events") or 0), "slow": bool(health.get("slow")),
            "torn": bool(health.get("torn")), "overflow": bool(health.get("overflow")),
            "mode": str(health.get("mode") or ""), "fullReason": health.get("fullReason"),
            "pendingEvents": int(health.get("pendingEvents") or 0)}


async def health_view(session: AsyncSession, settings) -> dict:
    """`GET /api/experience/health`：物化健康 + epoch 现状 + 已自动静音条数（**轮询专用**）。

    从 `overview` 里拆出来的理由很实在：概览条与启动弹窗都要轮询它，而 `overview` 会扫整张
    节点表。把一个轮询接口挂在一次全表扫上，是那种「先能跑、后来变慢、再也查不出为什么」的
    典型来源。`utilityMuted` 也在这里下发——启动弹窗那行「已自动静音 N 条」因此**零新增请求**。
    """
    from . import utility

    state = await read_epoch(session) or {}
    return {**health_wire(await _read_health(session)),
            "epoch": int(state.get("epoch") or 0),
            "watermarkEventId": int(state.get("watermarkEventId") or 0),
            "eventCount": int(state.get("eventCount") or 0),
            "lastMode": str(state.get("lastMode") or ""),
            "utilityMuted": await utility.muted_count(session, settings)}


# ---------------- 纯计算段（跑在 `run_in_threadpool` 里） ----------------
def _fold_batch(events: list[EventView], base: dict, cuts: dict, spec: HistSpec) -> dict:
    """把一批事件折进（可能为空的）既有底座；**单趟、O(len(events) × 桶数)**。

    Args:
        events: 本批事件（`id > watermark` 的那一批）。
        base: 既有底座 `{"buckets": 累加器行, "counts": 语境计数行, "costHist": 成本直方图}`。
        cuts: 本 epoch **冻结**的成本分位。
        spec: 直方图与有界集合的旋钮。

    Returns:
        `{"entries","touched","live_keys","bucket_rows","count_rows","single","pairs","cost_hist"}`。
    """
    stored = base.get("buckets") or []
    counts_rows = base.get("counts") or []
    accumulators = {row_["key"]: from_row(row_) for row_ in stored}
    buckets = {row_["key"]: Bucket(signature=dict(row_.get("signature") or {}),
                                   scope=row_.get("scope") or "symbol")
               for row_ in stored}
    single = {r["a"]: int(r["count"]) for r in counts_rows if not r["b"]}
    pairs = {(r["a"], r["b"]): int(r["count"]) for r in counts_rows if r["b"]}
    # 只回写**本批真的变过**的语境计数：整表重写在稳态下是几千行换十几行的改动。
    touched_single: set[str] = set()
    touched_pairs: set[tuple[str, str]] = set()
    cost_hist: dict[str, dict[str, int]] = {group: dict(bins) for group, bins
                                            in (base.get("costHist") or {}).items()}
    touched: set[str] = set()
    for event in events:
        _fold_one(event, {"accumulators": accumulators, "buckets": buckets,
                          "touched": touched, "costHist": cost_hist}, cuts, spec)
        keys = sorted(tax.canonical_key(axis, value)
                      for axis, value in _event_axes(event, cuts).items())
        for key in keys:
            single[key] = single.get(key, 0) + 1
            touched_single.add(key)
        for index, left in enumerate(keys):
            for right in keys[index + 1:]:
                pairs[(left, right)] = pairs.get((left, right), 0) + 1
                touched_pairs.add((left, right))
    entries = [(buckets[key], accumulators[key]) for key in sorted(accumulators)]
    return {
        "entries": entries, "touched": touched,
        "bucket_rows": [{"key": key, "signature": buckets[key].signature,
                         "scope": buckets[key].scope, **row(accumulators[key])}
                        for key in sorted(touched)],
        "count_rows": ([{"a": key, "b": "", "count": single[key]}
                        for key in sorted(touched_single)]
                       + [{"a": a, "b": b, "count": pairs[(a, b)]}
                          for a, b in sorted(touched_pairs)]),
        "single": single, "pairs": pairs, "cost_hist": cost_hist,
    }


def _fold_one(event: EventView, state: dict, cuts: dict, spec: HistSpec) -> None:
    """把**一条**事件折进桶累加器与成本直方图（就地改 `state`）。

    ★ 与 `aggregate.enumerate_buckets` 逐字同构：同一条事件的组合**必须先去重再落桶**。
    此前那个坑的形态是：三阶的跨标的同形桶与二阶的同签名桶逐字相同，于是同一条事件被数两遍
    ⇒ `n` 翻倍、而 `t = mean/(std/√n)` 随 √n 增大 ⇒ **一条只有 10 个样本的经验拿着
    「20 笔、t=3.5」晋升成铁律**，然后把那个假数字逐字写进喂给模型的 prompt（违反 I-9）。
    """
    accumulators, buckets = state["accumulators"], state["buckets"]
    regime, fallback = label_cost_regime(event, cuts)
    signature = dict(event.context)
    signature[tax.AXIS_COST_REGIME] = regime
    signature.setdefault(tax.AXIS_SYMBOL, event.inst_id)
    signature.setdefault(tax.AXIS_BAR, event.bar)
    _fold_cost(state["costHist"], event)
    seen: set[str] = set()
    for combo in _combos_for(signature):
        key = tax.signature_key(combo)
        if key in seen:
            continue
        seen.add(key)
        if key not in accumulators:
            accumulators[key] = BucketAccumulator()
            buckets[key] = Bucket(signature=combo,
                                  scope="symbol" if tax.AXIS_SYMBOL in combo else "global")
        fold_event(accumulators[key], event, spec=spec, cost_fallback=fallback)
        state["touched"].add(key)


def _fold_cost(cost_hist: dict[str, dict[str, int]], event: EventView) -> None:
    """把一条事件的 `cost_bps` 折进直方图（判据 B-2 的输入；`None` ⇒ 不折）。"""
    if event.cost_bps is None:
        return
    index = str(int(math.floor(max(-_COST_CLAMP_BPS, min(_COST_CLAMP_BPS,
                                                         float(event.cost_bps)))
                               / _COST_BIN_BPS)))
    for group in (_GLOBAL_GROUP, f"{event.inst_id}|{event.bar}"):
        bucket = cost_hist.setdefault(group, {})
        bucket[index] = int(bucket.get(index, 0)) + 1


def _cuts_drift(state: dict, rows: list[dict], settings) -> str | None:
    """判据 B-2：把本批成本折进直方图后重算三分位，与冻结值比。没漂 ⇒ `None`。

    第二个半条同样重要：**没有新的 `(instId, bar)` 分组跨过 `cost_regime_min_samples`**。
    一个此前样本不足、只能用全库分位的分组一旦够了样本，它下面每一条事件的 `costRegime`
    标签都会变——那等于桶身份变了，继续在旧桶上累加就是在拿两把尺子量同一批交易。
    """
    hist = {group: dict(bins) for group, bins in (state.get("costHist") or {}).items()}
    for raw in rows:
        cost = raw.get("costBps")
        if cost is None:
            continue
        index = str(int(math.floor(max(-_COST_CLAMP_BPS, min(_COST_CLAMP_BPS, float(cost)))
                                   / _COST_BIN_BPS)))
        for group in (_GLOBAL_GROUP, f"{raw.get('instId')}|{raw.get('bar')}"):
            bucket = hist.setdefault(group, {})
            bucket[index] = int(bucket.get(index, 0)) + 1
    cuts = state.get("cuts") or {}
    tolerance = float(settings.ai_trade_experience_cuts_drift_bps)
    min_samples = int(settings.ai_trade_experience_cost_regime_min_samples)
    for group, bins in sorted(hist.items()):
        total = sum(bins.values())
        frozen = (cuts.get("global") if group == _GLOBAL_GROUP
                  else (cuts.get("byScope") or {}).get(group))
        qualifies = total >= 3 and (group == _GLOBAL_GROUP or total >= min_samples)
        if frozen is None:
            if qualifies:
                return f"{group}:new_group"
            continue
        if not qualifies:
            continue
        fresh = _tertiles_from_hist(bins, total)
        if fresh is None:
            continue
        if any(abs(float(a) - float(b)) > tolerance for a, b in zip(fresh, frozen)):
            return f"{group}:{fresh}!={frozen}"
    return None


def _tertiles_from_hist(bins: dict[str, int], total: int) -> list[float] | None:
    """从成本直方图还原两个三分位（与 `aggregate._tertiles` **同一个取秩规则**）。"""
    if total < 3:
        return None
    order = sorted((int(index), int(count)) for index, count in bins.items())
    return [_value_at_rank(order, total // 3), _value_at_rank(order, (2 * total) // 3)]


def _value_at_rank(order: list[tuple[int, int]], rank: int) -> float:
    """升序箱序列里第 `rank`（0 基）个样本的**箱心**值。"""
    seen = 0
    for index, count in order:
        seen += count
        if rank < seen:
            return round((index + 0.5) * _COST_BIN_BPS, 4)
    return round((order[-1][0] + 0.5) * _COST_BIN_BPS, 4)


def _options(settings) -> MaterializeOptions:
    """与 `service._options` **同一份**装配（两处各写一份必漂移，而漂的是晋升门槛）。"""
    from .service import _options as build

    return build(settings)


async def _persist(session: AsyncSession, graph_plan, existing: dict,
                   live_keys: set[str]) -> dict:
    """落节点 / 解静音 / 标沉降 / 换边——与 `service.rebuild` **逐字同一条写序列**。"""
    from .service import _node_payload

    payload = [_node_payload(node) for node in graph_plan.nodes]
    upserted = await repo.upsert_lesson_nodes(session, payload)
    unmuted = await repo.unmute_reappeared_experience_lessons(session, live_keys)
    stale = await repo.mark_stale_experience_lessons(session, live_keys)
    edges = await repo.replace_experience_edges(
        session, [{"src_key": e.src_key, "dst_key": e.dst_key, "rel": e.rel,
                   "weight": e.weight, "support": e.support} for e in graph_plan.edges],
        {**{key: node.id for key, node in existing.items() if node.id}, **upserted["keys"]})
    return {"nodes": len(payload), "edges": edges, "stale": stale, "unmuted": unmuted,
            "inserted": upserted["inserted"], "updated": upserted["updated"]}
