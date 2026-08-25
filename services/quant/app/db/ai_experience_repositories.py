"""交易经验上下文图的数据层（ai-trade-experience-context-graph §六）。

本模块**自包含**（沿用 `ai_auto_repositories.py` 的既有拆分范式）：不 import 同仓在制模块，
公开函数经 `repositories.py` re-export，调用点仍用 `repo.X`。

**物化的单点 upsert 函数是唯一写路径**（评审 P0-2 / 放行条件 2）。`upsert_lesson_nodes` 里那六个
「绝不覆盖」的字段是本模块存在的全部理由：编排层若另写一份 SQL，两份必漂移，而这里漂移的是
用户亲手按下的「静音」与 `iron` 档赖以晋升的符号历史。两者失效都**不会报错**——用户只会发现
自己静音过的经验又回来了，或者经验库里一条铁律都没有。

并发（多标的分组 N 个会话同时落事件 / 物化）：事件写走 `INSERT OR IGNORE`（幂等键
`(group_id, inst_id, seq, exit_ts)` 保护），物化由 `_MATERIALIZE_LOCK` 串行。两者都是 best-effort
——**经验沉淀失败绝不允许阻断真实资金的交易路径**。
"""
from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from .models import AiExperienceEdge, AiExperienceEvent, AiExperienceNode

# 物化串行锁（桌面端单进程；SQLite 并发写在半成品图上会留下指向不存在节点的边）。
_MATERIALIZE_LOCK = asyncio.Lock()

_LESSON_KIND = "lesson"
_CONTEXT_KIND = "context"

# ★ E-2（评审 R-2 / 不变式 J-7）· **参与统计的事件来源集合，只此一个常量。**
#
# `origin` 是这张表唯一能回答「这条经验的证据是本进程亲眼看到的，还是从交易所回读拼出来的」
# 的列，它是一个**口径闸**。四个读点——桶枚举（`load_experience_events`）/ 证据表
# （`load_evidence`）/ 概览计数（`overview`）/ 规模闸（`count_events`）——必须共用它。
#
# 只加写侧不改读侧的后果不是抽象的：`adopted` 事件会**写得进、读不出**，一条也进不了桶、
# 一条也进不了证据抽屉，而 `count_events` / `overview` 又不过滤 origin ⇒ L0 页眉那句
# 「来自 X 组…的 Y 笔已结算往返」把这些从未贡献过任何一条经验的往返算进去（`v1:I-9` 当场破），
# UI 上却同时显示「已补采 N 笔」——**比不做更坏**。
#
# `imported` 之类将来可能出现的来源默认**不进**统计：口径闸是本表的产品属性，不是过滤器的
# 默认值。新增一种 origin 时，写侧与读侧必须同一个 PR（G-16 是它的机械判据）。
STAT_ORIGINS: tuple[str, ...] = ("live", "adopted")

# upsert 时**绝不覆盖**的字段（§4.5 的落地清单，评审 P0-2）。
_PRESERVED = ("pinned", "muted", "mute_reason", "first_ts", "created_ts")


def materialize_lock() -> asyncio.Lock:
    """物化串行锁的唯一出口（编排层持它，绝不各自新建一把）。"""
    return _MATERIALIZE_LOCK


def _loads(raw: str | None) -> dict:
    """JSON 列 → dict；缺 / 空 / 坏 JSON 一律 `{}`（沿用既有 `run_out` 容错口径）。"""
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _dumps(value) -> str:
    return json.dumps(value or {}, ensure_ascii=False)


