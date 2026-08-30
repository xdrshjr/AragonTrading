"""第一方网页抓取：两段 SSRF 闸 + 逐跳复检 + 流式大小闸 + 并发死线（§3.1.2 / §3.1.6）。

本模块是本轮**唯一**新增的「服务端主动出网到任意域名」的能力，因此闸写在设计里而不是留给实现。

四条不得违反的纪律：

1. **可抓 URL 的来源被唯一化**（不变式 I-5）：只能是检索源返回的 `SearchDoc.url`，永远不能来自
   用户输入或条目 `sources_json` 之外的任何地方。放开它等于把一个受限于搜索源的取数器变成一个
   通用的服务端代理。
2. **`fetch_many` 绝不抛**（`CancelledError` 除外，那是关停信号）。每一种失败都翻译成一个
   `ReaderResult.status`——一个新闻网站 504 不该让 AI 自动交易停摆。
3. **只持配置，不持任何需要关停的资源**（评审 P1-11）。`httpx.AsyncClient` 在 `fetch_many`
   内部 `async with` 建与关，与 `search.py:219` / `search.py:319` 两个既有适配器同形。这正是
   §3.5「不新增关停步骤」那句话**唯一**能成立的前提；把一个长生命周期 client 挂在 `IntelDeps`
   上，症状是 pytest 的 unclosed-client 告警与关停期偶发挂起，而两者都不指向本特性。
4. **请求里零本地事实**：固定 UA / Accept，**不带 Cookie、不带 Referer、不带 instId**——
   抓取只拿 URL。日志只记 `domain + status`，不记完整 URL（可能带 tracking id）、不记正文。

**代理下的语义**（评审 P1-10）：抓取器复用进程代理 `derived.proxy_url`，而 `httpx` 走代理时
**真正的 DNS 解析发生在代理侧**——本地 `getaddrinfo` 的结论与实际连接目标无关，桌面用户的代理
又常常就在 `127.0.0.1`。因此第一段（语法闸）恒为主防线，第二段（解析闸）在有代理时降级为
「尽力而为」但**照常执行**：它仍拦得住直接写死内网域名的那一类，而少跑一次的收益是零。
残余的 TOCTOU（DNS rebinding）已在 spec §3.1.2 与 §八 R-1 / R-3 诚实登记，不假装解决。
"""
from __future__ import annotations

import asyncio
import ipaddress
import logging
import re
import socket
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

import httpx

from . import taxonomy as tax
from .extract import extract_main_text

logger = logging.getLogger(__name__)

