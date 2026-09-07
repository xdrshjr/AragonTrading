"""`ai_trade_group_states` 的读写（ai-trade-pause-resume §3.4）。

镜像 `ai_prior_repositories.py` 的小文件先例：本模块只做「行 ⇄ dict」的搬运与 upsert，
**不做任何业务判定**（判定在 `ai_auto/pause.py`，编排在 `ai_auto/manager.py` / `resume.py`）。

极性（§3.4.2）：整行写（W1/W4/W5）失败**必须上抛**——契约没落地却说续跑可用是欺骗；
成交与心跳的增量写（W2/W3）由调用方包 best-effort，绝不阻塞热路径。
"""
from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from functools import wraps
from weakref import WeakKeyDictionary

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import AiTradeGroupState

_PAUSED = "paused"
_WRITE_LOCKS = WeakKeyDictionary()


def state_write_lock() -> asyncio.Lock:
    """当前后端事件循环的成员状态写锁，所有生命周期写点共用。"""
    loop = asyncio.get_running_loop()
    return _WRITE_LOCKS.setdefault(loop, asyncio.Lock())


def _serialized_write(operation):
    @wraps(operation)
    async def write(session, *args, **kwargs):
        async with state_write_lock():
            try:
                return await operation(session, *args, **kwargs)
            except BaseException:
                await session.rollback()
                raise
    return write


def _has_pending(member: dict) -> bool:
    """只有显式空值表示无未决记录，不可读身份仍需保留恢复入口。"""
    runtime = member.get("runtimeIntegrity")
    if runtime is None:
        return False
    if not isinstance(runtime, dict):
        return True
    queue = runtime.get("pendingTrades")
    if queue is not None and queue != []:
        return True
    if runtime.get("pendingOpenAttempt") is not None:
        return True
    adopted = runtime.get("adoptedOrder")
    if adopted is None:
        return False
    return (not isinstance(adopted, dict)
            or adopted.get("resting") is not None)


def _merge_members(current: list[dict], proposed: list[dict]) -> list[dict]:
    """普通快照保留最新事务身份；被移除的未决成员继续留档。"""
    existing = {(m.get("instId"), m.get("runId")): m for m in current}
    merged = []
    for member in proposed:
        previous = existing.pop((member.get("instId"), member.get("runId")), {})
        row = {**previous, **member}
        if "runtimeIntegrity" in previous:
            row["runtimeIntegrity"] = previous["runtimeIntegrity"]
        merged.append(row)
    merged.extend({**m, "state": "stopped"} for m in existing.values()
                  if _has_pending(m))
    return merged


@dataclass
class GroupStateWrite:
    """整行 upsert 的入参（聚合结构体，避免位置堆参）。"""

    group_id: str
    status: str
    account_id: int = 0
    bar: str = ""
    start_wire: dict = field(default_factory=dict)
    resolved_echo: dict = field(default_factory=dict)
    members: list[dict] = field(default_factory=list)
    pause_reason: str | None = None
    paused_ts: int | None = None
    resumed_count: int = 0


@_serialized_write
async def upsert_group_state(session: AsyncSession, write: GroupStateWrite) -> None:
    """按 `group_id` 整行 upsert（W1 / W4）。自带 commit（镜像 `append_live_trade` 的语义）。"""
    now = int(time.time())
    row = await _get_row(session, write.group_id)
    if row is None:
        row = AiTradeGroupState(group_id=write.group_id, created_ts=now, last_seen_ts=now)
        session.add(row)
    row.status = write.status
    row.account_id = int(write.account_id or 0)
    row.bar = str(write.bar or "")
    row.start_wire_json = _dump(write.start_wire)
    row.resolved_echo_json = _dump(write.resolved_echo)
    row.members_json = _dump(_merge_members(
        _load(row.members_json, []), write.members))
    row.pause_reason = write.pause_reason
    row.paused_ts = write.paused_ts
    row.resumed_count = int(write.resumed_count or 0)
    row.last_seen_ts = now
    row.updated_ts = now
    await session.commit()