# ---------------- 事件（append-only 唯一真源） ----------------
async def append_experience_events(session: AsyncSession, rows: list[dict], *,
                                   commit: bool = True) -> int:
    """批量落事件（`INSERT OR IGNORE`，幂等键保护）；返回**实际新增**条数。

    续跑重放 / 并发重复写全部安全。返回值是「真的新增了几条」而不是「提交了几条」——两者不同时，
    差额就是被幂等键挡下的重复，那是一个应当可见的事实而不是静默成功。

    `commit=False` 是给「事件行与注入台账**同一次 commit**」那条路径用的（★ W-C §4.6.1）：
    两张表同源同刻写入，中途失败必须两边都没有新行（A-26 逐条断言）。此时返回的是**提交前的
    行数**（`count` 差额此刻还看不见），语义随之变成「本次尝试写入的条数」。
    """
    if not rows:
        return 0
    before = await count_events(session) if commit else 0
    payload = [{
        "group_id": row["group_id"], "run_id": int(row["run_id"] or 0),
        "inst_id": row["inst_id"], "bar": row["bar"], "seq": int(row["seq"] or 0),
        "origin": row.get("origin") or "live",
        "entry_ts": int(row.get("entry_ts") or 0), "exit_ts": int(row.get("exit_ts") or 0),
        "entry_wall_ts": int(row.get("entry_wall_ts") or 0),
        "exit_wall_ts": int(row.get("exit_wall_ts") or 0),
        "side": row.get("side") or "", "context_json": _dumps(row.get("context")),
        "outcome_json": _dumps(row.get("outcome")),
        "cost_basis": row.get("cost_basis") or "gross", "fee_known": bool(row.get("fee_known")),
        "net_bps": row.get("net_bps"), "gross_bps": row.get("gross_bps"),
        "cost_bps": row.get("cost_bps"), "edge_error_bps": row.get("edge_error_bps"),
        "hold_seconds": float(row.get("hold_seconds") or 0.0),
        "exit_source": row.get("exit_source") or "unknown",
        "created_ts": int(row.get("created_ts") or time.time()),
    } for row in rows]
    stmt = sqlite_insert(AiExperienceEvent).values(payload).on_conflict_do_nothing(
        index_elements=["group_id", "inst_id", "seq", "exit_ts"])
    await session.execute(stmt)
    if not commit:
        return len(payload)
    await session.commit()
    return max(0, await count_events(session) - before)


async def count_events(session: AsyncSession, *,
                       origins: tuple[str, ...] = STAT_ORIGINS,
                       up_to_id: int | None = None) -> int:
    """**参与统计的**事件行数（物化的规模闸、导出信封的 `counts`、B-5 对账共用；空表 ⇒ 0）。

    ★ E-2：口径与桶枚举必须一致，否则规模闸会用一个比真实样本池更大的数去判「越界」。
    ★ E-11：`after_id` 是 B-5 水位线对账（`COUNT(id <= watermark)`）的取数入口。

    Args:
        session: DB 会话。
        origins: 参与统计的来源集合（默认 `STAT_ORIGINS`）。
        up_to_id: 非 None ⇒ 只数 `id <= up_to_id` 的行（B-5 水位线对账用）。**刻意不叫
            `after_id`**：`EventQuery.after_id` 的语义是 `id > after_id`，两处同名反向会
            造出一条只在并发下现形的错误。

    Returns:
        行数。
    """
    stmt = select(func.count(AiExperienceEvent.id))
    if origins:
        stmt = stmt.where(AiExperienceEvent.origin.in_(list(origins)))
    if up_to_id is not None:
        stmt = stmt.where(AiExperienceEvent.id <= int(up_to_id))
    return int((await session.execute(stmt)).scalar() or 0)


@dataclass(frozen=True)
class EventQuery:
    """事件读取的过滤条件（聚合形参，满足「形参 ≤5」）。

    ★ E-2：`origin`（单值）换成 `origins`（集合），默认即 `STAT_ORIGINS`——口径闸而非死码。
    ★ E-11：`after_id` 是 W-B 增量物化的**事件水位线**，既有过滤字段里根本没有它。
    """

    inst_id: str | None = None
    bar: str | None = None
    since_ts: int | None = None
    until_ts: int | None = None
    origins: tuple[str, ...] = STAT_ORIGINS
    after_id: int | None = None
    limit: int | None = None
    offset: int = 0


