"""情报检索层：`IntelSearchProvider` 协议 + tavily 适配器 + 超时/重试/熔断（§3.2）。

**检索留在后端**是本特性可信度的物理来源（§1.2 承诺一）：情报 Agent 从头到尾没有联网能力，
它只能看到后端替它检索回来、编号为 `[D1]…[Dn]` 的语料，提交条目时**只能引用编号**。模型能编造
文字，但编造不出一个后端语料里不存在的编号。

三个适配器：`tavily`（body 传 key 的 POST）、`brave`（`X-Subscription-Token` 头的 GET，
`freshness=pd|pw|pm`，日期是 RFC3339 的 `page_age`）、`serper`（`X-API-KEY` 头的 POST，
`tbs=qdr:d|w|m`，日期是 `"3 days ago"` 这样的**相对文案**）。协议层没有为它们改过一个字节
——`brave` / `serper` 是 intel-closed-loop 轴 D 补的，只多了一个共用重试骨架 `_HttpProvider`。

**仍是单活**：设置里选一个，不做多源扇出（closed-loop §十 非目标 3）。

密钥三条纪律（§3.2.3）：库内行优先 / 绝不进日志 / 绝不出接口。
"""
from __future__ import annotations

import asyncio
import logging
import random
import re
import time
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol

import httpx

logger = logging.getLogger(__name__)

# 可重试的 HTTP 状态（限流 / 网关瞬时故障）。4xx（除 429）**不重试**——那是配置错，重试只会
# 更快烧掉配额，与 `agent/llm/retry.py` 的判据同源。
RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})
_RETRYABLE_EXC = (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout,
                  httpx.RemoteProtocolError)
_JITTER_MS = 250
_BACKOFF_BASE_MS = 500
_BACKOFF_MAX_MS = 8000

TAVILY_ENDPOINT = "https://api.tavily.com/search"
BRAVE_ENDPOINT = "https://api.search.brave.com/res/v1/web/search"
SERPER_ENDPOINT = "https://google.serper.dev/search"
PROVIDER_SLUGS: tuple[str, ...] = ("none", "tavily", "brave", "serper")
# 曾经的 v1.1 待办（`brave` / `serper`）已由 ai-trade-intel-closed-loop 轴 D 兑现，故此表清空。
# **保留这个名字而不是删掉它**：`test_ai_intel_search_providers.py` 拿它断言「那条债确实还清了」，
# 而一个空表比一条 commit message 更难被下一个人读漏。下一次再登记待接入的 slug 时填回这里。
# （拼错的 provider 名由 `resolve_search_provider` 末尾那条 `logger.info` 兜底，与本表无关。）
PLANNED_SLUGS: tuple[str, ...] = ()
# 单次检索的条数硬上限（两家的 API 各自封顶 20；超出先夹紧而不是让对端 400）。
_PROVIDER_MAX_RESULTS = 20


class IntelSearchError(Exception):
    """检索失败。``message`` 在构造时即完成脱敏（key 恒不进日志、不进 DB、不出接口）。"""

    def __init__(self, code: str, message: str, *secrets: str):
        self.code = code
        self.message = _redact(message, *secrets)
        super().__init__(f"{code}: {self.message}")


def _redact(text: str, *secrets: str) -> str:
    """把出现在文本里的密钥替换成 ``***``。

    与 `okx/accounts.py:67::_redact(text, *secrets)` **同签名**的模块私有实现。刻意不跨包 import
    那个下划线私有名——一个下划线开头的名字被三个包共用，等于把它的签名变成公开契约却没有任何
    地方说明这件事（评审 P2-3）。
    """
    out = text or ""
    for secret in secrets:
        if secret and len(secret) >= 6:
            out = out.replace(secret, "***")
    return out


@dataclass(frozen=True)
class SearchDoc:
    """检索适配器归一化后的单篇文档。"""

    title: str
    url: str
    snippet: str
    publisher: str = ""
    published_ts: int = 0        # 0 = 未知
    score: float = 0.0


