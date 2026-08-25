"""**不可信输入**的夹紧与导入（§五 的 `POST /nodes` 与 `POST /import`）。

从 `service.py` 拆出来的理由是尺寸：那个文件已经贴着 CLAUDE.md 的 800 行阈值，而这一段有一条
清楚的职责边界——它处理的全部是**别人给的字节**（用户手写、LLM 蒸馏、别人导出的 JSON），
而 `service.py` 的其余部分处理的是**本地事实**（事件、图、快照、阈值）。

R-8 的六条防线加 ★ E-10 的两条，全部落在本模块：
强制 `source` / `tier`；轴与取值走 `is_valid` 闭合词表；长度夹紧；边与事件一律拒收；
不解析任何可执行内容；**条数上限**；`replace` 的删与写在**同一个事务**里。
"""
from __future__ import annotations

import hashlib
import json
import logging
import time

from sqlalchemy.ext.asyncio import AsyncSession

from . import taxonomy as tax

logger = logging.getLogger(__name__)

_MAX_STATEMENT = 120
_MAX_DETAIL = 600


def manual_key(statement: str, signature: dict) -> str:
    """手工 / 导入经验的稳定 key（同一句话 + 同一语境 ⇒ 同一条，重复导入不会翻倍）。"""
    raw = f"{statement}#{tax.signature_key(signature)}".encode()
    return "lesson:m" + hashlib.sha1(raw).hexdigest()[:11]     # noqa: S324 —— 身份哈希，非安全用途


def sanitize_lesson(raw: dict, *, source: str) -> dict | None:
    """把一条**不可信**的经验夹紧到可入库的形状；越界轴 / 空陈述 ⇒ `None`（整条丢弃）。

    导入是不可信输入（R-8）：越界档位、超长文本、`tier:"iron"`、注入型字符串全部在这里挡下。
    强制 `source` 由调用方给定、`tier` 恒 `candidate`——一条导入的经验绝不能一进门就是铁律。
    """
    statement = str(raw.get("statement") or raw.get("label") or "").strip()
    if not statement:
        return None
    signature = {}
    for axis, value in (raw.get("context") or raw.get("signature") or {}).items():
        axis_id = str(axis)
        if axis_id in tax.AXES and tax.is_valid(axis_id, value):
            signature[axis_id] = str(value)
    if not signature:
        return None
    return {"key": manual_key(statement[:_MAX_STATEMENT], signature),
            "label": statement[:_MAX_STATEMENT],
            "detail": str(raw.get("detail") or "")[:_MAX_DETAIL],
            "source": source, "tier": "candidate",
            "scope": "symbol" if tax.AXIS_SYMBOL in signature else "global",
            "signature": signature, "stats": {"n": 0, "manual": True},
            # `weight` 的真值由下一次物化按 `manual_seed_weight` 写入（★ E-2）；出生时留 0.0 是
            # 诚实的——它此刻确实还没有任何证据，而检索侧的下限（`retrieval._evidence`）保证它
            # **不会因为这个 0 被剔除**。两处一起看才是完整的修法。
            "support": 0, "weight": 0.0}


class ImportTooLargeError(ValueError):
    """★ E-10（评审 P1-4）· 导入条数越界。调用方回 `422 EXPERIENCE_IMPORT_TOO_LARGE`。"""

    def __init__(self, count: int, limit: int) -> None:
        super().__init__(f"导入的经验条数 {count} 超过上限 {limit}")
        self.count = count
        self.limit = limit