async def load_experience_events(session: AsyncSession, query: EventQuery) -> list[dict]:
    """按条件读事件行（聚合与重建的取数点）。返回的是**行字典**，聚合层不认识 ORM 对象。"""
    stmt = select(AiExperienceEvent)
    if query.origins:
        stmt = stmt.where(AiExperienceEvent.origin.in_(list(query.origins)))
    if query.after_id is not None:
        stmt = stmt.where(AiExperienceEvent.id > int(query.after_id))
    if query.inst_id:
        stmt = stmt.where(AiExperienceEvent.inst_id == query.inst_id)
    if query.bar:
        stmt = stmt.where(AiExperienceEvent.bar == query.bar)
    if query.since_ts:
        stmt = stmt.where(AiExperienceEvent.exit_ts >= int(query.since_ts))
    if query.until_ts:
        stmt = stmt.where(AiExperienceEvent.exit_ts <= int(query.until_ts))
    stmt = stmt.order_by(AiExperienceEvent.id.asc())
    if query.limit:
        stmt = stmt.limit(int(query.limit)).offset(max(0, int(query.offset)))
    rows = (await session.execute(stmt)).scalars().all()
    return [_event_row(row) for row in rows]


def _event_row(row: AiExperienceEvent) -> dict:
    return {
        "id": row.id, "groupId": row.group_id, "runId": row.run_id, "instId": row.inst_id,
        "bar": row.bar, "seq": row.seq, "origin": row.origin,
        "entryTs": row.entry_ts, "exitTs": row.exit_ts,
        "entryWallTs": row.entry_wall_ts, "exitWallTs": row.exit_wall_ts,
        "side": row.side, "context": _loads(row.context_json), "outcome": _loads(row.outcome_json),
        "costBasis": row.cost_basis, "feeKnown": bool(row.fee_known),
        "netBps": row.net_bps, "grossBps": row.gross_bps, "costBps": row.cost_bps,
        "edgeErrorBps": row.edge_error_bps, "holdSeconds": row.hold_seconds,
        "exitSource": row.exit_source, "createdTs": row.created_ts,
    }


async def load_evidence(session: AsyncSession, signature: dict, *, limit: int = 20) -> list[dict]:
    """一条经验的溯源证据（最近 N 笔支撑往返）；按 `signature` 的标的 / 周期 / 退出来源过滤。

    刻意**只按提列的三列过滤**：`context_json` 是 Text，按它做子串匹配在 SQLite 上既慢又会误命中
    （`side=long` 会匹配到 `side=long_xxx` 这类不存在但迟早出现的取值）。三列之外的轴由调用方在
    内存里再过滤一次——证据表只取最近 20 条，代价可忽略。
    """
    stmt = select(AiExperienceEvent).where(
        AiExperienceEvent.origin.in_(list(STAT_ORIGINS)))      # ★ E-2：与桶枚举同一口径
    if signature.get("symbol"):
        stmt = stmt.where(AiExperienceEvent.inst_id == signature["symbol"])
    if signature.get("bar"):
        stmt = stmt.where(AiExperienceEvent.bar == signature["bar"])
    if signature.get("exit"):
        stmt = stmt.where(AiExperienceEvent.exit_source == signature["exit"])
    if signature.get("side"):
        stmt = stmt.where(AiExperienceEvent.side == signature["side"])
    stmt = stmt.order_by(AiExperienceEvent.exit_ts.desc()).limit(max(1, int(limit)) * 4)
    rows = (await session.execute(stmt)).scalars().all()
    out: list[dict] = []
    for row in rows:
        context = _loads(row.context_json)
        if all(context.get(axis) == value
               for axis, value in signature.items() if axis in context):
            out.append(_event_row(row))
        if len(out) >= limit:
            break
    return out


# ---------------- 节点（物化图） ----------------
async def load_nodes(session: AsyncSession, *, kinds: tuple[str, ...] = (),
                     limit: int | None = None) -> list[dict]:
    """读节点行（`kinds` 为空即全部）。按 `weight` 降序 + `key` 升序（决定性截断）。"""
    stmt = select(AiExperienceNode)
    if kinds:
        stmt = stmt.where(AiExperienceNode.kind.in_(kinds))
    stmt = stmt.order_by(AiExperienceNode.weight.desc(), AiExperienceNode.key.asc())
    if limit:
        stmt = stmt.limit(int(limit))
    return [_node_row(row) for row in (await session.execute(stmt)).scalars().all()]


