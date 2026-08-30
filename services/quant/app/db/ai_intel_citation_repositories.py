"""情报引用明细、影响力聚合、信源聚合与核查判定的数据层（ai-trade-intel-closed-loop §5.5）。

`ai_intel_repositories.py` 实读 610 行，再加这三组查询必然越 800 线；本模块的**存在本身**就是
「不让它越线」的处置（§十二 已登记）。公开函数经 `db/repositories.py` re-export，调用点仍写
`repo.X`（E-9 的既有姿势，导入面单一）。

三条贯穿本模块的纪律：

1. **统计列一律 `UPDATE … SET x = x + :n`，绝不「读-改-写」。** 情报轮与决策轮跑在不同的 DB
   会话里，读改写会丢更新，而丢更新不会报错。
2. **幂等**。`promote_..._to_entry` 只更新 `role="hold"` 的行，`settle_...` 只更新
   `settled=0 且 role in (entry,exit)` 的行——部分成交 / 重复平仓事件重复调用不会二次累加。
3. **`void` 不进四个结算统计列**（§3.2.2）。「结算尝试确实发生过、净额确实不可得」写
   `settled=True, outcome="void", net_pnl_usd=NULL`，但 `settled_count` / `win_count` /
   `net_pnl_usd` 一个字节不动——把一次失败伪装成一次平局比缺一个数更坏。
"""
from __future__ import annotations

import json
import time

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from .models import AiIntelCitation, AiIntelItem

# 影响力页签的榜单默认长度上限（端点侧另有 `limit ≤ 50` 的夹紧）。
_IMPACT_TOP_N = 10
_IMPACT_WORST_N = 5


def intel_citation_out(row: AiIntelCitation) -> dict:
    """一条引用明细 → camelCase wire（**唯一**装配点；前端 `IntelCitationRow` 与它逐键同构）。"""
    return {
        "id": row.id,
        "itemId": int(row.item_id or 0),
        "instId": row.inst_id or "",
        "runId": int(row.run_id or 0),
        "roundNo": int(row.round_no or 0),
        "citedTs": int(row.cited_ts or 0),
        "refToken": row.ref_token or "",
        "clOrdId": row.cl_ord_id or "",
        "role": row.role or "hold",
        "proposedAction": row.proposed_action or "",
        "finalAction": row.final_action or "",
        "traded": bool(row.traded),
        "positionSeq": int(row.position_seq or 0),
        "settled": bool(row.settled),
        "settledTs": int(row.settled_ts or 0),
        # **可空**：`None` = 结算不可得（`outcome="void"`）。绝不用 0.0 冒充「没结算」。
        "netPnlUsd": (None if row.net_pnl_usd is None else round(float(row.net_pnl_usd), 6)),
        "holdS": int(row.hold_s or 0),
        "exitSource": row.exit_source or "",
        "outcome": row.outcome or "",
        "createdTs": int(row.created_ts or 0),
    }


