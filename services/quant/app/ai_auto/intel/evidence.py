"""采集 / 核查轮的证据装配：抓取配置、正文缓存往返、引文落库的 payload 组装。

**本模块是从 `service.py` 整段搬过来的，零语义变化**：闸⑦ 与抓取接线落地后 `service.py` 实测
912 行，越过 CLAUDE.md 的 800 行阈值，而 CLAUDE.md 对越线的处置是「超了就再下沉一层」而不是
放宽阈值（与 `cleanup.py` 同源）。

依赖方向是**单向**的：`evidence` 依赖 `corpus` / `reader` / `repo`，**不依赖 `service`**——
`deps` 以鸭子类型消费（只读 `settings` / `sessionmaker` / `now` / `reader` / `limits`），
否则两者会成环。

两条本模块独有的纪律：

1. **正文缓存写坏绝不阻断轮次**（不变式 I-2）。抓取是加法：抓不到就诚实回落 snippet。
2. **日志只记 instId 与 domain**，不记完整 URL（可能带 tracking id）、不记正文。
"""
from __future__ import annotations

import asyncio
import logging

from ...db import repositories as repo
from . import corpus as corpus_mod
from . import reader as reader_mod

logger = logging.getLogger(__name__)


def reader_config(settings) -> reader_mod.ReaderConfig:
    """`Settings` → `ReaderConfig`（**唯一**的抓取配置读点）。

    `proxy` 复用进程代理（检索适配器已经在用同一个来源），故本轮**不新增任何代理配置项**。
    """
    return reader_mod.ReaderConfig(
        enabled=bool(getattr(settings, "ai_trade_intel_reader_enabled", True)),
        top_k=int(getattr(settings, "ai_trade_intel_reader_top_k", 6)),
        timeout_s=float(getattr(settings, "ai_trade_intel_reader_timeout_s", 8.0)),
        round_deadline_s=float(
            getattr(settings, "ai_trade_intel_reader_round_deadline_s", 25.0)),
        concurrency=int(getattr(settings, "ai_trade_intel_reader_concurrency", 4)),
        max_bytes=int(getattr(settings, "ai_trade_intel_reader_max_bytes", 1_500_000)),
        max_redirects=int(getattr(settings, "ai_trade_intel_reader_max_redirects", 3)),
        max_per_domain=int(getattr(settings, "ai_trade_intel_reader_max_per_domain", 2)),
        body_max_chars=int(getattr(settings, "ai_trade_intel_reader_body_max_chars", 20_000)),
        proxy=getattr(settings, "proxy_url", None))


def _cache_windows(settings) -> tuple[float, float]:
    """``(正缓存窗, 负缓存窗)``。

    负缓存窗是**派生量** `min(6h, 正窗/12)`，**不是配置项**：少一个键就少一行必须双向对齐的
    门禁，也少一个「两个键各调一半」的坏状态。
    """
    ttl_s = float(getattr(settings, "ai_trade_intel_reader_cache_ttl_h", 72.0)) * 3600.0
    return ttl_s, min(6.0 * 3600.0, ttl_s / 12.0)


def _document_row(url: str, key: str, result, *, now: int) -> dict:
    """`ReaderResult` → `upsert_documents` 的 payload（**失败结果也入库**：负缓存也是缓存）。"""
    return {"urlHash": key, "url": url, "finalUrl": result.final_url, "domain": result.domain,
            "title": result.title, "body": result.body, "bodyChars": result.body_chars,
            "byteLen": result.byte_len, "status": result.status,
            "httpStatus": result.http_status, "fetchedTs": now,
            "truncated": result.truncated}


