"""`ai_trade_group_states` 的读写（ai-trade-pause-resume §3.4）。

镜像 `ai_prior_repositories.py` 的小文件先例：本模块只做「行 ⇄ dict」的搬运与 upsert，
**不做任何业务判定**（判定在 `ai_auto/pause.py`，编排在 `ai_auto/manager.py` / `resume.py`）。

极性（§3.4.2）：整行写（W1/W4/W5）失败**必须上抛**——契约没落地却说续跑可用是欺骗；
成交与心跳的增量写（W2/W3）由调用方包 best-effort，绝不阻塞热路径。
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import AiTradeGroupState

_PAUSED = "paused"


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
    row.members_json = _dump(write.members)
    row.pause_reason = write.pause_reason
    row.paused_ts = write.paused_ts
    row.resumed_count = int(write.resumed_count or 0)
    row.last_seen_ts = now
    row.updated_ts = now
    await session.commit()


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
        row.members_json = _dump(fields["members"])
    row.last_seen_ts = int(fields.get("lastSeenTs") or time.time())
    row.updated_ts = int(time.time())
    await session.commit()
    return True


async def patch_member_state(session: AsyncSession, group_id: str, inst_id: str,
                             patch: dict) -> bool:
    """只改某成员的若干键（W2 成交驱动写点）；组 / 成员不存在 ⇒ False。"""
    row = await _get_row(session, group_id)
    if row is None:
        return False
    members = _load(row.members_json, [])
    hit = False
    for member in members:
        if str(member.get("instId") or "") == inst_id:
            member.update(patch)
            hit = True
    if not hit:
        return False
    row.members_json = _dump(members)
    row.last_seen_ts = row.updated_ts = int(time.time())
    await session.commit()
    return True


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
    return None if row is None else _row_out(row)


async def delete_group_state(session: AsyncSession, group_id: str) -> int:
    """删掉该组的契约行（启动回滚的竞态清理，G-30）；返回删除条数。"""
    result = await session.execute(
        delete(AiTradeGroupState).where(AiTradeGroupState.group_id == group_id))
    await session.commit()
    return int(result.rowcount or 0)


async def _get_row(session: AsyncSession, group_id: str) -> AiTradeGroupState | None:
    return (await session.execute(
        select(AiTradeGroupState)
        .where(AiTradeGroupState.group_id == group_id))).scalars().first()


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