def _node_row(row: AiExperienceNode) -> dict:
    return {
        "id": row.id, "kind": row.kind, "key": row.key, "axis": row.axis, "value": row.value,
        "label": row.label, "detail": row.detail, "source": row.source, "scope": row.scope,
        "tier": row.tier, "pinned": bool(row.pinned), "muted": bool(row.muted),
        "mute_reason": row.mute_reason, "support": row.support, "weight": row.weight,
        "stats": _loads(row.stats_json), "signature": _loads(row.signature_json),
        "first_ts": row.first_ts, "last_seen_ts": row.last_seen_ts,
        "prev_sign": row.prev_sign, "sign_streak": row.sign_streak,
        "created_ts": row.created_ts, "updated_ts": row.updated_ts,
    }


async def load_edges(session: AsyncSession, node_ids: list[int] | None = None) -> list[dict]:
    """读边行；`node_ids` 非空时只取两端都在集合内的边（图视图截断后不留悬空边）。"""
    stmt = select(AiExperienceEdge)
    if node_ids is not None:
        ids = set(node_ids)
        if not ids:
            return []
        stmt = stmt.where(AiExperienceEdge.src_id.in_(ids), AiExperienceEdge.dst_id.in_(ids))
    stmt = stmt.order_by(AiExperienceEdge.id.asc())
    return [{"id": row.id, "src_id": row.src_id, "dst_id": row.dst_id, "rel": row.rel,
             "weight": row.weight, "support": row.support, "updated_ts": row.updated_ts}
            for row in (await session.execute(stmt)).scalars().all()]


async def upsert_lesson_nodes(session: AsyncSession, nodes: list[dict]) -> dict:
    """**物化的唯一写路径**：按 `key` upsert 节点，六个字段绝不覆盖（评审 P0-2）。

    Args:
        session: DB 会话（调用方持 `materialize_lock()`）。
        nodes: `GraphNode` 的行字典（`graph.materialize` 产物 → `_node_payload` 转换）。

    Returns:
        `{"inserted": n, "updated": m, "keys": {key: id}}`——`keys` 供边表把 key 翻成主键 id。

    绝不覆盖：`pinned` / `muted` / `mute_reason` / `first_ts` / `created_ts`。
    `prev_sign` / `sign_streak` 由**调用方**按 §4.7 的规则前滚后传进来（本函数照写不判）——
    判据在纯核心、写入在数据层，这是本仓库已成文的分工。
    """
    if not nodes:
        return {"inserted": 0, "updated": 0, "keys": {}}
    now = int(time.time())
    keys = [node["key"] for node in nodes]
    existing = {row.key: row for row in (await session.execute(
        select(AiExperienceNode).where(AiExperienceNode.key.in_(keys)))).scalars().all()}
    inserted = updated = 0
    for node in nodes:
        row = existing.get(node["key"])
        if row is None:
            session.add(_new_node(node, now))
            inserted += 1
            continue
        _apply_update(row, node, now)
        updated += 1
    await session.commit()
    rows = (await session.execute(
        select(AiExperienceNode.key, AiExperienceNode.id)
        .where(AiExperienceNode.key.in_(keys)))).all()
    return {"inserted": inserted, "updated": updated, "keys": {k: i for k, i in rows}}


def _new_node(node: dict, now: int) -> AiExperienceNode:
    return AiExperienceNode(
        kind=node.get("kind") or _LESSON_KIND, key=node["key"], axis=node.get("axis"),
        value=node.get("value"), label=node.get("label") or "", detail=node.get("detail") or "",
        source=node.get("source") or "stat", scope=node.get("scope") or "symbol",
        tier=node.get("tier") or "candidate", pinned=bool(node.get("pinned")),
        muted=bool(node.get("muted")), mute_reason=node.get("mute_reason"),
        support=int(node.get("support") or 0), weight=float(node.get("weight") or 0.0),
        stats_json=_dumps(node.get("stats")), signature_json=_dumps(node.get("signature")),
        first_ts=int(node.get("first_ts") or 0), last_seen_ts=int(node.get("last_seen_ts") or 0),
        prev_sign=int(node.get("prev_sign") or 0), sign_streak=int(node.get("sign_streak") or 0),
        created_ts=now, updated_ts=now)