# ---------------- 写：引用落库与结算 ----------------
async def append_intel_citations(session: AsyncSession, rows: list[dict], *,
                                 commit: bool = True) -> list[int]:
    """批量 INSERT 引用行，并在**同一事务**内累加三个条目统计列。

    Args:
        rows: 形如 ``{"itemId", "instId", "runId", "roundNo", "citedTs", "refToken",
            "clOrdId", "role", "proposedAction", "finalAction", "traded", "positionSeq"}``。
        commit: 由编排层统一提交时传 False。

    Returns:
        新写入的引用行 id 列表（顺序与入参一致）。
    """
    if not rows:
        return []
    now = int(time.time())
    ids: list[int] = []
    per_item: dict[int, dict[str, int]] = {}
    for payload in rows:
        item_id = int(payload.get("itemId") or 0)
        role = str(payload.get("role") or "hold")
        cited_ts = int(payload.get("citedTs") or now)
        row = AiIntelCitation(
            item_id=item_id, inst_id=str(payload.get("instId") or ""),
            run_id=int(payload.get("runId") or 0),
            round_no=int(payload.get("roundNo") or 0), cited_ts=cited_ts,
            ref_token=str(payload.get("refToken") or "")[:4],
            cl_ord_id=str(payload.get("clOrdId") or "")[:64], role=role,
            proposed_action=str(payload.get("proposedAction") or "")[:16],
            final_action=str(payload.get("finalAction") or "")[:16],
            traded=bool(payload.get("traded")),
            position_seq=int(payload.get("positionSeq") or 0), created_ts=now)
        session.add(row)
        await session.flush()
        ids.append(int(row.id))
        bucket = per_item.setdefault(item_id, {"cite": 0, "entry": 0, "ts": 0})
        bucket["cite"] += 1
        bucket["entry"] += 1 if role == "entry" else 0
        bucket["ts"] = max(bucket["ts"], cited_ts)
    for item_id, bucket in per_item.items():
        await session.execute(
            update(AiIntelItem).where(AiIntelItem.id == item_id).values(
                cite_count=AiIntelItem.cite_count + bucket["cite"],
                cite_entry_count=AiIntelItem.cite_entry_count + bucket["entry"],
                last_cited_ts=func.max(AiIntelItem.last_cited_ts, bucket["ts"])))
    if commit:
        await session.commit()
    return ids


async def promote_intel_citations_to_entry(session: AsyncSession, *, run_id: int,
                                           cl_ord_id: str, position_seq: int,
                                           commit: bool = True) -> int:
    """把一批已落库的引用行升成 ``role="entry"`` 并绑定持仓周期（**P0-1 的落库侧**）。

    限价开仓在成交轮才知道 `seq`，而那时引用行早已 INSERT 过（下单轮的 `persist_pending` 跑过了）。
    只更新 ``role="hold"`` 的行 ⇒ **幂等**：部分成交重复调用不会二次累加 `cite_entry_count`。

    Args:
        run_id / cl_ord_id: 下单那一刻冻结的委托号 + 会话号（跨轮认领的键）。
        position_seq: 成交时的持仓周期 epoch。

    Returns:
        实际升级的行数（0 = 这张挂单没有任何引用行，正常情形，不是错误）。
    """
    if not cl_ord_id:
        return 0
    rows = (await session.execute(
        select(AiIntelCitation.id, AiIntelCitation.item_id).where(
            AiIntelCitation.run_id == int(run_id),
            AiIntelCitation.cl_ord_id == str(cl_ord_id),
            AiIntelCitation.role == "hold"))).all()
    if not rows:
        return 0
    ids = [int(row[0]) for row in rows]
    await session.execute(update(AiIntelCitation).where(AiIntelCitation.id.in_(ids))
                          .values(role="entry", position_seq=int(position_seq)))
    per_item: dict[int, int] = {}
    for _cid, item_id in rows:
        per_item[int(item_id)] = per_item.get(int(item_id), 0) + 1
    for item_id, n in per_item.items():
        await session.execute(
            update(AiIntelItem).where(AiIntelItem.id == item_id)
            .values(cite_entry_count=AiIntelItem.cite_entry_count + n))
    if commit:
        await session.commit()
    return len(ids)


