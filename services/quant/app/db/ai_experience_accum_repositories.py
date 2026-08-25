"""增量物化底座的数据层：桶累加器 / 语境计数器 / 注入台账（ai-trade-experience-compounding §6.1）。

本模块**自包含**（沿用 `ai_experience_repositories.py` 的既有拆分范式）：不 import 同仓在制模块，
公开函数经 `repositories.py` re-export，调用点仍用 `repo.X`。

三条纪律：

- **按 `(epoch, key)` upsert，绝不整表替换**。累加器是 append-only 语义的和式：整表替换会在
  「写了一半」的瞬间把一个桶的历史清零，而那不会报错，只会让一条经验的样本数突然变小。
- **写入必须是有界批次**。SQLite 的绑定参数上限是 999（旧版）/ 32766，一次 upsert 几千个桶
  × 二十余列会当场超限。故本模块的写函数一律分片，分片大小由列数推出而不是拍脑袋。
- **读出来的是行字典**，纯核心不认识 ORM 对象（与经验层其余模块同构）。
"""
from __future__ import annotations

import json
import time

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from .models import AiExperienceBucket, AiExperienceContextCount, AiExperienceInjection

# 一次 `INSERT` 的最大行数。SQLite 的 `SQLITE_MAX_VARIABLE_NUMBER` 在旧版是 999——桶行有 24 列，
# 故 40 行/片留足余量；语境计数行只有 5 列，用同一个数是刻意的保守（分片本身几乎不花钱）。
_CHUNK = 40


def _dumps(value) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False)


def _loads(raw: str | None, fallback):
    if not raw:
        return fallback
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError, ValueError):
        return fallback
    return parsed if isinstance(parsed, type(fallback)) else fallback


def _chunks(rows: list, size: int = _CHUNK):
    for start in range(0, len(rows), size):
        yield rows[start:start + size]


# ---------------- 桶累加器 ----------------
async def upsert_experience_buckets(session: AsyncSession, epoch: int, rows: list[dict], *,
                                    commit: bool = True) -> int:
    """按 `(epoch, key)` 整行覆盖累加器（调用方给的就是**合并后**的最终值）。

    Args:
        session: DB 会话（调用方持物化锁）。
        epoch: 当前 epoch。
        rows: `{"key", "signature", "scope", **accumulate.row(acc)}` 的列表。
        commit: 是否在本函数内提交；`False` ⇒ 由调用方与语境计数**合成一次提交**。
            一次增量在大库上的耗时里，绝大部分是 SQLite 的提交延迟而不是计算（G-15 实测）。

    Returns:
        写入行数。
    """
    if not rows:
        return 0
    now = int(time.time())
    payload = [{
        "epoch": int(epoch), "key": row["key"],
        "signature_json": _dumps(row.get("signature")), "scope": row.get("scope") or "symbol",
        "n": int(row.get("n") or 0), "excluded": int(row.get("excluded") or 0),
        "wins": int(row.get("wins") or 0), "sum_net": float(row.get("sum_net") or 0.0),
        "sum_net_sq": float(row.get("sum_net_sq") or 0.0),
        "sum_hold": float(row.get("sum_hold") or 0.0),
        "sum_cost": float(row.get("sum_cost") or 0.0), "cost_n": int(row.get("cost_n") or 0),
        "sum_abs_gross": float(row.get("sum_abs_gross") or 0.0),
        "gross_n": int(row.get("gross_n") or 0),
        "sum_edge_err": float(row.get("sum_edge_err") or 0.0),
        "edge_n": int(row.get("edge_n") or 0),
        "hist_json": _dumps(row.get("hist")),
        "symbols_json": json.dumps(list(row.get("symbols") or []), ensure_ascii=False),
        "symbols_overflow": bool(row.get("symbols_overflow")),
        "first_ts_kept": int(row.get("first_ts_kept") or 0),
        "last_ts_kept": int(row.get("last_ts_kept") or 0),
        "first_ts_all": int(row.get("first_ts_all") or 0),
        "last_ts_all": int(row.get("last_ts_all") or 0),
        "cost_fallback": bool(row.get("cost_fallback")), "updated_ts": now,
    } for row in rows]
    for chunk in _chunks(payload):
        stmt = sqlite_insert(AiExperienceBucket).values(chunk)
        await session.execute(stmt.on_conflict_do_update(
            index_elements=["epoch", "key"],
            set_={key: getattr(stmt.excluded, key) for key in chunk[0]
                  if key not in ("epoch", "key")}))
    if commit:
        await session.commit()
    return len(payload)


