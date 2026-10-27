"""`audit-csv` 与 `audit-jsonl` 的流式生成器（ai-trade-audit-journal §2.5.1）。

三条硬约束：

1. **全部流式**。一次性拼进内存再序列化会在桌面端把整个进程顶起来（`experience/service.py`
   的 `export_stream` docstring 已记录在案，本轮沿用）。
2. **墙钟预算**（评审 RP0-2）。流式解决的是内存、解决不了墙钟，而这条链路上有一堵 300 秒的墙：
   `apps/web/next.config.mjs` 的 `experimental.proxyTimeout: 300000` 对每一个 `/api/py/*` 请求
   生效，而 `<a download>` 走的正是同一条 rewrite。本仓已为同一形状付过三次代价（三份 diagnoses
   在案），症状一律是传输层的 `socket hang up`，而**后端日志全绿**。故每写完一页检查一次
   `monotonic()`，超预算即**干净收尾**并给出 `nextBeforeId` ——绝不让代理去撕一条写到一半的流。
3. **完整性信封**。NDJSON **首尾各一行**：首行 `manifest`、末行 `eof`。首行信封能证明「我打算
   给你 N 条」，但证明不了流是否完整地走到了尾——被网络截断的 NDJSON 与完整的在语法上没有任何
   区别。**没有 eof 行的文件一律视为不完整。** CSV 没有 eof 行可写，故截断时追加一行
   `#TRUNCATED,<reason>,<nextBeforeId>`：Excel 会把它显示成一条普通行——那正是目的，
   一个静默截断的 CSV 是本节要消灭的东西。
"""
from __future__ import annotations

import json
import re
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime, timezone

from ...db import ai_journal_repositories as journal_repo
from . import linking

# UTF-8 BOM：前置后 Excel 才会按 UTF-8 解析中文表头（U+FEFF）。
BOM = "﻿"
# RFC4180 风格转义的判据。与 `apps/web/lib/export.ts` 的 `escapeField` **逐字同构**，
# 由共享黄金夹具 `docs/plans/ai-trade-audit-journal/fixtures/` 两侧各自断言（评审 RP1-9）。
_ESCAPE_RE = re.compile(r'[",\r\n]')
ROW_SEP = "\r\n"

# `audit-csv` 的 22 列（中文表头）。顺序是契约：离线脚本按列位取数。
CSV_COLUMNS = ("时间(UTC)", "时间(本地)", "分组", "运行ID", "标的", "周期", "轮次", "唤醒原因",
               "模型动作", "最终动作", "仓位比例", "置信度", "委托方式", "限价", "降级原因",
               "反思裁决", "是否成交", "成交价", "成交量", "手续费", "已实现盈亏", "决策理由")

TRUNCATED_MARKER = "#TRUNCATED"
NDJSON_MEDIA_TYPE = "application/x-ndjson"
CSV_MEDIA_TYPE = "text/csv; charset=utf-8"


def escape_field(value) -> str:
    """RFC4180 风格转义（`None` ⇒ 空串；含 `,` `"` `\\r` `\\n` 时双引号包裹、内部 `"` 转 `""`）。

    ⚠️ 本函数与 `apps/web/lib/export.ts` 的 `escapeField` 必须**逐字节同构**。两处实现无法在
    同一个进程里互测（后端跑 pytest、前端跑 vitest），故它们断言的是**同一组提交进仓库的夹具
    文件**：任一侧漂移都会在自己的 CI 上判红，而夹具本身的改动必然出现在 diff 里。
    """
    text = "" if value is None else str(value)
    return f'"{text.replace(chr(34), chr(34) * 2)}"' if _ESCAPE_RE.search(text) else text


def csv_line(values) -> str:
    """一行 CSV（不含行分隔符）。"""
    return ",".join(escape_field(v) for v in values)