async def settle_intel_citations(session: AsyncSession, *, run_id: int, position_seq: int,
                                 net_pnl_usd: float | None, hold_s: int, exit_source: str,
                                 outcome: str, now: int, commit: bool = True) -> int:
    """结算一个持仓周期内全部 entry / exit 引用行，并累加条目的四个结算统计列。

    ⚠️ **`position_seq` 的实参只能是 `trip.seq`**（评审 P0-2）：`_dispatch` 的 close 分支第一件事
    就是 `self._position_seq += 1`，等结算跑到时那个计数器已经是下一个 epoch；取错的症状是
    UPDATE 恒命中零行、`settled_count` 与 `net_pnl_usd` 恒 0，而**这件事不会报任何错**。

    只更新 ``settled=0 且 role in (entry, exit)`` 的行 ⇒ **幂等**（重复的平仓事件不二次累加）。
    ``outcome == "void"`` ⇒ 写 `settled=True` 与 `net_pnl_usd=NULL`，但四个统计列**一个字节不动**。

    Returns:
        实际结算的行数。
    """
    rows = (await session.execute(
        select(AiIntelCitation.id, AiIntelCitation.item_id).where(
            AiIntelCitation.run_id == int(run_id),
            AiIntelCitation.position_seq == int(position_seq),
            AiIntelCitation.settled.is_(False),
            AiIntelCitation.role.in_(("entry", "exit"))))).all()
    if not rows:
        return 0
    ids = [int(row[0]) for row in rows]
    void = outcome == "void"
    await session.execute(update(AiIntelCitation).where(AiIntelCitation.id.in_(ids)).values(
        settled=True, settled_ts=int(now), outcome=outcome, hold_s=int(hold_s or 0),
        exit_source=str(exit_source or "")[:24],
        net_pnl_usd=(None if void or net_pnl_usd is None else float(net_pnl_usd))))
    if not void:
        per_item: dict[int, int] = {}
        for _cid, item_id in rows:
            per_item[int(item_id)] = per_item.get(int(item_id), 0) + 1
        share = float(net_pnl_usd or 0.0)
        for item_id, n in per_item.items():
            await session.execute(
                update(AiIntelItem).where(AiIntelItem.id == item_id).values(
                    settled_count=AiIntelItem.settled_count + n,
                    win_count=AiIntelItem.win_count + (n if outcome == "win" else 0),
                    # ★ 净额**每条情报每笔往返只加一次**（不乘行数）：同一条情报可能既在开仓轮
                    # 被引用、又在平仓轮被引用，按行数乘会让这一笔往返的净额在它身上算两遍。
                    # G-4 写死的是「净额增量**等于** `trip.net_pnl_usd`」。计数列则仍按行走
                    # （`settled_count` 是「已结算的引用条数」，不是「已结算的往返数」）。
                    net_pnl_usd=AiIntelItem.net_pnl_usd + share))
    if commit:
        await session.commit()
    return len(ids)


async def prune_intel_citations(session: AsyncSession, *, before_ts: int,
                                commit: bool = True) -> int:
    """裁剪过保留期的引用明细（G-13）。**只删明细、不回滚统计列**。

    统计列是已发布的读数；为了省几行明细去追溯改写它们，会让「引用 12 次」这个数在用户眼里
    无缘无故变小，而那比多留几行明细坏得多。
    """
    rows = (await session.execute(
        select(AiIntelCitation.id).where(AiIntelCitation.cited_ts < int(before_ts)))).all()
    ids = [int(row[0]) for row in rows]
    if not ids:
        return 0
    await session.execute(delete(AiIntelCitation).where(AiIntelCitation.id.in_(ids)))
    if commit:
        await session.commit()
    return len(ids)


# ---------------- 读：引用明细 / 影响力 / 信源 ----------------
async def list_intel_citations(session: AsyncSession, *, inst_id: str,
                               item_id: int | None = None, limit: int = 50,
                               before: int | None = None) -> tuple[list[dict], bool]:
    """引用明细的游标分页（按 `id` 降序）。

    Returns:
        ``(rows, has_more)``。游标是 `id`，不是时间戳——同一秒里的多条引用用时间戳翻页会漏行。
    """
    stmt = select(AiIntelCitation).where(AiIntelCitation.inst_id == inst_id)
    if item_id:
        stmt = stmt.where(AiIntelCitation.item_id == int(item_id))
    if before:
        stmt = stmt.where(AiIntelCitation.id < int(before))
    capped = max(1, int(limit))
    rows = (await session.execute(
        stmt.order_by(AiIntelCitation.id.desc()).limit(capped + 1))).scalars().all()
    return [intel_citation_out(row) for row in rows[:capped]], len(rows) > capped


