"""经验层的后台任务调度（§4.5 的四个触发点 + §4.9 的关停链）。

**为什么它是一个独立模块**：`ai_auto/manager.py` 实测 1774 行，早已超过 CLAUDE.md 的 800 行
阈值，而 §8.2 给它的净增预算是 46 行——「超了就再下沉一层而不是再放宽阈值」是 `prior_service.py`
的成文先例。manager 因此只保留**五个薄转发**（调度 / 变脏 / 终局 / 排空 / 死线），全部判据与
生命周期都住在这里。

三条硬纪律，逐条都对应评审记录里的一条 P0：

1. **绝不 await**（评审 F-1）。`stop_group` 串行遍历成员，而客户端 `/ai-trade/{groupId}/stop`
   的超时档是 `FAST_MS = 20_000` 且被判为写路径——一次 45 秒的 LLM 调用（或一次 5 秒的物化
   ×3 成员）会让**真实资金的停止按钮**弹出「本次操作结果未知」。
2. **关停先 cancel 再有界收敛**（★ E-4 / 评审 P0-4）。`main._run_shutdown` 的单步死线是
   `shutdown_step_timeout_s`（默认 20s），内层等得比它久 ⇒ 外层先 cancel ⇒ **内层的清理永远
   不执行**，后台任务活着穿过 `dispose_engine`。
3. **未装配的会话不起任务**（实施发现 I-6）。为一个没有经验可终局化的会话起一个开 DB 会话、
   持物化锁的后台任务只有害处：它在事件循环关闭时若还持着锁，后续每一次物化都会永久挂起。
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass

from ...db.session import get_sessionmaker
from . import service

logger = logging.getLogger(__name__)

# tracked 后台任务集。**模块级**而不是实例级：`get_ai_trade_manager()` 是进程级单例，模块级让
# 「任务活得比 manager 实例久」这件事在类型上就不可能发生。
_TASKS: set[asyncio.Task] = set()
# 按 `group_id` 去重的在飞物化任务（§4.11：N 个成员同时变脏只产生一个任务）。
_DIRTY: dict[str, asyncio.Task] = {}


@dataclass(frozen=True)
class GroupContext:
    """一次后台物化需要的全部分组事实（**同步**取自 manager 的内存，绝不查 DB）。

    做成聚合形参而不是让调度层反向依赖 manager：那会让 `manager ⇄ experience` 变成一个双向
    依赖环，而本包已经被 `session` / `manager` / `api` 三处 import。
    """

    settings: object
    sessions: list                      # 分组内**仍在运行**的会话（`adopt_snapshot` 的落点）
    rule: object | None                 # 任一成员的经验规则（分组内同一份）


def tasks() -> set[asyncio.Task]:
    """在飞的经验层后台任务（只读；供 G-5 时延门禁断言「它们仍在飞」）。"""
    return _TASKS


def schedule(coro) -> None:
    """把一个经验层协程登记成 tracked 后台任务（`done_callback` 摘掉自己，防泄漏）。"""
    task = asyncio.ensure_future(coro)
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)


def note_dirty(group_id: str, resolve: Callable[[], "GroupContext | None"]) -> None:
    """会话侧「有新事件落盘」的**同步**回调入口（`_dispatch` 的 close 侧 hook 直接调它）。

    去抖与进程级单槽都在 `service.materialize_bounded` 里判（判据在纯核心，本层只调度）；
    这里只做任务级去重——同一分组同一时刻至多一个物化任务在飞。

    Args:
        group_id: 变脏的分组。
        resolve: 零参回调，返回 `GroupContext | None`（分组已不在 ⇒ None ⇒ 任务自行退出）。
    """
    existing = _DIRTY.get(group_id)
    if existing is not None and not existing.done():
        return
    task = asyncio.ensure_future(_materialize(group_id, resolve))
    _DIRTY[group_id] = task
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)
    task.add_done_callback(_forget_dirty(group_id))


def _forget_dirty(group_id: str) -> Callable[[asyncio.Task], None]:
    """`_DIRTY` 的摘除回调。

    `group_id` 每次启动分组都是新的，只进不出的字典在一个长跑进程里单调增长——本仓库对每一个
    进程内缓冲都有成文的有界纪律（`runtime._OPEN_SLOT_CAP` / `AttributionLedger._CAPACITY`），
    调度层不能是例外。**比对身份再删**：慢任务收尾时该键可能已经属于下一个任务了。
    """
    def forget(done: asyncio.Task) -> None:
        if _DIRTY.get(group_id) is done:
            del _DIRTY[group_id]

    return forget


async def _materialize(group_id: str, resolve: Callable[[], "GroupContext | None"]) -> None:
    """跑一次有界物化，并把新快照交给仍在跑的成员。**绝不抛**。"""
    ctx = resolve()
    if ctx is None:
        return
    result = await service.materialize_bounded(ctx.settings)
    if result.get("ran"):
        await _broadcast(resolve() or ctx)
    logger.debug("ai-trade experience materialize for %s: %s", group_id, result.get("reason"))


async def _broadcast(ctx: GroupContext) -> None:
    """重新载入一份 `SessionGraph` 并广播给分组内仍在运行的成员。**绝不抛**。

    这就是「经验是累积的，随着交易的进行不断更新」在**同一会话内**的兑现方式（A-38）：
    没有它，用户看到的是一个刚刚亏过同样的钱却毫无反应的 Agent。
    """
    if ctx.rule is None or not ctx.sessions:
        return
    try:
        async with get_sessionmaker()() as db:
            fresh = await service.load_session_graph(db, settings=ctx.settings, rule=ctx.rule)
    except Exception as exc:  # noqa: BLE001 —— 后台任务绝不把异常带出去
        logger.warning("ai-trade experience snapshot reload failed: %s", exc)
        return
    if fresh is None:
        return
    for session in ctx.sessions:
        session.experience.adopt_snapshot(fresh)


async def finalize(session, *, distill: bool = True) -> None:
    """会话终结（或暂停）后的两步串行后台任务。任一步失败只 warning。

    ① 终局物化（**豁免去抖**——这是最后一次机会）；
    ② 有界 LLM 叙事蒸馏（`distill=False` 时跳过：**暂停不是终结**，没有「会话回顾」可写，
       也不该在关软件时起一次 45 秒的 LLM 调用）。

    未装配的会话直接返回（实施发现 I-6）：**这一行不是优化，是纪律**——不加它，每一次
    `stop` / `pause`（含全部既有用例里的那些）都会凭空起一个开 DB 会话、持物化锁的后台任务。
    """
    if session.experience.rule is None:
        return
    settings = session.executor.settings
    try:
        await service.materialize_bounded(settings, force=True)
    except Exception as exc:  # noqa: BLE001 —— 见 docstring
        logger.warning("ai-trade experience finalize materialize failed: %s", exc)
    if not distill or not getattr(settings, "ai_trade_experience_distill_enabled", False):
        return
    inputs = session.experience.distill_inputs()
    if not inputs:
        return
    from . import distill as distill_mod

    try:
        await distill_mod.run_bounded(session.decider.llm, inputs, settings)
    except Exception as exc:  # noqa: BLE001 —— 蒸馏是加法，失败只放弃
        logger.warning("ai-trade experience distill failed: %s", exc)


async def drain(timeout: float) -> None:
    """★ E-4（评审 P0-4）· 关停时**先 cancel、再有界收敛**（与 `main._cancel_background` 同姿态）。

    v3 让 drain 内部等 `distill_timeout_s + 5 = 50` 秒，而 `_run_shutdown` 的单步死线是 20 秒
    ⇒ **drain 永远走不到自己那句「cancel 剩余任务」**，外层的 cancel 把它打断在 `asyncio.wait`
    上，那些后台任务于是活着穿过 `("db_engine", dispose_engine)` 那一步。

    姿态也不对：**退出软件时没有任何理由再等一次 45 秒的 LLM 调用**。确定性事件在这之前
    **已经落盘**，蒸馏是加法，图可以随时从事件重建。死线由启动期校验器钉死
    `drain_timeout_s < shutdown_step_timeout_s`（用例 A-47 正反两向各一条）。
    """
    pending = {task for task in _TASKS if not task.done()}
    if not pending:
        return
    for task in pending:
        task.cancel()                       # ① 先取消
    await asyncio.wait(pending, timeout=timeout)   # ② 有界收敛；收不干净就抛下它继续


async def catch_up(settings) -> None:
    """★ E-5（评审 P0-5）· 冷启动补物化：消费上一次进程留下的 `experience.dirty` 脏标志。

    桌面端正常退出走的是 `pause_group("app_quit")`（`ai_trade_shutdown_action` 默认 `"hold"`），
    去抖窗口内平掉的仓位如果只把脏标志放在内存里，**它会随进程消亡**——那一笔往返永远不会
    进图，而「经验是累积的」这条需求在最常见的使用节奏（跑一会儿 → 关软件 → 明天再开）下
    不兑现，且不报错。

    跑在后台任务里而不是 lifespan 的同步段：全库重建在大库上可能是秒级的，绝不能让它把桌面端
    的启动挡在后面。**绝不抛**。
    """
    try:
        result = await service.consume_dirty_flag(settings)
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 —— 启动期加法步骤，绝不阻断
        logger.warning("ai-trade experience catch-up skipped: %s", exc)
        return
    if result.get("ran"):
        logger.info("ai-trade experience catch-up materialized (%s events)",
                    result.get("events"))