class IntelSearchProvider(Protocol):
    """检索适配器协议。"""

    slug: str

    async def search(self, query: str, *, limit: int, freshness_days: int) -> list[SearchDoc]:
        """执行一次检索。

        非 2xx / 解析失败 ⇒ 抛 `IntelSearchError(code, message)`，**绝不返回空列表冒充成功**：
        「一条都没搜到」与「检索源坏了」在界面上是两段不同的文案，混为一谈就等于用「暂无情报」
        掩盖一次故障。
        """
        ...


def _parse_rfc3339(raw) -> int:
    """RFC3339 / ISO8601 → 墙钟秒；**解析失败一律回 0**（宁可保守，不猜时间，R-11）。"""
    if not raw or not isinstance(raw, str):
        return 0
    text = raw.strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        for fmt in ("%Y-%m-%d", "%Y/%m/%d"):
            try:
                dt = datetime.strptime(text[:10], fmt)
                break
            except ValueError:
                continue
        else:
            return 0
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


class _Breaker:
    """检索源熔断器。

    **刻意不复用 `okx/rest_client.py` 的那一个**（形状照抄、状态不共享）：那个 breaker 与
    `link_state` 注册表耦合，是 OKX 链路的专属状态；把第三方检索源的失败记进交易所链路健康，
    会让「行情是否正常」这件事被一个与它无关的 API key 过期污染。
    """

    def __init__(self, *, threshold: int = 4, cooldown_s: float = 300.0):
        self._threshold = max(0, int(threshold))
        self._cooldown = float(cooldown_s)
        self.failures = 0
        self.opened_until = 0.0

    @property
    def enabled(self) -> bool:
        return self._threshold > 0

    def remaining_s(self, now: float | None = None) -> int:
        """熔断剩余秒数（0 = 未开路）。下发到 wire 让弹窗直接倒计时。"""
        wall = time.monotonic() if now is None else now
        return max(0, int(self.opened_until - wall))

    def ensure_closed_or_raise(self) -> None:
        if not self.enabled or self.opened_until <= 0.0:
            return
        remaining = self.remaining_s()
        if remaining > 0:
            raise IntelSearchError(
                "circuit_open", f"检索源连续失败已暂停，{remaining} 秒后自动重试")
        self.opened_until = 0.0        # 冷却结束：放行下一次真实调用（成功即完全复位）

    def note_ok(self) -> None:
        self.failures = 0
        self.opened_until = 0.0

    def note_failure(self) -> None:
        if not self.enabled:
            return
        self.failures += 1
        if self.failures >= self._threshold:
            self.opened_until = time.monotonic() + self._cooldown


class TavilyProvider:
    """tavily `/search` 适配器（body 传 `api_key`，`days` 控时效，`content` 是正文片段）。"""

    slug = "tavily"

    def __init__(self, *, api_key: str, base_url: str = "", timeout_s: float = 12.0,
                 max_retries: int = 2, proxy: str | None = None):
        self._api_key = api_key
        self._endpoint = (base_url or TAVILY_ENDPOINT).rstrip("/")
        self._timeout = float(timeout_s)
        self._max_retries = max(0, int(max_retries))
        self._proxy = proxy

    def _body(self, query: str, limit: int, freshness_days: int) -> dict:
        return {
            "api_key": self._api_key,
            "query": query,
            "search_depth": "basic",
            "max_results": max(1, int(limit)),
            "include_answer": False,
            "include_raw_content": False,
            "days": max(1, int(freshness_days)),
        }

    @staticmethod
    def _parse(payload: dict) -> list[SearchDoc]:
        rows = payload.get("results") if isinstance(payload, dict) else None
        if not isinstance(rows, list):
            raise IntelSearchError("bad_response", "检索源返回了无法解析的结果结构")
        docs: list[SearchDoc] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            url = str(row.get("url") or "").strip()
            if not url:
                continue
            docs.append(SearchDoc(
                title=str(row.get("title") or "").strip(),
                url=url,
                snippet=str(row.get("content") or "").strip(),
                publisher=str(row.get("source") or "").strip(),
                published_ts=_parse_rfc3339(row.get("published_date")),
                score=float(row.get("score") or 0.0)))
        return docs

    async def search(self, query: str, *, limit: int, freshness_days: int) -> list[SearchDoc]:
        """一次检索（含有界重试）。失败一律抛 `IntelSearchError`。"""
        last: IntelSearchError | None = None
        for attempt in range(self._max_retries + 1):
            try:
                async with httpx.AsyncClient(timeout=self._timeout,
                                             proxy=self._proxy) as client:
                    resp = await client.post(
                        self._endpoint, json=self._body(query, limit, freshness_days),
                        headers={"Content-Type": "application/json"})
                if resp.status_code >= 400:
                    detail = resp.text[:200]
                    if resp.status_code in RETRYABLE_STATUS and attempt < self._max_retries:
                        last = IntelSearchError(
                            "http_error", f"HTTP {resp.status_code}: {detail}", self._api_key)
                        await _sleep_backoff(attempt)
                        continue
                    raise IntelSearchError(
                        "http_error", f"HTTP {resp.status_code}: {detail}", self._api_key)
                return self._parse(resp.json())
            except IntelSearchError:
                raise
            except _RETRYABLE_EXC as exc:
                last = IntelSearchError("network_error", str(exc), self._api_key)
                if attempt >= self._max_retries:
                    raise last from exc
                await _sleep_backoff(attempt)
            except httpx.HTTPError as exc:
                raise IntelSearchError("network_error", str(exc), self._api_key) from exc
            except ValueError as exc:       # json 解析失败
                raise IntelSearchError("bad_response", str(exc), self._api_key) from exc
        raise last or IntelSearchError("network_error", "检索请求失败")