def _apply_update(row: AiExperienceNode, node: dict, now: int) -> None:
    """更新一行；`_PRESERVED` 里的五个字段一个都不碰（见模块 docstring）。"""
    row.label = node.get("label") or row.label
    row.detail = node.get("detail") if node.get("detail") is not None else row.detail
    row.scope = node.get("scope") or row.scope
    row.tier = node.get("tier") or row.tier
    row.support = int(node.get("support") or 0)
    row.weight = float(node.get("weight") or 0.0)
    row.stats_json = _dumps(node.get("stats"))
    row.signature_json = _dumps(node.get("signature"))
    row.last_seen_ts = int(node.get("last_seen_ts") or row.last_seen_ts)
    row.prev_sign = int(node.get("prev_sign") or 0)
    row.sign_streak = int(node.get("sign_streak") or 0)
    row.updated_ts = now
    if not row.first_ts:                # 只在从未有值时补，绝不覆盖既有值
        row.first_ts = int(node.get("first_ts") or 0)


async def unmute_reappeared(session: AsyncSession, keys: set[str]) -> int:
    """★ E-9（评审 P1-3）· 自动静音必须可解除：本轮重新出现的经验解除 `stale` / `flipped` 静音。

    `_apply_update` 从不触碰 `muted` / `mute_reason`，于是一条经验只要安静过一轮物化就被
    `mark_stale_lessons` 判为 `stale` 并**永久沉降**——即便后来拿到成百上千条新证据也再回不到
    prompt，而 §4.7 的档位表写的是「未被新证据佐证」。那半句此前在代码里没有对应物。

    **「绝不覆盖」的范围因此收窄为「用户意志」**：`mute_reason == "user"` 的一律不动。

    Args:
        session: DB 会话（调用方持 `materialize_lock()`）。
        keys: 本轮物化重新产出的经验 key 集合。

    Returns:
        实际被解除静音的条数。
    """
    if not keys:
        return 0
    rows = (await session.execute(select(AiExperienceNode).where(
        AiExperienceNode.kind == _LESSON_KIND,
        AiExperienceNode.key.in_(list(keys)),
        AiExperienceNode.muted.is_(True)))).scalars().all()
    touched = 0
    for row in rows:
        if row.mute_reason == "user":       # 人类意志：一次自动物化没有资格改写它
            continue
        row.muted = False
        row.mute_reason = None
        row.updated_ts = int(time.time())
        touched += 1
    if touched:
        await session.commit()
    return touched


async def mark_stale_lessons(session: AsyncSession, seen_keys: set[str], *,
                             reason: str = "stale") -> int:
    """本轮物化未出现的 `stat` 经验：**不删**，改置 `muted=True, mute_reason="stale"`。

    删除会让「这条经验去哪了」变成一个无法回答的问题。用户静音的（`mute_reason == "user"`）
    一律跳过——那是人类意志，不该被一次自动物化改写成 `stale`。
    """
    rows = (await session.execute(select(AiExperienceNode).where(
        AiExperienceNode.kind == _LESSON_KIND,
        AiExperienceNode.source == "stat"))).scalars().all()
    touched = 0
    for row in rows:
        if row.key in seen_keys or row.muted or row.mute_reason == "user":
            continue
        row.muted = True
        row.mute_reason = reason
        row.updated_ts = int(time.time())
        touched += 1
    if touched:
        await session.commit()
    return touched