USER_AGENT = "AragonTrading-IntelReader/1.0 (+quant research; contact via app)"
_HEADERS: dict[str, str] = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,text/plain;q=0.9,*/*;q=0.1",
    "Accept-Language": "en,zh;q=0.8",
}

_ALLOWED_SCHEMES: frozenset[str] = frozenset({"http", "https"})
_ALLOWED_PORTS: frozenset[int] = frozenset({80, 443})
_PRIVATE_SUFFIXES: tuple[str, ...] = (".local", ".internal", ".localdomain",
                                      ".home.arpa", ".onion")
_HOST_RE = re.compile(r"^[a-z0-9._\-]+$")
_REDIRECT_STATUS: frozenset[int] = frozenset({301, 302, 303, 307, 308})
_HTML_PREFIXES: tuple[str, ...] = ("text/html", "application/xhtml+xml", "text/plain")
# CGNAT：`ipaddress` 不把它算作 private，但它同样能到达运营商内网。
_CGNAT = ipaddress.ip_network("100.64.0.0/10")

# `status` 的值域（与 `ai_intel_documents.status` 一一对应）。
READER_STATUSES: tuple[str, ...] = (
    "ok", "blocked", "http_error", "timeout", "network_error", "not_html", "too_large", "empty",
)


@dataclass(frozen=True)
class ReaderConfig:
    """抓取器的全部配置。**只持配置**——本对象上没有任何需要关停的资源（纪律 3）。"""

    enabled: bool = True
    top_k: int = 6
    timeout_s: float = 8.0
    round_deadline_s: float = 25.0
    concurrency: int = 4
    max_bytes: int = 1_500_000
    max_redirects: int = 3
    max_per_domain: int = 2
    body_max_chars: int = 20_000
    proxy: str | None = None


@dataclass(frozen=True)
class ReaderResult:
    """一次抓取的结果。**没有任何一格会让轮次 failed**（§3.1.6）。"""

    url: str
    status: str = "network_error"
    final_url: str = ""
    domain: str = ""
    title: str = ""
    body: str = ""
    body_chars: int = 0
    byte_len: int = 0
    http_status: int = 0
    truncated: bool = False

    @property
    def ok(self) -> bool:
        return self.status == "ok" and bool(self.body)


# ---------------- 第一段：语法闸（纯函数，代理与否都同样有效） ----------------
def _is_public_ip(raw: str) -> bool:
    """一个 IP 字面量是否属于公网（私有 / 回环 / 链路本地 / CGNAT / 组播 / 保留一律为假）。"""
    try:
        ip = ipaddress.ip_address(raw)
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    if (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast
            or ip.is_reserved or ip.is_unspecified):
        return False
    return not (ip.version == 4 and ip in _CGNAT)


def guard_url(url: str) -> str | None:
    """出网前的语法闸。返回拒绝码或 ``None``（通过）。

    Returns:
        ``bad_scheme`` / ``bad_userinfo`` / ``bad_port`` / ``private_host`` / ``bad_host``
        之一；``None`` 表示这一跳可以发出去。
    """
    try:
        parts = urlsplit((url or "").strip())
        port = parts.port
    except ValueError:
        return "bad_port"
    if parts.scheme.lower() not in _ALLOWED_SCHEMES:
        return "bad_scheme"
    if parts.username or parts.password:
        return "bad_userinfo"
    if port is not None and port not in _ALLOWED_PORTS:
        return "bad_port"
    host = (parts.hostname or "").strip().lower()
    if not host or len(host) > 253:
        return "bad_host"
    if ":" in host or host.replace(".", "").isdigit():
        return None if _is_public_ip(host) else "private_host"
    if not _HOST_RE.match(host):
        return "bad_host"
    if host == "localhost" or any(host.endswith(suffix) for suffix in _PRIVATE_SUFFIXES):
        return "private_host"
    return None


# ---------------- 第二段：解析闸（一次 getaddrinfo，跑在默认线程池里） ----------------
async def resolve_is_public(host: str) -> bool:
    """解析出的**每一个**地址都必须是公网地址；解析失败即视为不通过。

    有代理时这一段是「尽力而为」（真正的解析发生在代理侧），但**照常执行**——它仍拦得住
    直接写死内网域名的那一类，而少跑一次的收益是零。
    """
    if not host:
        return False
    try:
        infos = await asyncio.to_thread(socket.getaddrinfo, host, None, 0,
                                        socket.SOCK_STREAM)
    except asyncio.CancelledError:
        raise
    except OSError:
        return False
    addresses = [str(info[4][0]).split("%", 1)[0] for info in infos if info and info[4]]
    return bool(addresses) and all(_is_public_ip(addr) for addr in addresses)


async def guard_hop(url: str) -> str | None:
    """一跳的完整两段闸（**每一跳都要过**，见 `_fetch_one` 的循环）。"""
    code = guard_url(url)
    if code:
        return code
    host = (urlsplit(url).hostname or "").strip().lower()
    return None if await resolve_is_public(host) else "private_host"


# ---------------- 抓取 ----------------
def _result(url: str, *, status: str, final_url: str = "", http_status: int = 0,
            byte_len: int = 0) -> ReaderResult:
    return ReaderResult(url=url, status=status, final_url=final_url or url,
                        domain=tax.normalize_domain(url), http_status=http_status,
                        byte_len=byte_len)


async def _read_bounded(response: httpx.Response, *, max_bytes: int) -> tuple[bytes, bool]:
    """流式读取并**在超限的那一刻中断**。

    读完再判等于没有上限：一个 500MB 的响应会先把内存吃光，再被我们礼貌地拒绝。
    """
    cap = max(1, int(max_bytes))
    chunks: list[bytes] = []
    total = 0
    async for chunk in response.aiter_bytes():
        chunks.append(chunk)
        total += len(chunk)
        if total > cap:
            return b"".join(chunks), True
    return b"".join(chunks), False


async def _read_response(url: str, current: str, response: httpx.Response, *,
                         cfg: ReaderConfig) -> ReaderResult:
    """响应闸：状态码 → Content-Type → 流式大小 → 正文抽取。"""
    if response.status_code >= 400:
        return _result(url, status="http_error", final_url=current,
                       http_status=response.status_code)
    content_type = (response.headers.get("content-type") or "").split(";")[0].strip().lower()
    if not content_type.startswith(_HTML_PREFIXES):
        return _result(url, status="not_html", final_url=current,
                       http_status=response.status_code)
    payload, over = await _read_bounded(response, max_bytes=cfg.max_bytes)
    if over:
        return _result(url, status="too_large", final_url=current,
                       http_status=response.status_code, byte_len=len(payload))
    encoding = response.charset_encoding or "utf-8"
    try:
        html = payload.decode(encoding, errors="replace")
    except LookupError:                      # 服务端给了一个 Python 不认识的 charset
        html = payload.decode("utf-8", errors="replace")
    extracted = extract_main_text(html, max_chars=cfg.body_max_chars)
    if not extracted.block_count:
        return _result(url, status="empty", final_url=current,
                       http_status=response.status_code, byte_len=len(payload))
    return ReaderResult(
        url=url, status="ok", final_url=current, domain=tax.normalize_domain(current),
        title=extracted.title, body=extracted.text, body_chars=len(extracted.text),
        byte_len=len(payload), http_status=response.status_code,
        truncated=extracted.truncated)


async def _fetch_one(client: httpx.AsyncClient, url: str, *,
                     cfg: ReaderConfig) -> ReaderResult:
    """抓一篇（含手动重定向跟随）。**绝不抛**（`CancelledError` 除外）。

    重定向必须手动跟随：`httpx` 自动跟随时**中间跳不经过我们的闸**，一个
    `https://news.example/x` → `http://169.254.169.254/…` 的 302 就能穿过去。
    """
    current = url
    for _hop in range(max(0, int(cfg.max_redirects)) + 1):
        code = await guard_hop(current)
        if code:
            logger.info("ai-trade intel reader blocked %s (%s)",
                        tax.normalize_domain(current) or "?", code)
            return _result(url, status="blocked", final_url=current)
        try:
            async with client.stream("GET", current, headers=_HEADERS,
                                     follow_redirects=False,
                                     timeout=cfg.timeout_s) as response:
                if response.status_code not in _REDIRECT_STATUS:
                    return await _read_response(url, current, response, cfg=cfg)
                location = (response.headers.get("location") or "").strip()
            if not location:
                return _result(url, status="http_error", final_url=current)
            current = urljoin(current, location)
        except asyncio.CancelledError:
            raise                            # 关停信号：绝不吞
        except httpx.TimeoutException:
            return _result(url, status="timeout", final_url=current)
        except (httpx.HTTPError, ValueError, OSError):
            return _result(url, status="network_error", final_url=current)
    return _result(url, status="blocked", final_url=current)   # 跳数超限


def select_targets(urls, *, cfg: ReaderConfig) -> list[str]:
    """按语料顺序挑出本轮真正要抓的那几篇（去重 + 每域名礼貌闸 + 前 K 篇）。

    每域名上限**先于** top-K 生效：否则一个刷屏站点会把 6 个名额全占了，而我们真正想要的是
    6 篇来自不同来源的正文。
    """
    picked: list[str] = []
    seen: set[str] = set()
    per_domain: dict[str, int] = {}
    for raw in urls or []:
        url = str(raw or "").strip()
        if not url or url in seen or not tax.is_http_url(url):
            continue
        seen.add(url)
        domain = tax.normalize_domain(url)
        if per_domain.get(domain, 0) >= max(1, int(cfg.max_per_domain)):
            continue
        per_domain[domain] = per_domain.get(domain, 0) + 1
        picked.append(url)
        if len(picked) >= max(1, int(cfg.top_k)):
            break
    return picked


async def fetch_many(urls, *, cfg: ReaderConfig) -> dict[str, ReaderResult]:
    """抓一批网页。**绝不抛**（`CancelledError` 除外）。

    Args:
        urls: 候选 URL（按语料顺序；本函数自己做去重 / 域名闸 / top-K）。
        cfg: 抓取配置。`enabled=False` ⇒ **一个请求都不发**，返回空字典。

    Returns:
        ``{原始 url: ReaderResult}``——**每一个被选中的目标都有一行**，包括被本轮死线掐掉的
        那些（它们记 `timeout`）。少了这一条，`reader_attempt` 的分母会随死线系统性变小，而
        命中率正是把 `quote_mode` 翻到 strict 的唯一判据。
    """
    targets = select_targets(urls, cfg=cfg)
    if not cfg.enabled or not targets:
        return {}
    semaphore = asyncio.Semaphore(max(1, int(cfg.concurrency)))
    out: dict[str, ReaderResult] = {}
    async with httpx.AsyncClient(timeout=cfg.timeout_s, proxy=cfg.proxy or None) as client:
        async def _guarded(target: str) -> ReaderResult:
            async with semaphore:
                return await _fetch_one(client, target, cfg=cfg)

        tasks = {asyncio.ensure_future(_guarded(target)): target for target in targets}
        done, pending = await asyncio.wait(tasks.keys(),
                                           timeout=max(1.0, float(cfg.round_deadline_s)))
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        for task, target in tasks.items():
            if task not in done:
                out[target] = _result(target, status="timeout")
                continue
            error = task.exception()
            if error is not None:
                logger.info("ai-trade intel reader failed %s: %s",
                            tax.normalize_domain(target) or "?", type(error).__name__)
                out[target] = _result(target, status="network_error")
            else:
                out[target] = task.result()
    return out