# ---------------- brave / serper（intel-closed-loop 轴 D §3.4.3） ----------------
_RELATIVE_DATE_RE = re.compile(
    r"^(\d+)\s+(minute|hour|day|week|month|year)s?\s+ago$", re.IGNORECASE)
_RELATIVE_SECONDS = {"minute": 60, "hour": 3600, "day": 86400, "week": 7 * 86400,
                     "month": 30 * 86400, "year": 365 * 86400}


def _parse_relative_date(raw, *, now: float) -> int:
    """serper 的 `date` 字段 → 墙钟秒。支持 ``"3 days ago"`` 与 ``"Jan 5, 2026"``。

    **任何解析失败一律返回 0**（⇒ `undated=True` ⇒ 该条永不可升级，v1 既有规则）。
    **绝不**在解析失败时回落到 `now`（G-14）：把「不知道什么时候发的」写成「刚刚发的」会让
    时效闸与升级门槛同时失守，且不报任何错。非英文文案同样落 0 而不是猜。
    """
    if not raw or not isinstance(raw, str):
        return 0
    text = raw.strip()
    match = _RELATIVE_DATE_RE.match(text)
    if match:
        span = _RELATIVE_SECONDS.get(match.group(2).lower())
        if not span:
            return 0
        return max(0, int(float(now) - int(match.group(1)) * span))
    for fmt in ("%b %d, %Y", "%B %d, %Y"):
        try:
            parsed = datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        return int(parsed.timestamp())
    return 0


def _freshness_token(freshness_days: int, *, tokens: tuple[str, str, str]) -> str:
    """`freshness_days` → 适配器自己的时效枚举（`<=1` / `<=7` / 其余，两家同一映射）。"""
    days = max(1, int(freshness_days))
    if days <= 1:
        return tokens[0]
    return tokens[1] if days <= 7 else tokens[2]