async def set_lesson_mute(session: AsyncSession, node_id: int, *, muted: bool,
                          reason: str | None) -> bool:
    """**自动化**的静音写点（`mute_reason` 由调用方显式给定）。

    刻意与 `patch_node` 分开：后者把 `mute_reason` 写死成 `"user"`——那是「人类按下了静音」
    这一个事实的标记，也是 `mark_stale_lessons` / `unmute_reappeared` 判断「不许自动改写」的
    唯一依据。让效用闭环走 `patch_node` 会把每一次自动静音都伪装成用户意志，此后再也解除不掉
    （`unmute_reappeared` 跳过 `user`），而且**不报任何错**。

    用户意志同样受保护：`mute_reason == "user"` 的行本函数一律不动。

    Args:
        session: DB 会话。
        node_id: 经验节点主键。
        muted: 目标静音状态。
        reason: 静音原因（`ineffective` / `stale` / `flipped`）；解除时传 `None`。

    Returns:
        是否真的改动了这一行。
    """
    row = await session.get(AiExperienceNode, int(node_id))
    if row is None or row.mute_reason == "user":
        return False
    if bool(row.muted) == bool(muted) and row.mute_reason == reason:
        return False
    row.muted = bool(muted)
    row.mute_reason = reason if muted else None
    row.updated_ts = int(time.time())
    await session.commit()
    return True


async def replace_edges(session: AsyncSession, edges: list[dict], key_to_id: dict) -> int:
    """整体替换物化边（一个事务内）。悬空边（端点不在节点表）一律丢弃。

    边**可以**全量替换——它没有任何用户意志附着其上（用户不会「静音一条边」），且重算是幂等的。
    这与节点的 upsert 语义**刻意不同**，理由见模块 docstring。
    """
    await session.execute(delete(AiExperienceEdge))
    now = int(time.time())
    payload = []
    seen: set[tuple[int, int, str]] = set()
    for edge in edges:
        src = key_to_id.get(edge["src_key"])
        dst = key_to_id.get(edge["dst_key"])
        if src is None or dst is None or src == dst:
            continue
        identity = (src, dst, edge["rel"])
        if identity in seen:
            continue
        seen.add(identity)
        payload.append({"src_id": src, "dst_id": dst, "rel": edge["rel"],
                        "weight": float(edge.get("weight") or 0.0),
                        "support": int(edge.get("support") or 0), "updated_ts": now})
    if payload:
        await session.execute(sqlite_insert(AiExperienceEdge).values(payload)
                              .on_conflict_do_nothing(
                                  index_elements=["src_id", "dst_id", "rel"]))
    await session.commit()
    return len(payload)


async def get_node(session: AsyncSession, node_id: int) -> dict | None:
    row = await session.get(AiExperienceNode, int(node_id))
    return None if row is None else _node_row(row)


async def create_node(session: AsyncSession, values: dict) -> dict:
    """手工新增 / 导入一条经验（`source ∈ {manual, imported}`，`tier` 恒 `candidate`）。

    **按 `key` 幂等**：同一句话 + 同一语境重复导入只更新文本，不会翻倍——`key` 上有 UNIQUE，
    不做这一步的话第二次导入直接 500，而用户看到的只是「导入失败」。
    """
    now = int(time.time())
    existing = (await session.execute(select(AiExperienceNode).where(
        AiExperienceNode.key == values["key"]))).scalars().first()
    if existing is not None:
        existing.label = values.get("label") or existing.label
        existing.detail = values.get("detail") or existing.detail
        existing.signature_json = _dumps(values.get("signature"))
        existing.updated_ts = now
        await session.commit()
        await session.refresh(existing)
        return _node_row(existing)
    row = AiExperienceNode(
        kind=_LESSON_KIND, key=values["key"], label=values.get("label") or "",
        detail=values.get("detail") or "", source=values.get("source") or "manual",
        scope=values.get("scope") or "symbol", tier=values.get("tier") or "candidate",
        support=int(values.get("support") or 0), weight=float(values.get("weight") or 0.0),
        stats_json=_dumps(values.get("stats")), signature_json=_dumps(values.get("signature")),
        first_ts=now, last_seen_ts=now, created_ts=now, updated_ts=now)
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return _node_row(row)


