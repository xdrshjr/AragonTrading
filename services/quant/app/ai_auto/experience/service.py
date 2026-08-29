"""经验层的**装配与编排**（§五 的端点实现 + 启动期取数）—— 判据全在纯核心，本模块只取数与装配。

沿用本仓库已成文的分工（`discipline_runtime.py` / `track_record_service.py`）：判据在
`aggregate` / `promote` / `retrieval` / `disclosure` 四个纯函数模块，本层负责 DB 会话、线程池
卸载与 wire 装配。

两条纪律：

- **物化只走 `repo.upsert_lesson_nodes` 一个写路径**（评审 P0-2 / 放行条件 2）。本模块不写任何
  第二份 SQL。
- **重建是 CPU 密集的**（几万条事件 × 二十余类桶），故纯计算部分走 `run_in_threadpool`
  （沿用 `run_backtest` / `prior_service` 的既有范式），绝不在事件循环上跑。
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from ...db import repositories as repo
from . import incremental
from . import taxonomy as tax
# 「物化健康读数」整块住在 `incremental.py`（它就是 mode / fullReason / pendingEvents 的产地），
# 这里**再导出**而不是让六个调用点改 import——`importing.py` 是同一条纪律的成文先例。
from .incremental import (  # noqa: F401
    _debounce_ok, _note_materialized, _note_torn, _read_health, _write_health, health_view)
from .aggregate import EventView
from .disclosure import budget_floor, build_wire, experience_tier, format_l0, format_l1
from .graph import (
    Graph,
    GraphNode,
    MaterializeOptions,
    build_graph,
    materialize,
    node_from_row,
)
from .promote import PromoteRule
from .retrieval import ContextSeed, RetrievalConfig, retrieve
from .rules import ExperienceRule

logger = logging.getLogger(__name__)

COST_CUTS_KEY = "experience.cost_regime_cuts"
HEALTH_KEY = incremental.HEALTH_KEY   # 规模墙 / 增量模式的露出点（九个键，I-8）；产地在 incremental
DIRTY_KEY = "experience.dirty"                    # ★ E-5：脏标志必须能跨进程活下来
HARVEST_GAP_KEY = "experience.harvest_gap_total"  # ★ E-8：`harvestGapTotal` 的唯一取数来源
REJECTED_KEY = "experience.rejected_narrative"

# ★ E-6（评审 P0-6）· 物化的并发控制是**进程级单槽**，不是 `asyncio.timeout`。
#
# `asyncio` 的取消只能解除 await 者——`run_in_threadpool` 里那个已经开跑的工作线程会把整轮全库
# 重建跑完。于是「20 秒后判为结束 ⇒ 在飞标志被清 ⇒ 60 秒后去抖窗口过期 ⇒ 再起一个」在大库上
# 变成**每 60 秒叠一个永不完成的全库重建**，吃满 anyio 默认 40 个工作线程；而那个线程池是
# **全进程共用**的，回测 / 因子挖掘 / 打分预览全都排在它后面。
#
# 修法：这个布尔的生命周期必须覆盖「线程里的真实工作」全程（`finally` 释放），忙时只置脏标志，
# 绝不叠第二个任务。超时的语义相应收窄为「排队等 `materialize_lock` 最多等多久」（用例 A-52）。
_MATERIALIZE_BUSY = False

# 等锁的轮询间隔（秒）。只在**已经有人在物化**时才会走到，故 50ms 的粒度既不空转也不迟钝。
_LOCK_POLL_S = 0.05

# ★ E-13 · 当前持有物化锁的那个 task。锁的作用域从 `rebuild()` 内部**上提**到 `_run_materialize`
# （使 `utility.refresh` 与 `mark_stale_lessons` 写同一批列时不会并发丢更新），而 `rebuild()`
# 自己那一行 `async with _materialize_lock(...)` **一行不删**——它对同一个 task 变成空操作。
# 记 task 而不是记布尔：布尔会让「另一个协程也在物化」被误判成重入，那是一次真正的数据竞争。
_LOCK_HOLDER = None


@dataclass(frozen=True)
class SessionGraph:
    """会话启动时的一次性快照：图 + 库存总量 + 快照时刻（**三者同一次取数**，不变式 I-9）。

    把它们绑成一个对象是评审 F-3 的修法：L0 页眉那两个整数要被逐字喂给下真单的模型，
    图与总量各取各的会让「来自 0 组有成交记录的自动交易的 0 笔已结算往返」紧跟着三条铁律出现。
    **在 prompt 里写一个假数字，比不写这一段更坏。**
    """

    graph: Graph
    totals: dict          # {"lessons": int, "events": int, "sessions": int}
    as_of_ts: int


def _options(settings) -> MaterializeOptions:
    return MaterializeOptions(
        rule=PromoteRule.from_settings(settings),
        co_min_weight=float(settings.ai_trade_experience_co_edge_min_weight),
        co_min_support=int(settings.ai_trade_experience_co_edge_min_support),
        cost_regime_min_samples=int(settings.ai_trade_experience_cost_regime_min_samples),
        node_cap=int(settings.ai_trade_experience_graph_node_cap),
        manual_seed_weight=float(settings.ai_trade_experience_manual_seed_weight))


def _event_views(rows: list[dict]) -> list[EventView]:
    """DB 行 → 聚合层视图（**唯一**转换点：聚合层不认识 ORM，也不认识 wire 键名）。"""
    return [EventView(
        inst_id=row["instId"], bar=row["bar"], context=dict(row.get("context") or {}),
        cost_basis=row.get("costBasis") or "gross", fee_known=bool(row.get("feeKnown")),
        net_bps=row.get("netBps"), gross_bps=row.get("grossBps"), cost_bps=row.get("costBps"),
        edge_error_bps=row.get("edgeErrorBps"), hold_seconds=float(row.get("holdSeconds") or 0.0),
        exit_wall_ts=int(row.get("exitWallTs") or row.get("exitTs") or 0),
        seq=int(row.get("seq") or 0), group_id=row.get("groupId") or "") for row in rows]


# ---------------- 启动期取数（决策轮零 IO 的前提，不变式 I-3） ----------------
async def load_session_graph(session: AsyncSession, *, settings,
                             rule: ExperienceRule | None) -> SessionGraph | None:
    """会话启动时**一次性**载入内存快照；未装配 / 空图 / 取数失败 ⇒ `None`。

    这是这份数据的**唯一**取数点。会话内不刷新——刷新需要在盯市 tick 上开 DB 会话，那是一条今天
    不存在的 IO 面，而收益（几小时内经验几乎不变）远小于风险（决策轮阻塞 = 真实资金错过行情）。
    沿用 `resolve_track_record` 的同款纪律。会话内的新鲜度由后台物化 + `adopt_snapshot` 承担。

    **`totals` 必须与图出自同一次取数**（不变式 I-9 / 评审 F-3）：它会被逐字渲染进 L0 页眉喂给
    要下真单的模型。

    取数失败一律降级为「无经验」——一个读不到历史的增强读数绝不能挡住真实资金会话的启动。
    """
    if rule is None or not rule.enabled:
        return None
    try:
        nodes = await repo.load_experience_nodes(
            session, limit=int(settings.ai_trade_experience_graph_node_cap))
        if not any(n["kind"] == "lesson" for n in nodes):
            return None
        edges = await repo.load_experience_edges(session, [n["id"] for n in nodes])
        stock = await repo.experience_overview(session)
    except Exception as exc:  # noqa: BLE001 —— 见 docstring：绝不阻断启动
        logger.info("ai-trade experience graph skipped: %s", exc)
        return None
    as_of = int(time.time())
    return SessionGraph(graph=build_graph(nodes, edges, as_of_ts=as_of),
                        totals=_totals_wire(stock), as_of_ts=as_of)


def _totals_wire(stock: dict) -> dict:
    """`repo.experience_overview` 的行 → L0 页眉要的三个整数（**唯一**换算点，I-9）。"""
    return {"lessons": sum((stock.get("lessons") or {}).values()),
            "events": int(stock.get("events") or 0),
            "sessions": int(stock.get("sessions") or 0)}


# ---------------- ★ E-3（评审 P0-3）：可写配置的唯一派生点 ----------------
# 设置中心可写的 15 项阈值：**一张表同时承载 wire 键、`app_settings` 稀疏键、`Settings` 字段**。
#
# 三者必须写在同一行（不变式 I-10）。分成三份表的后果已有成文前科：某一项在 `PUT` 里被接住、
# 在 `app_settings` 里被写下、却没有任何一行把它并回运行时真正读的那个对象——用户改得动、
# 界面有反应、系统行为毫无变化，而且**不报任何错**。
_SETTING_BRIDGE: tuple[tuple[str, str, str, type], ...] = (
    ("defaultOn", "experience.default_on", "ai_trade_experience_default_on", bool),
    ("writeEnabled", "experience.write_enabled", "ai_trade_experience_write_enabled", bool),
    ("minSamples", "experience.min_samples", "ai_trade_experience_min_samples", int),
    ("ironSamples", "experience.iron_samples", "ai_trade_experience_iron_samples", int),
    ("tThreshold", "experience.t_threshold", "ai_trade_experience_t_threshold", float),
    ("halfLifeDays", "experience.half_life_days", "ai_trade_experience_half_life_days", float),
    ("staleDays", "experience.stale_days", "ai_trade_experience_stale_days", float),
    ("l0Max", "experience.l0_max", "ai_trade_experience_l0_max", int),
    ("l0MaxChars", "experience.l0_max_chars", "ai_trade_experience_l0_max_chars", int),
    ("l1Max", "experience.l1_max", "ai_trade_experience_l1_max", int),
    ("l1MaxChars", "experience.l1_max_chars", "ai_trade_experience_l1_max_chars", int),
    ("distillEnabled", "experience.distill_enabled",
     "ai_trade_experience_distill_enabled", bool),
    # ★ W-C · 效用闭环的三项可写阈值。漏一项就是一个「改得动、永远不生效」的假控件（G-9 逐项覆盖）。
    ("utilityEnabled", "experience.utility_enabled",
     "ai_trade_experience_utility_enabled", bool),
    ("utilityMinSamples", "experience.utility_min_samples",
     "ai_trade_experience_utility_min_samples", int),
    ("utilityTThreshold", "experience.utility_t_threshold",
     "ai_trade_experience_utility_t_threshold", float),
)


def setting_bridge() -> tuple[tuple[str, str, str, type], ...]:
    """可写阈值的桥表（`api/app_settings.py`、本模块的派生点与门禁 G-9 共用；**绝不各抄一份**）。

    Returns:
        `(wire 键, app_settings 键, Settings 字段, 类型)` 的元组，15 项。
    """
    return _SETTING_BRIDGE


async def resolve_experience_settings(session: AsyncSession, settings):
    """把 `app_settings["experience.*"]` 并回 `Settings` 的**唯一**派生点（不变式 I-10）。

    **为什么必须有这一步**（评审 P0-3）：`RetrievalConfig.from_settings` / `PromoteRule.from_settings`
    / `_options(settings)` 读的全是 pydantic `Settings`，而 `PUT /api/settings/experience` 写的是
    `app_settings` 稀疏 KV——两者之间此前**没有任何桥**。不补这一步，设置中心那个「阈值设置」
    面板就是一个用户改得动、系统永远不读、而且不报错的假面板。

    镜像 `api/ai_auto.py::_resolve_discipline` 的成文姿态：产出**派生副本**
    （`settings.model_copy(update=...)`），**绝不 `setattr` 全局单例**——`get_settings()` 是进程级
    缓存，且 `LiveExecutor` 被 `ai_auto` 与 `live_auto` 共用同一个对象，就地改会让阈值静默溢出到
    非 AI 链路（本仓库的历史坑）。

    Args:
        session: DB 会话。
        settings: 基线 `Settings`（实盘启动时已经过 `_resolve_discipline` 派生）。

    Returns:
        并入 DB 覆盖值后的 `Settings` 派生副本；无任何覆盖 ⇒ 返回**同一个对象**（`is` 相等，
        既有路径逐字节零回归）。

    缺记录即回落 `config.py` 默认（沿用 `get_auto_trade_settings` 的稀疏语义）；越界值一律
    **夹紧到 `Field` 的域**并记一行 warning，绝不让一个 DB 里的坏值把真实资金会话打死。
    """
    updates: dict = {}
    for _wire, key, field, caster in _SETTING_BRIDGE:
        try:
            raw = await repo.get_app_setting(session, key, None)
        except Exception as exc:  # noqa: BLE001 —— 读不到就用默认，绝不阻断启动
            logger.info("ai-trade experience settings bridge skipped: %s", exc)
            return settings
        if raw is None:
            continue
        value = _cast_setting(field, raw, caster,
                              side_note=settings.ai_trade_directional_symmetry_enabled)
        if value is not None and value != getattr(settings, field, None):
            updates[field] = value
    if not updates:
        return settings
    try:
        return settings.model_copy(update=updates)
    except Exception as exc:  # noqa: BLE001 —— 派生失败即回落基线（绝不打死会话）
        logger.warning("ai-trade experience settings override rejected: %s", exc)
        return settings


def _cast_setting(field: str, raw, caster: type, *, side_note: bool = False):
    """把 DB 里的原始值转成字段类型并夹紧到域内；无法解析 ⇒ `None`（该项忽略）。

    `side_note`（directional-symmetry 轴 E-2）：方向对称性总闸开时 L0 页眉多一句退化声明，
    下限随之抬高。三个 `budget_floor` 调用点必须传**同一个**判据（★ E-7），少传一处，那一处
    就会用一个偏小的下限放行一个「整层渲染不出一个字」的配置。
    """
    try:
        value = bool(raw) if caster is bool else caster(raw)
    except (TypeError, ValueError):
        logger.warning("ai-trade experience setting %s ignored (unparsable: %r)", field, raw)
        return None
    if field.endswith("_max_chars"):
        # ⚠️ **本分支只认 l0 / l1**（按字段名猜层）。`reflection_max_chars`（L2）**不在**桥表里，
        # 故它今天够用；但下一轮把 L2 放进设置面板之前**必须先改这里**，否则它会静默拿 L1 的
        # 下限（164）去夹一个下限为 208 的值——一个改得动、却把整层关掉的假控件。
        floor = budget_floor("l0" if "l0" in field else "l1",   # ★ E-7：与校验器同一判据
                             side_note=side_note)
        if value < floor:
            logger.warning("ai-trade experience %s clamped %s → %s", field, value, floor)
            value = floor
    if caster in (int, float) and value < 0:
        return None
    return value


# ---------------- 物化 / 重建 ----------------
class MaterializeBusyError(RuntimeError):
    """★ E-6② · 排队等 `materialize_lock` 超时（放弃本次；脏标志留着，下次再来）。"""


@asynccontextmanager
async def _materialize_lock(timeout: float | None):
    """持物化串行锁，**等待有界**。

    ★ E-6（评审 P0-6）修订后的 `materialize_timeout_s` 语义就是这一条：它约束的**不是** CPU 段
    （`asyncio.timeout` 停不掉 `run_in_threadpool` 里已经开跑的线程），而是「排队等这把锁最多等
    多久」。裸 `async with` 会让一个持锁者异常消失（比如它所在的事件循环被关掉）之后，**后续
    每一次物化都永久挂起**——而挂起是最难诊断的一种失败：日志上什么都没有。

    **为什么是「轮询 `locked()` + 无取消的 `acquire()`」而不是 `wait_for(lock.acquire())`**：
    本仓库跑在 CPython **3.11**，而 `asyncio.Lock.acquire` 在「恰好被授予的同一刻被取消」时
    **不会**回滚 `_locked`（3.12 才修）。用 `wait_for` 就是把一个「有界等待」换成一个**概率性
    的永久锁泄漏**——正是本函数要消灭的那种故障，只是更难复现。轮询的代价是超时判据在争用下
    偏软（退出循环后仍可能被别人抢先，于是多等那一个持有者一轮），换来的是**它不可能泄漏锁**。
    在「软一点的死线」与「偶发的永久挂起」之间，这条选择是显然的。
    """
    global _LOCK_HOLDER

    current = asyncio.current_task()
    if _LOCK_HOLDER is not None and current is _LOCK_HOLDER:
        yield                       # ★ E-13：同一 task 已持锁 ⇒ 可重入的空操作
        return
    lock = repo.experience_materialize_lock()
    if timeout is not None:
        deadline = time.monotonic() + max(0.0, float(timeout))
        while lock.locked():
            if time.monotonic() >= deadline:
                raise MaterializeBusyError(
                    f"等物化锁超过 {timeout}s，放弃本次（脏标志留着，下次再来）")
            await asyncio.sleep(_LOCK_POLL_S)
    await lock.acquire()
    _LOCK_HOLDER = current
    try:
        yield
    finally:
        _LOCK_HOLDER = None
        lock.release()


async def rebuild(session: AsyncSession, settings, *, lock_timeout: float | None = None) -> dict:
    """从事件全量重建统计子图（`POST /api/experience/rebuild` 与后台物化共用同一条链）。

    幂等（A-15）：同一批事件重建两次 ⇒ 节点 / 边集合逐值相等，**且用户的置顶与静音一字不差地
    活着**（后半句是评审 P0-2 之后加强的断言）。

    **★ E-14（评审 P1-8）· 「单事务完成」是错的，本函数是四次独立提交**
    （`upsert_lesson_nodes` / `unmute_reappeared` / `mark_stale_lessons` / `replace_edges` 各自
    `await session.commit()`）。取消或异常落在两次提交之间会留下「新节点 + 旧边」的**撕裂图**，
    而 `match(l) = Σ A(c)·w_applies(l, c)` 对没有边的新节点恒为 0 ⇒ **L0 静默变空**。
    v1 **不强行改成单事务**（那要动数据层四个函数的提交语义，回归面远大于收益），改为**让撕裂
    可见**：调用方据本函数抛出与否在 `materialize_health.torn` 落一个状态位，§7.1 以 `--warn`
    明示「上次重建未走完全部步骤，图可能不完整」。**能修的修，修不动的说出来——绝不留一个不报错
    的半成品。**

    Returns:
        `{"nodes", "edges", "stale", "unmuted", "inserted", "updated", "elapsedMs", "events"}`。
    """
    started = time.time()
    async with _materialize_lock(lock_timeout):
        rows = await repo.load_experience_events(session, repo.ExperienceEventQuery())
        existing = {row["key"]: node_from_row(row) for row in
                    await repo.load_experience_nodes(session, kinds=("lesson",))}
        events = _event_views(rows)
        plan = await run_in_threadpool(materialize, events, _options(settings), existing)
        payload = [_node_payload(node) for node in plan.nodes]
        upserted = await repo.upsert_lesson_nodes(session, payload)
        # ★ E-9（评审 P1-3）：本轮重新出现 ⇒ 解除 `stale` / `flipped` 自动静音（用户静音不动）。
        unmuted = await repo.unmute_reappeared_experience_lessons(session, plan.seen_lesson_keys)
        stale = await repo.mark_stale_experience_lessons(session, plan.seen_lesson_keys)
        edges = await repo.replace_experience_edges(
            session, [{"src_key": e.src_key, "dst_key": e.dst_key, "rel": e.rel,
                       "weight": e.weight, "support": e.support} for e in plan.edges],
            # ★ E-2：非 `stat` 经验的主键来自**既有行**（它们不在本次 upsert 的 payload 里时
            # 也必须能被边解析到），故 key→id 映射是「既有 ∪ 本次 upsert」的并集。
            {**{key: node.id for key, node in existing.items() if node.id},
             **upserted["keys"]})
        await repo.set_app_setting(session, COST_CUTS_KEY, plan.cost_regime_cuts)
        # ★ W-B · 把本次算出的累加器与语境计数器写成**下一次增量的底座**并开一个新 epoch。
        # 少了这一步，判据 B-4 会把之后每一次物化都打回全量——增量永远起不来，而且全绿。
        await incremental.seed_from_full(session, settings, events, outcome={
            "cuts": plan.cost_regime_cuts,
            "lastId": max((int(row.get("id") or 0) for row in rows), default=0),
            "nodeCount": len(plan.seen_lesson_keys)})
    return {"nodes": len(payload), "edges": edges, "stale": stale, "unmuted": unmuted,
            "inserted": upserted["inserted"], "updated": upserted["updated"],
            "events": len(events),
            "elapsedMs": round((time.time() - started) * 1000, 1)}


# ---------------- 后台物化的调度体（★ D-3 / E-5 / E-6 / E-14）----------------
async def materialize_bounded(settings, *, force: bool = False) -> dict:
    """一次**有界**的后台物化：去抖 + 进程级单槽 + 规模闸 + 健康写回。**绝不抛**。

    这是 F-1 / F-2 两条 P0 的正解落点：物化既不在 `_dispatch` 热路径上，也不在 `stop()` /
    `pause()` 的 await 链上，而是 manager 侧的一个 tracked 后台任务（§4.9）。

    四道闸，缺一即回到评审记录里的某一条 P0：
      ① **进程级单槽**（★ E-6 / P0-6）：`_MATERIALIZE_BUSY` 的生命周期由 `finally` 覆盖「线程里
         的真实工作」全程。`asyncio.timeout` 停不掉 `run_in_threadpool` 里已经开跑的 CPU 工作，
         照 v3 实现会在大库上变成「每 60 秒叠一个永不完成的全库重建」，吃满全进程共用的 anyio
         线程池并饿死回测 / 挖掘。忙时只置脏标志，**绝不叠第二个任务**。
      ② **去抖**：距上次成功物化不足 `materialize_min_interval_s` ⇒ 只置脏标志（`force=True`
         的终局物化豁免——那是最后一次机会）。
      ③ **规模闸**（★ E-6）：事件数超过 `materialize_max_events` ⇒ **不自动物化**，只落
         `overflow: true` 并由 UI 明示。宁可停下来说清楚，也不要一个永远跑不完的后台任务。
      ④ **撕裂可见**（★ E-14）：`rebuild` 是四次提交，中途失败即落 `torn: true`。

    Args:
        settings: 基线 `Settings`（本函数内部经 `resolve_experience_settings` 再派生一次——用户
            可能在两次物化之间改了阈值，§4.10 的四个必经调用点之一）。
        force: 终局物化（`_stop_session` / `pause_group` / 冷启动补物化）⇒ 豁免去抖。

    Returns:
        `{"ran": bool, "reason": str, ...rebuild 回执}`；`ran=False` 时 `reason` 说明为什么没跑。
    """
    global _MATERIALIZE_BUSY

    from ...db.session import get_sessionmaker

    if _MATERIALIZE_BUSY:
        await _mark_dirty(True)
        return {"ran": False, "reason": "busy"}
    _MATERIALIZE_BUSY = True
    try:
        async with get_sessionmaker()() as session:
            derived = await resolve_experience_settings(session, settings)
            if not force and not await _debounce_ok(session, derived):
                await _mark_dirty(True, session=session)
                return {"ran": False, "reason": "debounced"}
            # ★ E-3（评审 R-3）· 规模闸此前在 `_run_materialize` **之前**短路，而增量住在它
            # **里面** ⇒ 库越过 20 万条之后增量根本没有机会跑，`overflow` 照旧置真、UI 照旧打
            # 「规模墙」。判据因此从「事件总数」改成「**本次真的要跑全量**」：增量的成本与库
            # 大小近似无关（只读 `id > watermark` 且被 `incremental_batch` 封顶），不受本闸约束。
            planned = await incremental.plan(session, derived)
            if planned["reason"] == incremental.REASON_UP_TO_DATE:
                await _mark_dirty(False, session=session)
                return {"ran": False, "reason": "up_to_date"}
            events = await repo.count_experience_events(session)
            cap = int(derived.ai_trade_experience_materialize_max_events)
            if planned["mode"] == "full" and events > cap:
                await _write_health(session, {"lastTs": int(time.time()), "lastMs": 0,
                                              "events": events, "slow": False, "torn": False,
                                              "overflow": True, "mode": "full",
                                              "fullReason": planned["reason"],
                                              "pendingEvents": 0})
                logger.warning("ai-trade experience full rebuild skipped: %d events > cap %d",
                               events, cap)
                return {"ran": False, "reason": "overflow", "events": events}
            return await _run_materialize(session, derived, planned=planned)
    except Exception as exc:  # noqa: BLE001 —— 后台任务绝不把异常带出去
        logger.warning("ai-trade experience materialize failed: %s", exc)
        return {"ran": False, "reason": "error", "error": str(exc)}
    finally:
        _MATERIALIZE_BUSY = False


async def _run_materialize(session: AsyncSession, settings, *,
                           planned: dict | None = None) -> dict:
    """跑一次物化（增量或全量）并落健康读数；撕裂 ⇒ `torn: true`，**不抛**。

    ★ E-13 · 锁在**这一层**持有，`utility.refresh` 因此与 `mark_stale_lessons` /
    `unmute_reappeared` 处在同一个临界区——它们写的是同一批 `muted` / `mute_reason` 列，
    跑在锁外会丢更新，且是那种「数字不对但不报错」的丢法。**不新增第二把锁、不新增后台任务。**
    """
    started = time.time()
    try:
        async with _materialize_lock(
                float(settings.ai_trade_experience_materialize_timeout_s)):
            result = await incremental.run_locked(session, settings, planned=planned)
    except MaterializeBusyError as exc:
        logger.info("ai-trade experience materialize gave up waiting for the lock: %s", exc)
        return {"ran": False, "reason": "lock_timeout"}
    except Exception as exc:  # noqa: BLE001 —— ★ E-14：能修的修，修不动的说出来
        await _note_torn(session, started)
        logger.warning("ai-trade experience materialize torn: %s", exc)
        return {"ran": False, "reason": "torn", "error": str(exc)}
    await _note_materialized(session, settings, result)
    return {"ran": True, "reason": "ok", **result}


async def rebuild_manual(session: AsyncSession, settings, *, lock_timeout: float,
                         scope: str = "stat") -> dict:
    """用户明确要求的重建（`POST /api/experience/rebuild`）——**无去抖、无规模闸**。

    与后台物化**共用同一处健康写回**（`_note_materialized` / `_note_torn`）。不共用的后果不是
    抽象的：`materialize_health` 是 §7.1 概览条那三句注记的唯一取数来源，其中两句写的正是
    「……建议手动重建」。手动重建不落健康读数 ⇒ 「上次重建」永远停在上一次**自动**物化的时刻，
    而 `torn` / `overflow` 这两条警告在用户照做之后**永远不会消失**——产品告诉用户的做法，
    产品自己不兑现，这正是本特性反复在守的那条线。

    与 `_run_materialize` 的唯一区别是**失败一律上抛**：这是一个用户正在等着看结果的前台动作，
    把撕裂咽成 `{"ran": false}` 会让按钮报成功而图是残的。

    Args:
        session: DB 会话。
        settings: 已经过 `resolve_experience_settings` 派生的 `Settings`。
        lock_timeout: 等物化锁的死线（调用方给前台动作更宽裕的值）。

    Returns:
        `rebuild` 的回执 + `torn: False`。

    Raises:
        MaterializeBusyError: 等锁超时（调用方转 409）。
        Exception: 撕裂——健康读数已落 `torn: true` 后原样上抛。
    """
    started = time.time()
    try:
        async with _materialize_lock(lock_timeout):
            # `scope="full"` = 用户明确要求**强制全量**（并 epoch+1）；`"stat"` = 由五条判据自选。
            planned = None if scope == "full" else await incremental.plan(session, settings)
            result = await incremental.run_locked(session, settings, planned=planned)
    except MaterializeBusyError:
        raise
    except Exception:
        await _note_torn(session, started)
        raise
    await _note_materialized(session, settings, result)
    return {**result, "torn": False}


async def _mark_dirty(dirty: bool, *, session: AsyncSession | None = None) -> None:
    """★ E-5（评审 P0-5）· 脏标志**落库**，否则关软件即丢。

    桌面端正常退出走的是 `pause_group("app_quit")`（`ai_trade_shutdown_action` 默认 `"hold"`），
    manager 内存里的脏标志随进程消亡 ⇒ 最后一个去抖窗口内平掉的仓位**永远不会进图**，
    而「经验是累积的」这条需求恰恰在最常见的使用节奏（跑一会儿 → 关软件 → 明天再开）下不兑现。
    由 `main` 的 lifespan 启动段消费一次（用例 A-45）。
    """
    from ...db.session import get_sessionmaker

    try:
        if session is not None:
            await repo.set_app_setting(session, DIRTY_KEY, bool(dirty))
            return
        async with get_sessionmaker()() as own:
            await repo.set_app_setting(own, DIRTY_KEY, bool(dirty))
    except Exception as exc:  # noqa: BLE001 —— best-effort，绝不阻断
        logger.info("ai-trade experience dirty flag not persisted: %s", exc)


async def consume_dirty_flag(settings) -> dict:
    """★ E-5 · 冷启动补物化：`app_settings["experience.dirty"]` 为真 ⇒ 补跑一次终局物化。

    由 `main` 的 lifespan 启动段调用一次；标志为假 ⇒ 什么都不做（`ran=False`）。**绝不抛**。
    """
    from ...db.session import get_sessionmaker

    try:
        async with get_sessionmaker()() as session:
            dirty = bool(await repo.get_app_setting(session, DIRTY_KEY, False))
    except Exception as exc:  # noqa: BLE001 —— 启动期读不到就当不脏
        logger.info("ai-trade experience dirty flag not read: %s", exc)
        return {"ran": False, "reason": "unreadable"}
    if not dirty:
        return {"ran": False, "reason": "clean"}
    logger.info("ai-trade experience: consuming persisted dirty flag (cold start catch-up)")
    return await materialize_bounded(settings, force=True)


def _node_payload(node: GraphNode) -> dict:
    """`GraphNode` → 数据层的行字典（列名口径的唯一转换点）。"""
    return {"key": node.key, "kind": node.kind, "axis": node.axis, "value": node.value,
            "label": node.label, "detail": node.detail, "source": node.source,
            "scope": node.scope, "tier": node.tier, "pinned": node.pinned,
            "muted": node.muted, "mute_reason": node.mute_reason, "support": node.support,
            "weight": node.weight, "stats": node.stats, "signature": node.signature,
            "first_ts": node.first_ts, "last_seen_ts": node.last_seen_ts,
            "prev_sign": node.prev_sign, "sign_streak": node.sign_streak}


# ---------------- 只读视图 ----------------
async def _harvest_wire(session: AsyncSession, gap_total: int) -> dict:
    """采集完整性块：`{gapTotal, adoptedTotal, reasons}`（★ W-A / W-F）。

    `reasons` 的键集**恒为闭合的六个**（零值的键照常下发）：列出来才知道「这条路今天没走过」
    与「这条路不存在」的区别；少一个键则「为什么采不到」在 UI 上变成一句「其他」。
    """
    from .runtime import HARVEST_REASONS, HARVEST_TOTALS_KEY

    totals = await repo.get_app_setting(session, HARVEST_TOTALS_KEY, {})
    totals = totals if isinstance(totals, dict) else {}
    reasons = totals.get("reasons")
    reasons = reasons if isinstance(reasons, dict) else {}
    return {"gapTotal": int(gap_total), "adoptedTotal": int(totals.get("adopted") or 0),
            "reasons": {key: int(reasons.get(key) or 0) for key in HARVEST_REASONS}}


async def overview(session: AsyncSession, settings) -> dict:
    """概览条（`GET /api/experience/overview`）。

    ★ E-8（评审 P1-2）· `materializeHealth` 与 `harvestGapTotal` 此前**都没有取数来源**：前者是
    后台物化写进 `app_settings` 的、`service` 从没读回来，后者根本没有持久化写点（`harvest_gap`
    只活在会话内存里）。于是概览条上那两个数字**恒为 0**——而它们恰恰是用来说「这张表偏乐观」
    与「自动重建已经跟不上了」的。这是 I-8「观测量必须有渲染落点」的**镜像形态**：有渲染落点、
    没有取数来源。两者一并补齐（用例 A-48）。

    ★ 本轮新增三块：`harvest`（采集完整性）、`utility`（效用闭环）、`materializeHealth` 的
    `mode` / `fullReason` / `pendingEvents`。每一块在 §7.1 各有明确渲染落点（I-8）。
    """
    from . import utility

    wire = await repo.experience_overview(session)
    wire["rejectedNarrative"] = int(
        (await repo.get_app_setting(session, REJECTED_KEY, 0)) or 0)
    wire["harvestGapTotal"] = int(
        (await repo.get_app_setting(session, HARVEST_GAP_KEY, 0)) or 0)
    wire["harvest"] = await _harvest_wire(session, wire["harvestGapTotal"])
    utility_wire = await utility.overview_wire(session, settings)
    if utility_wire is not None:      # 总闸关 ⇒ **整键缺席**，绝不下发一个恒为 0 的假读数
        wire["utility"] = utility_wire
    wire["materializeHealth"] = incremental.health_wire(await _read_health(session))
    wire["costRegimeCuts"] = await repo.get_app_setting(session, COST_CUTS_KEY, {}) or {}
    return wire


async def graph_view(session: AsyncSession, filters: dict) -> dict:
    """图谱 / 列表视图共用的取数（**同数据源同筛选**，A-22 的支点）。

    截断如实下发（`truncated`）：静默截断读起来像「全都在这儿了」，那是本仓库反复付过学费的一条。
    """
    limit = max(1, min(int(filters.get("limit") or 120), 2000))
    rows = await repo.load_experience_nodes(session)
    lessons = [row for row in rows if row["kind"] == "lesson" and _match(row, filters)]
    total = len(lessons)
    kept = lessons[:limit]
    context_pairs = {pair for row in kept for pair in (row.get("signature") or {}).items()}
    contexts = [row for row in rows if row["kind"] == "context"
                and (row["axis"], row["value"]) in context_pairs]
    nodes = kept + contexts
    edges = await repo.load_experience_edges(session, [row["id"] for row in nodes])
    return {"nodes": [_node_wire(row) for row in nodes],
            "edges": [_edge_wire(row) for row in edges],
            "total": total, "truncated": total > len(kept)}


def _edge_wire(row: dict) -> dict:
    """边行 → wire（camelCase）。

    评审 P2-3：`load_edges` 的行是 snake_case（`src_id` / `dst_id`），而 `nodes` 走 `_node_wire`
    是 camelCase——同一个回执两套命名会逼着 `lib/experienceTypes.ts` 照抄一份不一致。本轮统一。
    """
    return {"id": row.get("id"), "srcId": row.get("src_id"), "dstId": row.get("dst_id"),
            "rel": row.get("rel"), "weight": row.get("weight"),
            "support": row.get("support"), "updatedTs": row.get("updated_ts")}


def _match(row: dict, filters: dict) -> bool:
    """列表 / 图谱的筛选判据（单点，两个视图共用 ⇒ 条数必然一致）。"""
    signature = row.get("signature") or {}
    if filters.get("instId") and signature.get(tax.AXIS_SYMBOL) not in (None, filters["instId"]):
        return False
    if filters.get("bar") and signature.get(tax.AXIS_BAR) not in (None, filters["bar"]):
        return False
    if filters.get("axis") and filters["axis"] not in signature:
        return False
    if filters.get("source") and row.get("source") != filters["source"]:
        return False
    tier = filters.get("tier")
    # 「已沉降」是一个**独立档位**（静音优先于 tier 本身），但它绝不能在这里就 `return`：
    # 工具栏允许「档位=已沉降」与搜索词同时存在，早退会让那个搜索框静默失效——用户敲了字、
    # 结果一条没少，而界面上没有任何东西告诉他搜索没生效。
    if tier == "muted":
        if not row.get("muted"):
            return False
    elif tier and (row.get("muted") or row.get("tier") != tier):
        return False
    query = (filters.get("q") or "").strip().lower()
    if query and query not in f"{row.get('label', '')}{row.get('detail', '')}".lower():
        return False
    return True


def _node_wire(row: dict) -> dict:
    """节点行 → wire（camelCase；前端类型逐字镜像本形状）。"""
    return {"id": row["id"], "kind": row["kind"], "key": row["key"], "axis": row.get("axis"),
            "value": row.get("value"), "label": row.get("label"), "detail": row.get("detail"),
            "source": row.get("source"), "scope": row.get("scope"), "tier": row.get("tier"),
            "pinned": bool(row.get("pinned")), "muted": bool(row.get("muted")),
            "muteReason": row.get("mute_reason"), "support": row.get("support"),
            "weight": row.get("weight"), "stats": row.get("stats"),
            "signature": row.get("signature"), "firstTs": row.get("first_ts"),
            "lastSeenTs": row.get("last_seen_ts"), "signStreak": row.get("sign_streak"),
            "createdTs": row.get("created_ts"), "updatedTs": row.get("updated_ts")}


async def lessons_view(session: AsyncSession, settings, filters: dict) -> dict:
    """列表视图（`GET /api/experience/lessons`）——**与图谱同数据源、同筛选判据**（A-22 的支点）。

    刻意**不**走一条独立的 SQL 分页：两条路径必然漂移，而这里漂移的是「同一筛选下图谱说 12 条、
    列表说 9 条」——用户完全无从判断哪个是真的。节点总量受 `graph_node_cap`（默认 4000）约束，
    在内存里筛一遍的代价可忽略，换来的是**两个视图的条数在结构上不可能不一致**。
    """
    from . import utility

    rows = await repo.load_experience_nodes(session)
    lessons = [row for row in rows if row["kind"] == "lesson" and _match(row, filters)]
    offset = max(0, int(filters.get("offset") or 0))
    limit = max(1, min(int(filters.get("limit") or 50), 500))
    page = lessons[offset:offset + limit]
    # ★ W-F · 「效用」列的取数与详情抽屉**同一个换算点**；没有注入样本的经验整键缺席，
    # 列表据此渲染 `—` 而不是一个「注入 0 次、之后 0bps」的假读数。
    scores = await utility.by_keys(session, settings, [row["key"] for row in page])
    items = []
    for row in page:
        item = _node_wire(row)
        hit = scores.get(row["key"])
        if hit is not None:
            item["utility"] = hit
        items.append(item)
    return {"items": items, "total": len(lessons)}


async def node_detail(session: AsyncSession, settings, node_id: int, *,
                      evidence_limit: int = 20) -> dict | None:
    """详情抽屉（统计 / 语境 / 版本链 / 冲突 / 溯源证据）。"""
    row = await repo.get_experience_node(session, node_id)
    if row is None:
        return None
    all_nodes = await repo.load_experience_nodes(session)
    by_id = {n["id"]: n for n in all_nodes}
    edges = await repo.load_experience_edges(session)
    related = {"contradicts": [], "refines": [], "contexts": []}
    for edge in edges:
        if edge["src_id"] != row["id"]:
            continue
        target = by_id.get(edge["dst_id"])
        if target is None:
            continue
        if edge["rel"] == "applies_in":
            related["contexts"].append({**_node_wire(target), "edgeWeight": edge["weight"]})
        elif edge["rel"] in related:
            related[edge["rel"]].append(_node_wire(target))
    evidence = await repo.load_experience_evidence(
        session, row.get("signature") or {}, limit=evidence_limit)
    from . import utility

    detail = {"node": _node_wire(row), "stats": row.get("stats") or {},
              "contexts": related["contexts"], "versions": related["refines"],
              "contradicts": related["contradicts"], "evidence": evidence}
    hit = await utility.detail(session, settings, row["key"])
    if hit is not None:               # ★ W-F：「注入后表现」区；无样本 ⇒ 整键缺席
        detail["utility"] = hit
    return detail


# ---------------- 检索预演 ----------------
async def preview(session: AsyncSession, settings, axes: dict, *,
                  live_sessions: list[dict] | None = None) -> dict:
    """检索预演：**逐字**渲染下一轮 Agent 会看到的 L0 / L1（§7.1 最有说服力的一屏）。

    **但它必须说实话**（评审 P1-10）：预演读的是**当前 DB 里的图**，而运行中的会话用的是
    **启动那一刻载入内存的快照**（I-3 的零 IO 约束使然），两者会因本会话增量物化与其它成员
    会话的写入而分叉。故回执带 `graphAsOfTs` 与 `liveSessions[]`，由 UI 在分叉时明说
    「该会话使用的是 X 时刻的快照」——少了这条，这一屏在最需要它的时刻说的是假话，
    而一屏假话比没有这一屏更坏。
    """
    nodes = await repo.load_experience_nodes(
        session, limit=int(settings.ai_trade_experience_graph_node_cap))
    edges = await repo.load_experience_edges(session, [n["id"] for n in nodes])
    as_of = int(time.time())
    graph = build_graph(nodes, edges, as_of_ts=as_of)
    seed = ContextSeed(axes={k: v for k, v in axes.items() if v}, now_wall=time.time())
    result = retrieve(graph, seed, RetrievalConfig.from_settings(settings))
    totals = await repo.experience_overview(session)
    budgets = {"l0": int(settings.ai_trade_experience_l0_max_chars),
               "l1": int(settings.ai_trade_experience_l1_max_chars)}
    wire = build_wire(result, tier=experience_tier(result, None, None),
                      totals=_totals_wire(totals),      # 与 L0 页眉同一换算点（I-9）
                      max_chars=budgets, graph_as_of_ts=as_of)
    l0_text = format_l0(wire["l0"], wire["totals"], budgets["l0"])
    l1_text = format_l1(wire["l1"], budgets["l1"])
    return {**wire, "l0Text": l0_text, "l1Text": l1_text,
            "chars": {"l0": len(l0_text), "l1": len(l1_text),
                      "total": len(l0_text) + len(l1_text), "budget": budgets},
            "seedActivation": [{"key": k, "a": round(v, 4)}
                               for k, v in sorted(result.activation.items(),
                                                  key=lambda kv: (-kv[1], kv[0]))][:24],
            "liveSessions": [_live_session_wire(row, as_of) for row in (live_sessions or [])]}


def _live_session_wire(row: dict, as_of: int) -> dict:
    """运行中会话的快照对照行（`diverged` 由**服务端**判定，前端不重算）。

    少了这条对照，「检索预演」这一屏在最需要它的时刻（会话正在跑）说的是假话：预演读的是当前
    DB 里的图，而运行中的会话用的是**启动那一刻**载入内存的快照。**一屏假话比没有这一屏更坏。**
    """
    session_as_of = int(row.get("graphAsOfTs") or 0)
    return {"instId": row.get("instId"), "graphAsOfTs": session_as_of,
            "diverged": bool(session_as_of and session_as_of < as_of)}


# ---------------- 导出（NDJSON 流） ----------------
async def export_stream(session: AsyncSession, query, *, page: int = 500):
    """导出的 NDJSON 行生成器（首行是 `counts` 信封，其后每行一个 node / edge / event）。

    **必须流式**：跑半年的库是几万条事件，一次性拼进内存再序列化会在桌面端把整个进程顶起来
    （用例 A-34 断言 RSS 峰值）。首行的 `counts` 让接收方能校验完整性——一条被网络截断的
    NDJSON 与一条完整的在语法上没有任何区别，这是导出格式必须自带体检项的理由。
    """
    counts = {"nodes": len(await repo.load_experience_nodes(session)),
              "events": await repo.count_experience_events(session)}
    yield json.dumps({"version": 1, "exportedTs": int(time.time()), "counts": counts},
                     ensure_ascii=False) + "\n"
    async for row in repo.stream_experience_export_rows(session, query, page=page):
        yield json.dumps(row, ensure_ascii=False) + "\n"

# ---------------- 手工新增 / 导入（下沉到 `importing.py`，见该模块 docstring 的拆分理由）----------------
# 再导出而不是让调用方改 import：`distill.py` 与 `api/experience.py` 都按 `service.sanitize_lesson`
# 写着，而「为了拆文件去改三处调用点」正是这类重构最容易漏掉一处的地方。
from .importing import (  # noqa: E402
    ImportTooLargeError, import_graph, manual_key, sanitize_lesson)
# 同款再导出：效用面板与「清理沉降经验」的编排住在 `utility.py`（service.py 的 800 行阈值是
# 硬判据，`importing.py` 是同一条纪律的成文先例），路由层仍按 `service.X` 写。
from .utility import list_view as utility_view  # noqa: E402
from .utility import prune_stale  # noqa: E402

__all__ = [
    "ImportTooLargeError",
    "MaterializeBusyError",
    "SessionGraph",
    "consume_dirty_flag",
    "export_stream",
    "graph_view",
    "health_view",
    "import_graph",
    "lessons_view",
    "load_session_graph",
    "manual_key",
    "materialize_bounded",
    "node_detail",
    "overview",
    "preview",
    "prune_stale",
    "rebuild",
    "rebuild_manual",
    "resolve_experience_settings",
    "sanitize_lesson",
    "setting_bridge",
    "utility_view",
]