class _HttpProvider:
    """brave / serper 共用的重试骨架（`TavilyProvider.search` 的形状，只换请求与解析）。

    刻意不把 `TavilyProvider` 改成继承它：tavily 是 body 传 key 的 POST、brave 是 query 串的
    GET，硬凑一个基类只会让两边的差异藏进条件分支里。这里复用的是**重试与脱敏**，不是请求。
    """

    slug = ""

    def __init__(self, *, api_key: str, endpoint: str, base_url: str = "",
                 timeout_s: float = 12.0, max_retries: int = 2, proxy: str | None = None):
        self._api_key = api_key
        self._endpoint = (base_url or endpoint).rstrip("/")
        self._timeout = float(timeout_s)
        self._max_retries = max(0, int(max_retries))
        self._proxy = proxy

    async def _request(self, client: httpx.AsyncClient, query: str, limit: int,
                       freshness_days: int) -> httpx.Response:
        raise NotImplementedError

    @staticmethod
    def _parse(payload: dict, *, now: float) -> list[SearchDoc]:
        raise NotImplementedError

    async def search(self, query: str, *, limit: int, freshness_days: int) -> list[SearchDoc]:
        """一次检索（含有界重试）。失败一律抛 `IntelSearchError`。"""
        last: IntelSearchError | None = None
        capped = max(1, min(int(limit), _PROVIDER_MAX_RESULTS))
        for attempt in range(self._max_retries + 1):
            try:
                async with httpx.AsyncClient(timeout=self._timeout,
                                             proxy=self._proxy) as client:
                    resp = await self._request(client, query, capped, freshness_days)
                if resp.status_code >= 400:
                    detail = resp.text[:200]
                    if resp.status_code in RETRYABLE_STATUS and attempt < self._max_retries:
                        last = IntelSearchError(
                            "http_error", f"HTTP {resp.status_code}: {detail}", self._api_key)
                        await _sleep_backoff(attempt)
                        continue
                    raise IntelSearchError(
                        "http_error", f"HTTP {resp.status_code}: {detail}", self._api_key)
                return self._parse(resp.json(), now=time.time())
            except IntelSearchError:
                raise
            except _RETRYABLE_EXC as exc:
                last = IntelSearchError("network_error", str(exc), self._api_key)
                if attempt >= self._max_retries:
                    raise last from exc
                await _sleep_backoff(attempt)
            except httpx.HTTPError as exc:
                raise IntelSearchError("network_error", str(exc), self._api_key) from exc
            except ValueError as exc:       # json 解析失败
                raise IntelSearchError("bad_response", str(exc), self._api_key) from exc
        raise last or IntelSearchError("network_error", "检索请求失败")


class BraveProvider(_HttpProvider):
    """brave `/res/v1/web/search` 适配器（GET，`X-Subscription-Token` 头，`page_age` 是 RFC3339）。"""

    slug = "brave"

    def __init__(self, **kwargs):
        super().__init__(endpoint=BRAVE_ENDPOINT, **kwargs)

    async def _request(self, client, query: str, limit: int, freshness_days: int):
        return await client.get(
            self._endpoint,
            params={"q": query, "count": limit,
                    "freshness": _freshness_token(freshness_days,
                                                  tokens=("pd", "pw", "pm"))},
            headers={"Accept": "application/json",
                     "X-Subscription-Token": self._api_key})

    @staticmethod
    def _parse(payload: dict, *, now: float) -> list[SearchDoc]:
        rows = ((payload or {}).get("web") or {}).get("results")
        if not isinstance(rows, list):
            raise IntelSearchError("bad_response", "检索源返回了无法解析的结果结构")
        docs: list[SearchDoc] = []
        for index, row in enumerate(rows):
            if not isinstance(row, dict):
                continue
            url = str(row.get("url") or "").strip()
            if not url:
                continue
            profile = row.get("profile")
            docs.append(SearchDoc(
                title=str(row.get("title") or "").strip(), url=url,
                snippet=str(row.get("description") or "").strip(),
                publisher=str((profile or {}).get("name") or "").strip()
                if isinstance(profile, dict) else "",
                published_ts=_parse_rfc3339(row.get("page_age")),
                # brave 不给分数：用名次折成一个单调递减的分（`corpus.build` 只用它排序）。
                score=max(0.0, 1.0 - index * 0.01)))
        return docs


class SerperProvider(_HttpProvider):
    """serper `/search` 适配器（POST JSON，`X-API-KEY` 头，`date` 是**相对文案**）。"""

    slug = "serper"

    def __init__(self, **kwargs):
        super().__init__(endpoint=SERPER_ENDPOINT, **kwargs)

    async def _request(self, client, query: str, limit: int, freshness_days: int):
        return await client.post(
            self._endpoint,
            json={"q": query, "num": limit,
                  "tbs": _freshness_token(freshness_days,
                                          tokens=("qdr:d", "qdr:w", "qdr:m"))},
            headers={"Content-Type": "application/json", "X-API-KEY": self._api_key})

    @staticmethod
    def _parse(payload: dict, *, now: float) -> list[SearchDoc]:
        rows = (payload or {}).get("organic")
        if not isinstance(rows, list):
            raise IntelSearchError("bad_response", "检索源返回了无法解析的结果结构")
        docs: list[SearchDoc] = []
        for index, row in enumerate(rows):
            if not isinstance(row, dict):
                continue
            url = str(row.get("link") or row.get("url") or "").strip()
            if not url:
                continue
            docs.append(SearchDoc(
                title=str(row.get("title") or "").strip(), url=url,
                snippet=str(row.get("snippet") or "").strip(),
                publisher=str(row.get("source") or "").strip(),
                published_ts=_parse_relative_date(row.get("date"), now=now),
                score=max(0.0, 1.0 - index * 0.01)))
        return docs