async def patch_node(session: AsyncSession, node_id: int, patch: dict) -> dict | None:
    """更新节点的可写子集（陈述 / 长文 / 置顶 / 静音 / 档位）。

    静音由用户按下时 `mute_reason` 恒为 `"user"`——这个值是 `mark_stale_lessons` 与
    `upsert_lesson_nodes` 判断「这是人类意志、不许自动改写」的唯一依据。
    """
    row = await session.get(AiExperienceNode, int(node_id))
    if row is None:
        return None
    if patch.get("label") is not None:
        row.label = str(patch["label"])[:200]
    if patch.get("detail") is not None:
        row.detail = str(patch["detail"])[:600]
    if patch.get("pinned") is not None:
        row.pinned = bool(patch["pinned"])
    if patch.get("muted") is not None:
        row.muted = bool(patch["muted"])
        row.mute_reason = "user" if row.muted else None
    if patch.get("tier") is not None:
        row.tier = str(patch["tier"])
    row.updated_ts = int(time.time())
    await session.commit()
    await session.refresh(row)
    return _node_row(row)


async def delete_node(session: AsyncSession, node_id: int) -> bool:
    """删除一个节点及其全部边；调用方负责挡住 `stat` 类（删了会被下次物化复活，是假动作）。"""
    row = await session.get(AiExperienceNode, int(node_id))
    if row is None:
        return False
    await session.execute(delete(AiExperienceEdge).where(
        (AiExperienceEdge.src_id == row.id) | (AiExperienceEdge.dst_id == row.id)))
    await session.delete(row)
    await session.commit()
    return True


async def overview(session: AsyncSession) -> dict:
    """概览条读数（档位分布 + 事件数 + 覆盖标的 + 最近更新 + 两个诚实性计数）。"""
    tiers = {"iron": 0, "normal": 0, "candidate": 0, "muted": 0, "archived": 0}
    rows = (await session.execute(select(AiExperienceNode).where(
        AiExperienceNode.kind == _LESSON_KIND))).scalars().all()
    last_updated = 0
    for row in rows:
        last_updated = max(last_updated, int(row.updated_ts or 0))
        if row.muted:
            tiers["muted"] += 1
        elif row.tier in tiers:
            tiers[row.tier] += 1
    # ★ E-2：这三个计数与 `symbols` 都必须走 `STAT_ORIGINS`——`events` / `sessions` 是 L0 页眉
    # 那句「来自 X 组有成交记录的自动交易的 Y 笔已结算往返」的**唯一**取数来源，口径不一致
    # 就是在喂给下真单的模型的 prompt 里写一个假数字（`v1:I-9`，A-42 逐值断言）。
    stat_only = AiExperienceEvent.origin.in_(list(STAT_ORIGINS))
    symbols = [s for (s,) in (await session.execute(
        select(AiExperienceEvent.inst_id).distinct().where(stat_only)
        .order_by(AiExperienceEvent.inst_id.asc()))).all() if s]
    sessions = int((await session.execute(select(func.count(func.distinct(
        AiExperienceEvent.group_id))).where(stat_only))).scalar() or 0)
    excluded = int((await session.execute(select(func.count(AiExperienceEvent.id)).where(
        stat_only,
        (AiExperienceEvent.cost_basis != "net") | (AiExperienceEvent.fee_known.is_(False))
    ))).scalar() or 0)
    return {"lessons": tiers, "events": await count_events(session), "sessions": sessions,
            "symbols": symbols, "lastUpdatedTs": last_updated,
            "excludedByCostBasis": excluded}


async def stream_export_rows(session: AsyncSession, query: EventQuery, *, page: int = 500):
    """导出的分页游标（NDJSON 流的取数点，评审 P2-7）。

    **必须流式**：跑半年的库是几万条事件，一次性拼进内存再序列化会在桌面端把整个进程顶起来。
    先发全部节点与边（量级受 `graph_node_cap` 约束），再按页发事件。
    """
    for row in await load_nodes(session):
        yield {"type": "node", "data": row}
    for row in await load_edges(session):
        yield {"type": "edge", "data": row}
    offset = 0
    while True:
        batch = await load_experience_events(
            session, EventQuery(inst_id=query.inst_id, bar=query.bar,
                                since_ts=query.since_ts, until_ts=query.until_ts,
                                origins=query.origins, limit=page, offset=offset))
        if not batch:
            return
        for row in batch:
            yield {"type": "event", "data": row}
        if len(batch) < page:
            return
        offset += page