@_serialized_write
async def patch_group_state(session: AsyncSession, group_id: str, fields: dict) -> bool:
    """局部更新（W2 / W3 / W5）；行不存在 ⇒ False（绝不静默新建一行没人认领的契约）。

    `fields` 允许的键：`status` / `pauseReason` / `pausedTs` / `resumedCount` / `lastSeenTs`
    / `members`（整表替换）。未知键一律忽略——这里是搬运层，不做校验。
    """
    row = await _get_row(session, group_id)
    if row is None:
        return False
    if "status" in fields:
        row.status = str(fields["status"])
    if "pauseReason" in fields:
        row.pause_reason = fields["pauseReason"]
    if "pausedTs" in fields:
        row.paused_ts = fields["pausedTs"]
    if "resumedCount" in fields:
        row.resumed_count = int(fields["resumedCount"] or 0)
    if "members" in fields:
        row.members_json = _dump(_merge_members(
            _load(row.members_json, []), fields["members"]))
    row.last_seen_ts = int(fields.get("lastSeenTs") or time.time())
    row.updated_ts = int(time.time())
    await session.commit()
    return True


@_serialized_write
async def patch_member_state(session: AsyncSession, group_id: str, inst_id: str,
                             patch: dict) -> bool:
    """只改某成员的若干键（W2 成交驱动写点）；组 / 成员不存在 ⇒ False。"""
    return await _patch_member(session, group_id, inst_id, patch, False)


@_serialized_write
async def patch_runtime_integrity(session: AsyncSession, group_id: str,
                                  inst_id: str, patch: dict) -> bool:
    """同一事务提交当前run的额度、待核实订单和持仓身份。"""
    if patch.get("runId") is None:
        return False
    return await _patch_member(session, group_id, inst_id, patch, True)


@_serialized_write
async def retry_pending_trades(session: AsyncSession, group_id: str) -> bool:
    """恢复前重放原始成交；插入已提交但清队列失败时仍可按事件身份重试。"""
    from ..ai_auto.trade_persistence import (
        append_trade_once, pending_trades_for_member,
    )

    row = await _get_row(session, group_id)
    if row is None:
        return False
    members = _load(row.members_json, [])
    changed = False
    for member in members:
        queue = pending_trades_for_member(member)
        if not queue:
            continue
        for trade in queue:
            await append_trade_once(session, member["runId"], trade)
        member["runtimeIntegrity"]["pendingTrades"] = []
        # 队列只是欠写的子集，成员位置可能已包含之后成功保存的反手开仓。
        changed = True
    if changed:
        row.members_json = _dump(members)
        row.updated_ts = int(time.time())
        await session.commit()
    return True


async def _patch_member(session, group_id, inst_id, patch, authoritative):
    row = await _get_row(session, group_id)
    if row is None:
        return False
    if "accountId" in patch and patch["accountId"] != row.account_id:
        return False
    members = _load(row.members_json, [])
    hit = False
    for member in members:
        if str(member.get("instId") or "") == inst_id:
            if "runId" in patch and member.get("runId") != patch["runId"]:
                return False
            changes = dict(patch)
            for key in ("expectedEpochKey", "expectedSettlementRevision",
                        "accountId"):
                changes.pop(key, None)
            if authoritative and "runtimeIntegrity" in patch:
                runtime = _merge_runtime(member, patch, row.account_id)
                if runtime is None:
                    return False
                changes["runtimeIntegrity"] = runtime
            elif "runtimeIntegrity" in member:
                changes.pop("runtimeIntegrity", None)
            member.update(changes)
            hit = True
    if not hit:
        return False
    row.members_json = _dump(members)
    row.last_seen_ts = row.updated_ts = int(time.time())
    await session.commit()
    return True


def _merge_runtime(member: dict, patch: dict, account_id: int) -> dict | None:
    """事务字段分别归属，结算观察使用旧 epoch/revision 比较后交换。"""
    previous = member.get("runtimeIntegrity") or {}
    proposed = patch["runtimeIntegrity"]
    if not isinstance(previous, dict) or not isinstance(proposed, dict):
        return None
    changes = dict(proposed)
    # revision 只由本事务推进，开仓回调不能回灌旧版本。
    changes.pop("settlementStateRevision", None)
    if "settlementContext" in changes:
        if not _matches_settlement_patch(member, patch, account_id):
            return None
        changes["settlementStateRevision"] = (
            int(previous.get("settlementStateRevision", 0)) + 1)
    return {**previous, **changes}