async def _sleep_backoff(attempt: int) -> None:
    """指数退避 + 抖动（可被 cancel 中断，语义与 `agent/llm/retry.py` 同源）。"""
    base = min(_BACKOFF_BASE_MS * (2 ** attempt), _BACKOFF_MAX_MS)
    await asyncio.sleep((base + random.uniform(0, _JITTER_MS)) / 1000.0)


def classify_provider(slug: str, api_key: str) -> str:
    """检索源配置的分类判定（**纯函数，无 IO**）。

    Args:
        slug: 用户选中的检索源 slug（大小写与前后空格在这里归一）。
        api_key: 该源的 API Key（库内行优先的那一份）。

    Returns:
        ``""`` = 可以装配；否则是 `taxonomy.ARM_REASONS` 里的一个码：
        ``"provider_unset"`` / ``"key_missing"`` / ``"provider_unknown"``。

    本函数是「检索源到底算不算配好了」的**唯一**判据。`resolve_search_provider` 与
    `arming.classify_config` 都调它——两处各手抄一份真值表，就会出现「面板说配好了、运行时说
    没配」这种两边都不报错的分叉，而那正是 ai-trade-intel-arm-state 要修的缺陷本身。

    ⚠️ 判定顺序上有一处**刻意的**行为变化（arm-state §3.1.1 / G-2）：原实现先判空 key、再判
    slug 是否实现，故「拼错的 slug + 空 key」走的是静默的空 key 分支；这里把 `provider_unknown`
    排在 `key_missing` 之前，那种组合会多打一行 `logger.info`。用户拼错了源名比「key 没填」更
    值得说出来。
    """
    key = (slug or "none").strip().lower()
    if key in ("", "none"):
        return "provider_unset"
    if key not in PROVIDER_SLUGS[1:]:
        return "provider_unknown"
    if not (api_key or "").strip():
        return "key_missing"
    return ""


def resolve_search_provider(*, slug: str, api_key: str, base_url: str = "",
                            timeout_s: float = 12.0, max_retries: int = 2,
                            proxy: str | None = None) -> IntelSearchProvider | None:
    """`slug` + key → 适配器实例；未配置 / 无 key ⇒ ``None``（全链路走「未配置」降级路径）。

    **绝不**在这里悄悄回落到「让模型自己写一条」：未配置时本特性诚实降级——按钮仍在、弹窗明说
    「未配置情报检索源」并给出设置入口，一条情报都不产出（G-7）。

    判据取自 `classify_provider`（**同一张真值表**，绝不在这里再抄一份）；三种「没配好」在返回值
    上仍然不可区分（恒 `None`），要区分的调用方去问 `classify_provider`。
    """
    key = (slug or "none").strip().lower()
    verdict = classify_provider(key, api_key)
    if verdict == "provider_unknown":
        logger.info("ai-trade intel: 未实现的检索源 %s，按未配置处理（已实现：%s）",
                    key, "/".join(PROVIDER_SLUGS[1:]))
        return None
    if verdict:
        return None
    if key == "tavily":
        return TavilyProvider(api_key=api_key.strip(), base_url=base_url,
                              timeout_s=timeout_s, max_retries=max_retries, proxy=proxy)
    if key == "brave":
        return BraveProvider(api_key=api_key.strip(), base_url=base_url,
                             timeout_s=timeout_s, max_retries=max_retries, proxy=proxy)
    if key == "serper":
        return SerperProvider(api_key=api_key.strip(), base_url=base_url,
                              timeout_s=timeout_s, max_retries=max_retries, proxy=proxy)
    return None