async def load_experience_buckets(session: AsyncSession, epoch: int) -> list[dict]:
    """读某个 epoch 的全部累加器行（增量步的底座；空 ⇒ `[]` ⇒ 判据 B-4 打回全量）。"""
    rows = (await session.execute(select(AiExperienceBucket).where(
        AiExperienceBucket.epoch == int(epoch))
        .order_by(AiExperienceBucket.key.asc()))).scalars().all()
    return [_bucket_row(row) for row in rows]


def _bucket_row(row: AiExperienceBucket) -> dict:
    return {
        "key": row.key, "signature": _loads(row.signature_json, {}), "scope": row.scope,
        "n": row.n, "excluded": row.excluded, "wins": row.wins, "sum_net": row.sum_net,
        "sum_net_sq": row.sum_net_sq, "sum_hold": row.sum_hold, "sum_cost": row.sum_cost,
        "cost_n": row.cost_n, "sum_abs_gross": row.sum_abs_gross, "gross_n": row.gross_n,
        "sum_edge_err": row.sum_edge_err, "edge_n": row.edge_n,
        "hist": _loads(row.hist_json, {}), "symbols": _loads(row.symbols_json, []),
        "symbols_overflow": bool(row.symbols_overflow),
        "first_ts_kept": row.first_ts_kept, "last_ts_kept": row.last_ts_kept,
        "first_ts_all": row.first_ts_all, "last_ts_all": row.last_ts_all,
        "cost_fallback": bool(row.cost_fallback), "updated_ts": row.updated_ts,
    }


async def count_experience_buckets(session: AsyncSession, epoch: int) -> int:
    """某个 epoch 的累加器行数（判据 B-4「累加器表非空」的取数点）。"""
    return int((await session.execute(select(func.count(AiExperienceBucket.id)).where(
        AiExperienceBucket.epoch == int(epoch)))).scalar() or 0)


async def purge_experience_epochs(session: AsyncSession, keep_epoch: int) -> int:
    """删掉除 `keep_epoch` 之外的全部累加器与语境计数（全量重建开新 epoch 时的收尾）。

    不删旧 epoch 的后果是这两张表随重建次数**线性无界增长**，而它们对旧 epoch 毫无读点——
    epoch 一换，那批分位就再也不会被用来解释任何一条经验了。
    """
    removed = 0
    for model in (AiExperienceBucket, AiExperienceContextCount):
        result = await session.execute(delete(model).where(model.epoch != int(keep_epoch)))
        removed += int(result.rowcount or 0)
    await session.commit()
    return removed


# ---------------- 语境计数器 ----------------
async def upsert_experience_context_counts(session: AsyncSession, epoch: int,
                                           rows: list[dict], *,
                                           commit: bool = True) -> int:
    """按 `(epoch, a_key, b_key)` 整行覆盖计数（`b_key == ""` = 单点计数）。

    调用方只传**本批真的变过**的那些行：整表重写在稳态下是几千行换十几行的改动，而这张表
    每一次增量都要写一遍。`commit=False` 时由调用方与累加器合成一次提交。
    """
    if not rows:
        return 0
    now = int(time.time())
    payload = [{"epoch": int(epoch), "a_key": row["a"], "b_key": row.get("b") or "",
                "count": int(row.get("count") or 0), "updated_ts": now} for row in rows]
    for chunk in _chunks(payload, size=120):
        stmt = sqlite_insert(AiExperienceContextCount).values(chunk)
        await session.execute(stmt.on_conflict_do_update(
            index_elements=["epoch", "a_key", "b_key"],
            set_={"count": stmt.excluded.count, "updated_ts": stmt.excluded.updated_ts}))
    if commit:
        await session.commit()
    return len(payload)


async def load_experience_context_counts(session: AsyncSession, epoch: int) -> list[dict]:
    """读某个 epoch 的全部语境计数（`co_occurs` 与 `context.support` 的唯一来源）。"""
    rows = (await session.execute(select(AiExperienceContextCount).where(
        AiExperienceContextCount.epoch == int(epoch))
        .order_by(AiExperienceContextCount.a_key.asc(),
                  AiExperienceContextCount.b_key.asc()))).scalars().all()
    return [{"a": row.a_key, "b": row.b_key, "count": int(row.count or 0)} for row in rows]