async def import_graph(session: AsyncSession, payload: dict, *, mode: str = "merge",
                       max_nodes: int = 2000) -> dict:
    """导入 JSON（`mode="merge"` 合并 / `"replace"` 先清手工与导入节点再合并）。

    **只导入经验节点，不导入边与事件**：边是物化派生物（导进来就是别人的库的派生物，与本地事件
    不一致），事件带着别人的成本口径（I-7 一旦混用，整张表偏移一个往返成本）。这是一条有意的
    收窄，回执如实告知被拒条数。

    **★ E-10（评审 P1-4）· 两条防线，缺一即是一个不报错的半成品**：
      ① **体量闸**：R-8 列了六条防线却漏了体量——它是一次性 JSON body（导出是 NDJSON 流，导入
         不是），`nodes` 条数此前无上限。越界 ⇒ 抛 `ImportTooLargeError`，**一条都不写**，
         **绝不静默截断**。
      ② **`replace` 必须原子**：此前是「先逐条 `delete` 再逐条 `create`」，各自 `commit`；中途
         失败留下一个「删了一半」的经验库，而用户看到的只是「导入失败」。本轮整段包在**一个
         事务**里，异常即整体回滚（用例 A-49）。
      ③ **体内重复也要夹紧**：`key` 是 UNIQUE 列，而两份导出拼在一起必然出现同一条经验两次。
         按 key 去重（后来者覆盖）并在回执里如实报 `deduped`，绝不静默合并、更不让一次唯一
         约束冲突把整批回滚成一句「导入失败」。

    Args:
        session: DB 会话。
        payload: 不可信的导入体。
        mode: `merge` | `replace`。
        max_nodes: 条数上限（`ai_trade_experience_import_max_nodes`）。

    Returns:
        `{"imported": {...}, "rejected": {...}, "deduped": int}`。

    Raises:
        ImportTooLargeError: `nodes` 条数越界。
    """
    raw_nodes = list(payload.get("nodes") or [])
    if len(raw_nodes) > max(1, int(max_nodes)):
        raise ImportTooLargeError(len(raw_nodes), int(max_nodes))
    rejected = {"nodes": 0,
                "edges": len(payload.get("edges") or []),
                "events": len(payload.get("events") or [])}
    # **按 key 去重，且后来者覆盖**（③ 见 docstring）。`AiExperienceNode.key` 是 UNIQUE，而
    # `manual_key` 只认「陈述 + 语境」——两份导出拼在一起、或用户手改文件时重复一条，是一个
    # 完全正常的输入。不去重就是在 `session.add` 里撞唯一约束 ⇒ 整批回滚 ⇒ 用户看到的是
    # 「导入失败」而不是「其中两条是重复的」。不可信输入的夹紧必须包含它自己的重复。
    unique: dict[str, dict] = {}
    for raw in raw_nodes:
        clean = sanitize_lesson(raw, source="imported")
        if clean is None:
            rejected["nodes"] += 1
            continue
        unique[clean["key"]] = clean
    cleaned = list(unique.values())
    deduped = len(raw_nodes) - rejected["nodes"] - len(cleaned)
    imported = await _write_import(session, cleaned, mode=mode)
    return {"imported": {"nodes": imported, "edges": 0, "events": 0},
            "rejected": rejected, "deduped": deduped}


async def _write_import(session: AsyncSession, cleaned: list[dict], *, mode: str) -> int:
    """把已夹紧的经验写进库；`replace` 模式的删与写在**同一个事务**内（★ E-10）。

    数据层的 `delete_node` / `create_node` 各自 `commit`，故本函数不能复用它们来做原子替换——
    这是全文唯一一处编排层直接落 SQL 的地方，理由是「原子性只能在这一层表达」，且它**不碰**
    `upsert_lesson_nodes` 守着的那六个字段（手工 / 导入节点本就没有统计物化路径）。
    """
    from sqlalchemy import delete, select

    from ...db.models import AiExperienceEdge, AiExperienceNode

    now = int(time.time())
    try:
        if mode == "replace":
            rows = (await session.execute(select(AiExperienceNode).where(
                AiExperienceNode.kind == "lesson",
                AiExperienceNode.source.in_(("manual", "imported"))))).scalars().all()
            for row in rows:
                await session.execute(delete(AiExperienceEdge).where(
                    (AiExperienceEdge.src_id == row.id) | (AiExperienceEdge.dst_id == row.id)))
                await session.delete(row)
            await session.flush()
        existing = {row.key: row for row in (await session.execute(select(AiExperienceNode).where(
            AiExperienceNode.key.in_([c["key"] for c in cleaned] or [""])))).scalars().all()}
        for clean in cleaned:
            row = existing.get(clean["key"])
            if row is not None:
                row.label = clean["label"] or row.label
                row.detail = clean["detail"] or row.detail
                row.signature_json = json.dumps(clean["signature"], ensure_ascii=False)
                row.updated_ts = now
                continue
            session.add(AiExperienceNode(
                kind="lesson", key=clean["key"], label=clean["label"], detail=clean["detail"],
                source=clean["source"], scope=clean["scope"], tier=clean["tier"],
                support=int(clean["support"]), weight=float(clean["weight"]),
                stats_json=json.dumps(clean["stats"], ensure_ascii=False),
                signature_json=json.dumps(clean["signature"], ensure_ascii=False),
                first_ts=now, last_seen_ts=now, created_ts=now, updated_ts=now))
        await session.commit()
    except Exception:
        await session.rollback()      # ★ E-10 ②：绝不留下一个「删了一半」的经验库
        raise
    return len(cleaned)