class SearchRunner:
    """一轮内的并发检索：外层死线 + 熔断 + 逐 query 容错。

    单 query 超时不拖垮整轮（`return_exceptions=True`），但**全部 query 都失败**即抛
    `IntelSearchError` —— 由调用方记 `degrade_code="search_failed"` 并进退避，**不静默跳过**。
    """

    def __init__(self, provider: IntelSearchProvider, *, timeout_s: float = 12.0,
                 breaker: _Breaker | None = None):
        self._provider = provider
        self._timeout = float(timeout_s)
        self._breaker = breaker or _Breaker()

    @property
    def slug(self) -> str:
        return getattr(self._provider, "slug", "unknown")

    @property
    def breaker(self) -> _Breaker:
        return self._breaker

    async def search_many(self, queries: Iterable[str], *, limit: int,
                          freshness_days: int) -> list[SearchDoc]:
        """并发跑完全部查询并拼接结果（去重留给 `corpus.build`）。"""
        items = [q for q in queries if q]
        if not items:
            return []
        self._breaker.ensure_closed_or_raise()
        try:
            async with asyncio.timeout(self._timeout * 1.5):
                results = await asyncio.gather(
                    *[self._provider.search(q, limit=limit, freshness_days=freshness_days)
                      for q in items], return_exceptions=True)
        except asyncio.TimeoutError as exc:
            self._breaker.note_failure()
            raise IntelSearchError("timeout", f"检索整轮超时（{self._timeout * 1.5:.0f}s）") from exc
        docs: list[SearchDoc] = []
        errors: list[BaseException] = []
        for result in results:
            if isinstance(result, BaseException):
                errors.append(result)
                continue
            docs.extend(result)
        if not docs and errors:
            self._breaker.note_failure()
            first = errors[0]
            code = getattr(first, "code", "network_error")
            raise IntelSearchError(code, str(getattr(first, "message", first)))
        self._breaker.note_ok()
        return docs


# probe 的服务端总耗时**硬封顶**（评审 P1-6）：前端 `httpTimeout.ts` 的 `FAST_MS = 20_000` 对
# `/collect`（202 排队即返回）成立，对 probe 不成立——它是一次同步的第三方出网调用，
# `search_timeout_s` 可配到 60s、`max_retries` 可配到 5，最坏耗时远超 FAST 档，症状是「后端其实
# 成功了，按钮却报超时」。probe 是配置校验、不是生产取数，故这里 `max_retries=0`。
PROBE_MAX_WALL_S = 12.0


async def probe_provider(*, slug: str, api_key: str, base_url: str = "",
                         timeout_s: float = 12.0, proxy: str | None = None) -> dict:
    """「测试连接」：一次受硬封顶约束的检索，回 `{ok, latencyMs, sampleTitle, message}`。"""
    provider = resolve_search_provider(slug=slug, api_key=api_key, base_url=base_url,
                                       timeout_s=min(float(timeout_s), PROBE_MAX_WALL_S),
                                       max_retries=0, proxy=proxy)
    if provider is None:
        return {"ok": False, "latencyMs": 0, "sampleTitle": "",
                "message": "尚未选择检索源或未填写 API Key"}
    started = time.monotonic()
    try:
        async with asyncio.timeout(PROBE_MAX_WALL_S):
            docs = await provider.search("bitcoin market news", limit=3, freshness_days=3)
    except asyncio.TimeoutError:
        return {"ok": False, "latencyMs": int(PROBE_MAX_WALL_S * 1000), "sampleTitle": "",
                "message": f"检索源在 {PROBE_MAX_WALL_S:.0f} 秒内没有响应"}
    except IntelSearchError as exc:
        return {"ok": False, "latencyMs": int((time.monotonic() - started) * 1000),
                "sampleTitle": "", "message": exc.message}
    latency = int((time.monotonic() - started) * 1000)
    if not docs:
        return {"ok": False, "latencyMs": latency, "sampleTitle": "",
                "message": "检索源连接正常，但本次没有返回任何结果"}
    return {"ok": True, "latencyMs": latency, "sampleTitle": docs[0].title,
            "message": f"连接正常，返回 {len(docs)} 条结果"}