async def read_intel_impact(session: AsyncSession, inst_id: str, *, limit: int = 10) -> dict:
    """影响力页签的全部读数（三张读数卡 + 贡献榜 + 拖累榜 + 未引用计数）。

    **一次 SQL 聚合**（不在 Python 里遍历全库）：这个端点会被弹窗按秒级轮询。

    Returns:
        ``{"totals": {...}, "top": [...], "worst": [...], "neverCited": int}``。
        `totals.netPnlUsd` 是**关联**净额（共现统计，不是因果归因，R-1）。
    """
    totals_row = (await session.execute(
        select(func.coalesce(func.sum(AiIntelItem.cite_count), 0),
               func.coalesce(func.sum(AiIntelItem.cite_entry_count), 0),
               func.coalesce(func.sum(AiIntelItem.settled_count), 0),
               func.coalesce(func.sum(AiIntelItem.win_count), 0),
               func.coalesce(func.sum(AiIntelItem.net_pnl_usd), 0.0))
        .where(AiIntelItem.inst_id == inst_id))).one()
    never = int((await session.execute(
        select(func.count()).select_from(AiIntelItem).where(
            AiIntelItem.inst_id == inst_id, AiIntelItem.status == "active",
            AiIntelItem.cite_count == 0))).scalar() or 0)
    top_n = max(1, min(int(limit), _IMPACT_TOP_N * 5))
    top = (await session.execute(
        select(AiIntelItem).where(AiIntelItem.inst_id == inst_id,
                                  AiIntelItem.cite_count > 0)
        .order_by(AiIntelItem.net_pnl_usd.desc(), AiIntelItem.cite_entry_count.desc())
        .limit(top_n))).scalars().all()
    worst = (await session.execute(
        select(AiIntelItem).where(AiIntelItem.inst_id == inst_id,
                                  AiIntelItem.net_pnl_usd < 0.0)
        .order_by(AiIntelItem.net_pnl_usd.asc())
        .limit(_IMPACT_WORST_N))).scalars().all()
    from .ai_intel_repositories import intel_item_out   # 同层复用，避免第二份 wire 口径

    return {
        "instId": inst_id,
        "totals": {
            "citeCount": int(totals_row[0] or 0),
            "citeEntryCount": int(totals_row[1] or 0),
            "settledCount": int(totals_row[2] or 0),
            "winCount": int(totals_row[3] or 0),
            "netPnlUsd": round(float(totals_row[4] or 0.0), 6),
        },
        "top": [intel_item_out(row) for row in top],
        "worst": [intel_item_out(row) for row in worst],
        "neverCited": never,
    }


async def clear_intel_verdict(session: AsyncSession, item_id: int, *,
                              commit: bool = True) -> dict | None:
    """「我不同意」：清 `verdict` / `verdict_ts` / `verdict_note`，置 `user_edited=True`。

    ⚠️ **`verify_count` 保持不变**（评审 P0-4）。清了它，这条目对 `plan_verification` 的 P2
    判据（`verdict == "" 且 verify_count == 0`）立刻重新成立 ⇒ 下一轮被自动判回 `refuted`
    ——用户的显式意愿被系统静默撤销，承诺三当场失效。`user_edited` 在这里兼任**判定锁**：
    三条候选判据全部带 `not user_edited`，故这条目此后永久移出自动候选池（G-17）。

    想让它重新接受核查？路径是明确的一次 `POST /intel/items/{itemId}/verify`（§4.1 #21）。

    Returns:
        更新后的条目 wire；条目不存在回 ``None``。
    """
    from .ai_intel_repositories import intel_item_out

    row = await session.get(AiIntelItem, int(item_id))
    if row is None:
        return None
    row.verdict = ""
    row.verdict_ts = 0
    row.verdict_note = ""
    row.user_edited = True
    row.updated_ts = int(time.time())
    if commit:
        await session.commit()
    return intel_item_out(row)


async def count_intel_verdicts(session: AsyncSession, inst_id: str) -> dict:
    """active 条目的判定分布（`AiTradeIntelSummary.verify` 的分子）。

    `pending` = 尚未核查过（``verdict == ""``），与 `unresolved`（核查过但没查出来）**分开计**：
    写成同一个数就等于把一次没跑过的核查伪装成一次「查不到」。
    """
    rows = (await session.execute(
        select(AiIntelItem.verdict, func.count()).where(
            AiIntelItem.inst_id == inst_id, AiIntelItem.status == "active")
        .group_by(AiIntelItem.verdict))).all()
    counts = {"confirmed": 0, "refuted": 0, "unresolved": 0, "pending": 0}
    for verdict, n in rows:
        key = str(verdict or "") or "pending"
        if key in counts:
            counts[key] += int(n)
    return counts