def _matches_settlement_patch(member, patch, account_id) -> bool:
    previous = member.get("runtimeIntegrity") or {}
    old_context = previous.get("settlementContext") or {}
    if "expectedEpochKey" not in patch:
        return False
    if patch["expectedEpochKey"] != old_context.get("epochKey"):
        return False
    revision = previous.get("settlementStateRevision", 0)
    if patch.get("expectedSettlementRevision") != revision:
        return False
    context = patch["runtimeIntegrity"]["settlementContext"]
    if context is None:
        return True
    if not isinstance(context, dict) or context.get("schemaVersion") != 1:
        return False
    return (context.get("accountId") == account_id
            and context.get("runId") == member.get("runId")
            and context.get("instId") == member.get("instId")
            and bool(context.get("epochKey")))


async def load_group_state(session: AsyncSession, group_id: str) -> dict | None:
    """按 `group_id` 读整行 → wire dict；不存在 ⇒ None。"""
    row = await _get_row(session, group_id)
    return None if row is None else _row_out(row)


async def load_paused_group_brief(session: AsyncSession) -> dict | None:
    """当前处于 `paused` 的那一行（至多一个，I-2）→ wire dict；没有 ⇒ None。

    容量闸（`AiTradeManager._require_capacity`）与冷启水合都读它：**真值取状态表，不取内存**
    （[P0-3]：内存 `_paused_ref` 的装载是 best-effort，一次偶发失败绝不该变成「可以在一份带仓
    的暂停契约之上再开一个新分组」）。
    """
    row = (await session.execute(
        select(AiTradeGroupState).where(AiTradeGroupState.status == _PAUSED)
        .order_by(AiTradeGroupState.updated_ts.desc()).limit(1))).scalars().first()
    if row is not None:
        return _row_out(row)
    pending = await load_pending_groups(session)
    return max(pending, key=lambda item: item["updatedTs"]) if pending else None


async def load_pending_groups(session: AsyncSession,
                              account_id: int | None = None) -> list[dict]:
    """读取包括ended在内的未决契约，供启动准入及冷恢复核查。"""
    query = select(AiTradeGroupState)
    if account_id is not None:
        query = query.where(AiTradeGroupState.account_id == account_id)
    rows = (await session.execute(query)).scalars().all()
    return [_row_out(row) for row in rows
            if any(_has_pending(m) for m in _load(row.members_json, []))]


@_serialized_write
async def remove_member_state(session: AsyncSession, group_id: str,
                               inst_id: str, run_id: int) -> bool:
    """按当前身份摘成员；未决订单仍保留停止态契约。"""
    row = await _get_row(session, group_id)
    if row is None:
        return False
    members = _load(row.members_json, [])
    remaining = []
    for member in members:
        matches = member.get("instId") == inst_id
        matches = matches and member.get("runId") == run_id
        if not matches or _has_pending(member):
            remaining.append({**member, "state": "stopped"} if matches else member)
    row.members_json = _dump(remaining)
    row.updated_ts = int(time.time())
    await session.commit()
    return True


@_serialized_write
async def delete_group_state(session: AsyncSession, group_id: str) -> int:
    """删掉该组的契约行（启动回滚的竞态清理，G-30）；返回删除条数。"""
    row = await _get_row(session, group_id)
    if row and any(_has_pending(m) for m in _load(row.members_json, [])):
        return 0
    result = await session.execute(
        delete(AiTradeGroupState).where(AiTradeGroupState.group_id == group_id))
    await session.commit()
    return int(result.rowcount or 0)


async def _get_row(session: AsyncSession, group_id: str) -> AiTradeGroupState | None:
    return (await session.execute(
        select(AiTradeGroupState)
        .where(AiTradeGroupState.group_id == group_id)
        .execution_options(populate_existing=True))).scalars().first()


def _row_out(row: AiTradeGroupState) -> dict:
    """ORM 行 → camelCase wire（坏 JSON 一律回落空壳，与 `run_out` 的既有容错一致）。"""
    return {"groupId": row.group_id, "status": row.status, "accountId": row.account_id,
            "bar": row.bar, "startWire": _load(row.start_wire_json, {}),
            "resolvedEcho": _load(row.resolved_echo_json, {}),
            "members": _load(row.members_json, []),
            "pauseReason": row.pause_reason, "pausedTs": int(row.paused_ts or 0),
            "resumedCount": int(row.resumed_count or 0),
            "lastSeenTs": int(row.last_seen_ts or 0), "createdTs": int(row.created_ts or 0),
            "updatedTs": int(row.updated_ts or 0)}


def _dump(value) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False)


def _load(raw: str | None, fallback):
    try:
        out = json.loads(raw or "")
    except (TypeError, ValueError):
        return fallback
    return out if isinstance(out, type(fallback)) else fallback