@dataclass(frozen=True, slots=True)
class ExportBudget:
    """一次（分片）导出的三个终止条件。**先到者生效**。"""

    page_size: int = 500
    max_rows: int = 200000
    time_budget_s: float = 240.0

    def exhausted(self, started: float, emitted: int) -> str | None:
        """返回触顶的原因码；未触顶 ⇒ `None`。"""
        if self.max_rows and emitted >= self.max_rows:
            return "max_rows"
        if self.time_budget_s and (time.monotonic() - started) >= self.time_budget_s:
            return "time_budget"
        return None


def _iso_utc(ts: int) -> str:
    return datetime.fromtimestamp(int(ts or 0), tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _iso_local(ts: int) -> str:
    return datetime.fromtimestamp(int(ts or 0)).strftime("%Y-%m-%d %H:%M:%S")


def _csv_row(full: dict, link: dict) -> list:
    """一行扁平化的决策（22 列，顺序即 `CSV_COLUMNS`）。"""
    fill = link.get("fill") or {}
    trip = link.get("trip") or {}
    return [_iso_utc(full.get("ts")), _iso_local(full.get("wallTs") or full.get("ts")),
            full.get("groupId"), full.get("runId"), full.get("instId"), full.get("bar"),
            full.get("roundNo"), full.get("trigger"), full.get("action"),
            full.get("finalAction"), full.get("sizeFraction"), full.get("confidence"),
            full.get("ordType"), full.get("limitPx"), full.get("degradeReason"),
            full.get("reflectionVerdict"),
            "是" if full.get("executed") else "否",
            fill.get("price"), fill.get("qty"), fill.get("fee"), trip.get("pnl"),
            full.get("reason")]


async def _pages(filters: dict, budget: ExportBudget):
    """按 `beforeId` 游标逐页取数（**绝不用 OFFSET**）；每页 yield `(rows, fulls, links)`。"""
    from ...db.session import get_sessionmaker
    cursor = filters.get("beforeId")
    sessionmaker = get_sessionmaker()
    while True:
        page_filters = dict(filters)
        if cursor:
            page_filters["beforeId"] = cursor
        async with sessionmaker() as session:
            rows = await journal_repo.list_decision_rows(session, page_filters,
                                                         limit=budget.page_size)
            if not rows:
                return
            fulls = [journal_repo.decision_full(row) for row in rows]
            trades = await journal_repo.list_run_trades(
                session, sorted({row.run_id for row in rows}))
            profiles = await journal_repo.load_profiles(
                session, [row.profile_sha256 for row in rows])
        links = linking.link_decisions_to_fills(fulls, trades)
        yield rows, fulls, links, profiles
        cursor = rows[-1].id
        if len(rows) < budget.page_size:
            return


async def stream_audit_csv(filters: dict, budget: ExportBudget) -> AsyncIterator[str]:
    """`audit-csv`：BOM + 22 列中文表头 + 一轮一行；行分隔 `\\r\\n`。"""
    started = time.monotonic()
    yield BOM + csv_line(CSV_COLUMNS) + ROW_SEP
    emitted = 0
    reason: str | None = None
    next_before_id = None
    async for rows, fulls, links, _profiles in _pages(filters, budget):
        for row, full, link in zip(rows, fulls, links):
            yield csv_line(_csv_row(full, link)) + ROW_SEP
            emitted += 1
            next_before_id = row.id
        reason = budget.exhausted(started, emitted)
        if reason:
            break
    if reason:
        # 静默截断的 CSV 正是本节要消灭的东西：这一行会在 Excel 里显示成一条普通行。
        yield csv_line([TRUNCATED_MARKER, reason, next_before_id]) + ROW_SEP


async def stream_audit_jsonl(filters: dict, budget: ExportBudget,
                             meta: dict) -> AsyncIterator[str]:
    """`audit-jsonl`：manifest → (promptProfile | decision)* → event* → eof。

    档案行在**首次被引用时**就地发出（进程内按 sha 去重），而不是全部排在最前面：把它们排到
    最前面需要先做一次全量预扫描，而那次预扫描会把墙钟预算吃掉一半。每一行都自带 `kind`，
    导入方按 `kind` 分支即可，顺序不承载语义。

    成交 / 订单 / 往返结果**不单独出行**：它们已经在每条 `decision` 的 `outcome` 里，
    再发一遍就是同一事实的第二份记录——而「两份真值、其中一份悄悄过期」正是本设计从头到尾
    拒绝的那个形状。
    """
    started = time.monotonic()
    yield _ndjson({"kind": "manifest", "schema": 1, "format": "audit-jsonl",
                   "generatedTs": int(time.time()), **meta})
    emitted = 0
    reason: str | None = None
    next_before_id = None
    seen_profiles: set[str] = set()
    async for rows, fulls, links, profiles in _pages(filters, budget):
        for row, full, link in zip(rows, fulls, links):
            sha = full.get("profileSha256") or ""
            if sha and sha not in seen_profiles and sha in profiles:
                seen_profiles.add(sha)
                # `**profiles[sha]` 里也有一个 `kind`（`profile` / `summary`），
                # 故它必须排在前面——排在后面会把行类型 `promptProfile` 覆盖掉，
                # 而导入方正是按行类型分支的。
                yield _ndjson({**profiles[sha], "kind": "promptProfile",
                               "sha256": sha,
                               "profileKind": profiles[sha].get("kind")})
            yield _ndjson({"kind": "decision", **full,
                           "outcome": {"linkConfidence": link["linkConfidence"],
                                       "fill": link["fill"], "trip": link["trip"],
                                       "noFillReason": link["noFillReason"],
                                       "fillSource": link["fillSource"]}})
            emitted += 1
            next_before_id = row.id
        reason = budget.exhausted(started, emitted)
        if reason:
            break
    # 事件段与决策段**共享同一份墙钟预算**，故它被截断时 `eof` 必须跟着说实话——
    # 一个 `complete: true` 却少了一半事件行的文件，正是本节的完整性信封要消灭的东西。
    events_state = {"emitted": 0, "reason": None}
    if not reason:
        async for line in _stream_events(filters, budget, started, events_state):
            yield line
    truncated = reason or events_state["reason"]
    yield _ndjson({"kind": "eof", "emitted": emitted,
                   "eventsEmitted": events_state["emitted"],
                   "complete": truncated is None,
                   "truncatedReason": truncated or "none",
                   # 游标只对**决策段**有意义（事件段的续取由同一批过滤条件覆盖）。
                   "nextBeforeId": next_before_id if reason else None})


async def _stream_events(filters: dict, budget: ExportBudget, started: float,
                         state: dict) -> AsyncIterator[str]:
    """事件行（同 scope、同游标语义）。墙钟触顶即停——它与决策行**共享同一份预算**。

    `state` 是调用方给的可变回执：`{"emitted": N, "reason": None | "time_budget"}`。
    生成器没法用返回值把这个说出来，而它必须被说出来——`eof` 要照它决定 `complete`。
    """
    from ...db.session import get_sessionmaker
    sessionmaker = get_sessionmaker()
    cursor = None
    while True:
        # 只有墙钟闸对事件段有意义（行数闸算的是决策行）。
        if budget.time_budget_s and (time.monotonic() - started) >= budget.time_budget_s:
            state["reason"] = "time_budget"
            return
        page_filters = {k: filters.get(k) for k in ("groupId", "instId", "sinceTs", "untilTs")}
        if cursor:
            page_filters["beforeId"] = cursor
        async with sessionmaker() as session:
            rows = await journal_repo.list_event_rows(session, page_filters,
                                                      limit=budget.page_size)
        if not rows:
            return
        for row in rows:
            yield _ndjson({"kind": "event", **journal_repo.event_out(row)})
            state["emitted"] += 1
        cursor = rows[-1].id
        if len(rows) < budget.page_size:
            return


def _ndjson(obj: dict) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str) + "\n"