async def list_intel_source_rows(session: AsyncSession, inst_id: str) -> list[dict]:
    """当前库存的域名聚合（域名 / 条数 / 最近一次 / 当前 block|trust 状态与它的作用域）。

    `sources_json` 在 Python 侧解析（SQLite 无 JSON 聚合保证），**只扫 active + archived**——
    扫全表会把 superseded 的重复来源算成第二次出现。

    ⚠️ 每行必须带上 `stateInstId`（生效的那条取舍记录的作用域，``""`` = 全局）。取舍状态是
    「全局 + 本标的」并集算出来的，而 `clear_intel_feedback` 按 ``(key, inst_id)`` **精确**删除
    ——不下发作用域，界面上那颗「撤销」对一条全局记录就是一次什么都不会发生的点击，且不报任何错。
    两条记录都在时报**更具体**的那一条：撤销先收窄再收窄，每一次点击都真的删掉了一行。
    """
    from .ai_intel_repositories import list_intel_feedback   # 同层复用，避免第二份取舍口径

    rows = (await session.execute(
        select(AiIntelItem.sources_json, AiIntelItem.updated_ts).where(
            AiIntelItem.inst_id == inst_id,
            AiIntelItem.status.in_(("active", "archived"))))).all()
    # 后写的（inst_id 非空 = 更具体）覆盖先写的：`list_intel_feedback` 的排序不保证作用域顺序，
    # 故显式按「全局在前、标的在后」重排一次再折成一张表。
    verdict_by_domain: dict[str, tuple[str, str]] = {}
    for feedback in sorted(await list_intel_feedback(session, inst_id),
                           key=lambda row: bool(row["instId"])):
        verdict_by_domain[feedback["key"]] = (feedback["action"], feedback["instId"])
    agg: dict[str, dict] = {}
    for raw, updated_ts in rows:
        try:
            sources = json.loads(raw or "[]")
        except (json.JSONDecodeError, TypeError, ValueError):
            continue
        seen: set[str] = set()
        for source in sources if isinstance(sources, list) else []:
            domain = str((source or {}).get("domain") or "") if isinstance(source, dict) else ""
            if not domain or domain in seen:
                continue
            seen.add(domain)
            bucket = agg.setdefault(domain, {"domain": domain, "itemCount": 0, "lastTs": 0})
            bucket["itemCount"] += 1
            bucket["lastTs"] = max(bucket["lastTs"], int(updated_ts or 0))
    out = []
    for domain, bucket in agg.items():
        action, scope_inst_id = verdict_by_domain.get(domain, ("", ""))
        state = {"block": "blocked", "trust": "trusted"}.get(action, "")
        out.append({**bucket, "state": state,
                    "stateInstId": scope_inst_id if state else ""})
    out.sort(key=lambda row: (-row["itemCount"], row["domain"]))
    return out


async def count_items_all_sources_blocked(session: AsyncSession, inst_id: str,
                                          blocked) -> int:
    """「全部来源都属于被拉黑域名」的 active 条目数（`POST /intel/feedback/preview` 的分子）。

    **纯读**。确认框必须显示受影响条数——一个说不清后果的破坏性操作按钮不该出现在这个产品里。
    受保护条目（`pinned` / `user_edited`）**不计入**：它们本就跳过全部自动清理路径。
    """
    domains = {str(d).strip().lower() for d in (blocked or []) if str(d).strip()}
    if not domains:
        return 0
    stmt = select(AiIntelItem.sources_json, AiIntelItem.pinned, AiIntelItem.user_edited).where(
        AiIntelItem.status == "active")
    if inst_id:
        stmt = stmt.where(AiIntelItem.inst_id == inst_id)
    total = 0
    for raw, pinned, user_edited in (await session.execute(stmt)).all():
        if pinned or user_edited:
            continue
        try:
            sources = json.loads(raw or "[]")
        except (json.JSONDecodeError, TypeError, ValueError):
            continue
        item_domains = [str((s or {}).get("domain") or "")
                        for s in (sources if isinstance(sources, list) else [])
                        if isinstance(s, dict) and (s or {}).get("domain")]
        if item_domains and all(domain in domains for domain in item_domains):
            total += 1
    return total