# ---------------- 注入台账 ----------------
async def append_experience_injections(session: AsyncSession, rows: list[dict], *,
                                       commit: bool = True) -> int:
    """落注入台账（`INSERT OR IGNORE`，幂等键与事件表逐字同源）。

    `commit=False` 是给「与事件行**同一次 commit**」那条路径用的（★ W-C §4.6.1）：开了仓不一定
    平（进程崩溃 / 被接管），开仓时写会在表里留下一批 `net_bps` 永远为 NULL 的悬空行，而那些行
    会静默拉偏效用统计。故台账只在平仓那一刻写，且与事件行同生共死。

    Args:
        session: DB 会话。
        rows: 台账行（列名即 `AiExperienceInjection` 的列）。
        commit: 是否在本函数内提交；`False` ⇒ 由调用方统一提交。

    Returns:
        提交的行数（被幂等键挡下的重复不单独计——那是一次幂等重放，不是失败）。
    """
    if not rows:
        return 0
    now = int(time.time())
    payload = [{
        "group_id": row.get("group_id") or "", "inst_id": row.get("inst_id") or "",
        "seq": int(row.get("seq") or 0), "exit_ts": int(row.get("exit_ts") or 0),
        "lesson_key": row.get("lesson_key") or "", "layer": row.get("layer") or "l0",
        "claim_sign": int(row.get("claim_sign") or 0),
        "injected_wall_ts": int(row.get("injected_wall_ts") or 0),
        "net_bps": row.get("net_bps"), "fee_known": bool(row.get("fee_known")),
        "cost_basis": row.get("cost_basis") or "gross",
        "created_ts": int(row.get("created_ts") or now),
    } for row in rows]
    for chunk in _chunks(payload, size=60):
        await session.execute(sqlite_insert(AiExperienceInjection).values(chunk)
                              .on_conflict_do_nothing(
                                  index_elements=["group_id", "inst_id", "seq", "exit_ts",
                                                  "lesson_key"]))
    if commit:
        await session.commit()
    return len(payload)


async def load_experience_injections(session: AsyncSession, *, lesson_key: str | None = None,
                                     limit: int | None = None) -> list[dict]:
    """读注入台账（效用聚合读全表、详情抽屉按 `lesson_key` 读最近 N 条）。

    Args:
        session: DB 会话。
        lesson_key: 只读这一条经验的注入记录；`None` ⇒ 全部。
        limit: 只取最近 N 条（按 `exit_ts` 降序）；`None` ⇒ 全部（按 `exit_ts` 升序，决定性）。

    Returns:
        行字典列表。
    """
    stmt = select(AiExperienceInjection)
    if lesson_key:
        stmt = stmt.where(AiExperienceInjection.lesson_key == str(lesson_key))
    if limit:
        stmt = stmt.order_by(AiExperienceInjection.exit_ts.desc(),
                             AiExperienceInjection.id.desc()).limit(int(limit))
    else:
        stmt = stmt.order_by(AiExperienceInjection.exit_ts.asc(),
                            AiExperienceInjection.id.asc())
    return [{
        "id": row.id, "groupId": row.group_id, "instId": row.inst_id, "seq": row.seq,
        "exitTs": row.exit_ts, "lessonKey": row.lesson_key, "layer": row.layer,
        "claimSign": row.claim_sign, "injectedWallTs": row.injected_wall_ts,
        "netBps": row.net_bps, "feeKnown": bool(row.fee_known), "costBasis": row.cost_basis,
    } for row in (await session.execute(stmt)).scalars().all()]


async def delete_orphan_experience_injections(session: AsyncSession,
                                              lesson_keys: set[str]) -> int:
    """删掉指向**已不存在**经验的台账行（`prune` 顺带做，防无界增长，R-9）。"""
    if not lesson_keys:
        return 0
    result = await session.execute(delete(AiExperienceInjection).where(
        AiExperienceInjection.lesson_key.in_(list(lesson_keys))))
    await session.commit()
    return int(result.rowcount or 0)