async def attach_document_bodies(deps, ctx, built):
    """采集 / 核查两条车道共用的 ③·5 + ③·6：抓正文并挂进语料。**绝不抛、绝不阻断轮次**。

    三个**毫秒级**短会话（缓存查询 / 正文回写各一个，都不跨任何网络 `await`，不变式 I-4）：
    步骤 ① 结束时我们还不知道会检索到哪些 URL（查询在 ② 才发），所以缓存查询只能排在
    `corpus.build` 之后；而抓到的正文必须在 ④ 之前落盘，否则一次在 LLM 阶段失败的轮会把已经
    花掉的抓取成本一起丢掉。

    Args:
        deps: 依赖聚合（**鸭子类型**消费，只读 `settings` / `sessionmaker` / `now` / `reader` /
            `limits`）。刻意不标注 `IntelDeps`——本模块不依赖 `service`，标注它会成环。
        ctx: 本轮上下文（`render_block` 渲染「已在库」清单需要它）。
        built: `corpus.build` 产出的、只有 snippet 的那一份语料。

    Returns:
        ``(新语料, {"readerAttempt": n, "readerOk": n})``。抓不到任何正文 ⇒ **原样返回入参**，
        语料块因此与本轮合入前**逐字节相同**（G-2 的冻结点）。
    """
    cfg = deps.reader
    counts = {"readerAttempt": 0, "readerOk": 0}
    if cfg is None or not getattr(cfg, "enabled", False) or not built.docs:
        return built, counts
    targets = reader_mod.select_targets([doc.url for doc in built.docs], cfg=cfg)
    if not targets:
        return built, counts
    keys = {url: corpus_mod.document_key(url) for url in targets}
    ttl_s, negative_ttl_s = _cache_windows(deps.settings)
    async with deps.sessionmaker()() as db:          # ③·5a 缓存查询（只读短会话）
        cached = await repo.get_cached_documents(db, list(keys.values()), now=deps.now(),
                                                 ttl_s=ttl_s, negative_ttl_s=negative_ttl_s)
    bodies: dict[str, str] = {}
    pending: list[str] = []
    for url in targets:
        hit = cached.get(keys[url])
        if hit is None:
            pending.append(url)
            continue
        counts["readerAttempt"] += 1
        if hit.get("status") == "ok" and hit.get("body"):
            bodies[url] = str(hit["body"])
            counts["readerOk"] += 1
    results = await reader_mod.fetch_many(pending, cfg=cfg) if pending else {}
    now_wall = int(deps.now())
    rows = [_document_row(url, keys[url], result, now=now_wall)
            for url, result in results.items()]
    for url, result in results.items():
        counts["readerAttempt"] += 1
        if result.ok:
            bodies[url] = result.body
            counts["readerOk"] += 1
    if rows:
        try:
            async with deps.sessionmaker()() as db:  # ③·5c 正文回写（写短会话）
                await repo.upsert_documents(db, rows, commit=True)
        except asyncio.CancelledError:
            raise
        except Exception as exc:      # noqa: BLE001 —— 缓存写坏绝不阻断轮次（不变式 I-2）
            logger.warning("ai-trade intel document cache write failed for %s: %s",
                           inst_hint(ctx), exc)
    if not bodies:
        return built, counts
    return corpus_mod.attach_bodies(built, bodies, ctx,
                                    per_doc_chars=int(deps.limits.body_per_doc_chars)), counts


def inst_hint(ctx) -> str:
    """日志用的标的标识（**只记 instId 与 domain，绝不记完整 URL 或正文**）。"""
    return str(getattr(ctx, "inst_id", "") or "?")


def quote_row(inst_id: str, item_id: int, round_id: int, quote, *, role: str,
              now: int) -> dict:
    """`VerifiedQuote` → `write_intel_quotes` 的 payload。"""
    return {"itemId": int(item_id), "instId": inst_id, "roundId": int(round_id), "role": role,
            "ref": quote.ref, "text": quote.text, "sourceField": quote.source_field,
            "docUrl": quote.doc_url, "docDomain": quote.doc_domain,
            "docPublishedTs": quote.doc_published_ts, "charStart": quote.char_start,
            "hasImperative": quote.has_imperative, "verifiedTs": now}


def plan_quote_writes(inst_id: str, round_id: int, admitted, item_ids: list[int], *, now: int):
    """把 `AdmitResult.quotes` 解析成「引文行 + 三列状态」（evidence-grounding §3.3.1）。

    三条落库规则：`accepted` 按 `upsert_intel_items` 的**按入参顺序**返回 id 落；`merges` 按
    `merge.item_id` 落并**累加** `quote_count`；`over_cap` / 同轮同题在 `gate.admit` 里就已经
    被丢弃并计 `quote_orphan_dropped`，到不了这里。

    Returns:
        ``(引文行, {item_id: (quoted, quote_count, quote_state)})``。状态表覆盖**全部**
        accepted + merges，包括零引文的那些——见 `write_intel_quote_state` 的 docstring。
    """
    rows: list[dict] = []
    states: dict[int, tuple] = {}
    for item, item_id in zip(admitted.accepted, item_ids):
        found = admitted.quotes.get(item.dedup_key, ())
        rows.extend(quote_row(inst_id, item_id, round_id, q, role="admit", now=now)
                    for q in found)
        states[int(item_id)] = (bool(found), len(found), item.quote_state)
    for merge in admitted.merges:
        found = admitted.quotes.get(merge.dedup_key, ())
        rows.extend(quote_row(inst_id, merge.item_id, round_id, q, role="admit", now=now)
                    for q in found)
        if found:
            states[int(merge.item_id)] = (True, int(merge.prior_quote_count) + len(found), "")
        else:
            # 没有新引文 ⇒ **原样写回**旧的三列。重算会把一条本来有证据的老情报说成没有证据。
            states[int(merge.item_id)] = (bool(merge.prior_quoted),
                                          int(merge.prior_quote_count),
                                          str(merge.prior_quote_state))
    return rows, states