async def sweep_blocked_source_items(session: AsyncSession, inst_id: str, blocked, *,
                                     now: int | None = None, commit: bool = True) -> int:
    """把「全部来源都被拉黑」的 active 条目归档，理由 ``source_blocked``（G-9②）。

    受保护条目跳过（与 TTL / 溢出的既有纪律一致）。**解除拉黑不自动复活**（G-9③）：归档是一次
    带理由的状态迁移，自动复活会让用户在两次点击之间看到条目数跳来跳去；恢复路径是「已归档」
    页签里那颗已经存在的恢复按钮。
    """
    from .ai_intel_repositories import archive_intel_items   # 同层复用软删口径

    domains = {str(d).strip().lower() for d in (blocked or []) if str(d).strip()}
    if not domains:
        return 0
    stmt = select(AiIntelItem.id, AiIntelItem.sources_json, AiIntelItem.pinned,
                  AiIntelItem.user_edited).where(AiIntelItem.status == "active")
    if inst_id:
        stmt = stmt.where(AiIntelItem.inst_id == inst_id)
    victims: list[int] = []
    for item_id, raw, pinned, user_edited in (await session.execute(stmt)).all():
        if pinned or user_edited:
            continue
        try:
            sources = json.loads(raw or "[]")
        except (json.JSONDecodeError, TypeError, ValueError):
            continue
        item_domains = [str((s or {}).get("domain") or "")
                        for s in (sources if isinstance(sources, list) else [])
                        if isinstance(s, dict) and (s or {}).get("domain")]
        if item_domains and all(domain in domains for domain in item_domains):
            victims.append(int(item_id))
    if not victims:
        return 0
    return await archive_intel_items(session, victims, reason="source_blocked", now=now,
                                     commit=commit)


# ---------------- 写：核查判定 ----------------
async def apply_intel_verdicts(session: AsyncSession, plans: list[dict], *, now: int,
                               commit: bool = True) -> dict:
    """执行核查轮**已过复核**的判定计划（`verdict` 的唯一写点）。

    Args:
        plans: 形如 ``{"itemId", "verdict", "reason", "confidence", "toTier", "expiresTs",
            "overwriteVerdict", "verifyCount"}``（`verify.VerdictPlan` 的 wire 形态）。
        now: 墙钟秒。

    Returns:
        判定直方图（实际写下去了几条）。

    Raises:
        ValueError: 写了 `toTier` 却没给 `expiresTs`——不变式 I-3 只能在构造期挡下来，运行期它是
            完全静默的（不重算的后果是整座多级库在超短 TTL 之后被整层扫空）。
    """
    counts: dict[str, int] = {}
    for plan in plans or []:
        row = await session.get(AiIntelItem, int(plan.get("itemId") or 0))
        if row is None:
            continue
        verdict = str(plan.get("verdict") or "")
        if plan.get("overwriteVerdict", True):
            row.verdict = verdict
        row.verdict_ts = int(now)
        row.verdict_note = str(plan.get("reason") or "")[:200]
        row.verify_count = int(plan.get("verifyCount") or (int(row.verify_count or 0) + 1))
        if plan.get("confidence") is not None:
            row.confidence = float(plan["confidence"])
        to_tier = plan.get("toTier")
        if to_tier:
            if plan.get("expiresTs") is None:
                raise ValueError("apply_intel_verdicts: 降层必须携带重算后的 expiresTs"
                                 "（不变式 I-3）")
            row.tier = str(to_tier)
            row.expires_ts = int(plan["expiresTs"])
            row.demote_count = int(row.demote_count or 0) + 1
        row.updated_ts = int(now)
        counts[verdict] = counts.get(verdict, 0) + 1
    if commit:
        await session.commit()
    return counts
