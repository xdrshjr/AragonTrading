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

**代理 / TUN 下的语义**（ai-trade-silent-degradation-repair 轴 B-1 重写了这一段）：

此前这里写的是「解析闸在有代理时降级为『尽力而为』但**照常执行**……少跑一次的收益是零」。
**那句自陈在事实上是错的**：它不是「尽力而为」，它是 fail-closed 且因此是全称的。在 fake-IP
透明代理（Clash / sing-box / Surge 一类）下，本机每一个域名都解析进 `198.18.0.0/15`，而
`ipaddress.is_private` 对该段为 True ⇒ **这道闸对每一个公开域名都判假**。生产实测：96 次抓取、
`reader_ok = 0`、`ai_intel_documents` 29 行全部 `blocked (private_host)`，覆盖 26 个互不相同的
公开域名（coindesk / coinbase / binance / tradingview …）。而注释里的判据是「有没有配代理」，
这台机器上一个代理键都没有（透明 TUN 不需要），所以任何以「配了代理才放宽」为形状的修法在真机上
一次都不会生效。

现在的判据是**三分类 + 出网后对端复核**（见 `intel/netpolicy.py` 与 `guard_hop`）：
`private` 仍拒；`synthetic`（合成段）降级为「域名闸 + 连接建立后的真实对端地址复核」——后者与
「这个连接到底通到哪」有**因果关系**，而 DNS 预判没有。`strict` 档逐字保留今天的行为。
残余的 TOCTOU（DNS rebinding）反而被对端复核**补上了一半**（复核发生在连接建立之后）。
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
from .netpolicy import classify_addr, classify_resolution, normalize_peer, parse_cidrs

logger = logging.getLogger(__name__)

# 轴 B-2：**进程内**拒因计数器（重启归零）。拒因今天只出现在一行 `logger.info` 里、从不落库，
# 而本轮是零迁移，故它无处可持久化——那就如实做成一个进程内计数器，并在 wire 上带
# `sinceProcessStart: true`。一个重启就归零的计数器如果不说自己会归零，就是在制造第五条静默降级。
_reject_counter: dict[str, int] = {}


def note_reject(code: str) -> None:
    """记一次拒绝（`guard_hop` 每返回一个拒因即 +1）。"""
    if not code:
        return
    _reject_counter[code] = _reject_counter.get(code, 0) + 1


def reject_counts() -> dict[str, int]:
    """本次运行以来的拒因分布（只读副本）。"""
    return dict(_reject_counter)


def reset_reject_counts() -> None:
    """仅测试用：清空计数（进程单例的既有复位范式）。"""
    _reject_counter.clear()

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
    # 轴 B-1：`auto` = 解析结果三分类（合成段降级放行 + 对端复核）；`strict` = 逐字节回到今天。
    # 默认取**新行为**而不是老行为：默认老行为意味着「忘了接线 = 功能关着」，那恰恰是本轮要
    # 消灭的静默降级形状。
    resolver: str = "auto"
    # 追加的合成地址段（逗号分隔 CIDR），留给用 `240.0.0.0/4` 一类段做 fake-IP 的代理实现。
    synthetic_cidrs: str = ""


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
    # 轴 B-1c：这一篇的**对端地址**有没有真的被复核过。经显式代理出网时它恒为 `False`
    # （TCP 对端是代理，不是目标站点），健康行据此如实显示「经代理出网，对端不可判」——
    # **降级要有声，哪怕降的是一道防护**。
    peer_checked: bool = False

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
async def resolve_addresses(host: str) -> list[str]:
    """本机解析出的全部地址（解析失败 ⇒ 空列表 ⇒ 上游判 `unresolved`）。"""
    if not host:
        return []
    try:
        infos = await asyncio.to_thread(socket.getaddrinfo, host, None, 0,
                                        socket.SOCK_STREAM)
    except asyncio.CancelledError:
        raise
    except OSError:
        return []
    return [str(info[4][0]).split("%", 1)[0] for info in infos if info and info[4]]


async def resolve_is_public(host: str) -> bool:
    """解析出的**每一个**地址都必须是公网地址（`strict` 档的判据，逐字保留今天的行为）。"""
    addresses = await resolve_addresses(host)
    return bool(addresses) and all(_is_public_ip(addr) for addr in addresses)


def _host_is_ip_literal(host: str) -> bool:
    """主机名是不是 IP 字面量（`guard_url` 用的同一条判据）。"""
    return ":" in host or host.replace(".", "").isdigit()


async def guard_hop(url: str, *, cfg: ReaderConfig | None = None) -> str | None:
    """一跳的完整两段闸（**每一跳都要过**，见 `_fetch_one` 的循环）。

    `strict` 档（`cfg is None` 时的兼容默认亦然）逐字节等于今天：语法闸 → 解析闸二值判定，
    `synthetic` 与解析失败都记 `private_host`。

    `auto` 档把解析结果四分（`intel/netpolicy.classify_resolution`）：

    ==============  ==========================================================
    解析结果        `auto` 的处置
    ==============  ==========================================================
    `public`        放行
    `private`       拒（`private_host`）——混合了真实私有地址也走这一格
    `synthetic`     **降级放行**：DNS 不再有否决权，改由出网后的对端复核兜底
    `unresolved`    拒，但记**新拒因** `dns_failed`——「本机 DNS 坏了」与「这个域名指向内网」
                    是两件事，压成同一个码会让台账再也答不出「今天为什么一篇都没抓到」
    ==============  ==========================================================

    `synthetic` 的降级放行还要求两件事同时成立：

    1. **主机不是 IP 字面量。** 这一条在正常拓扑下**不可达**（IP 字面量的 `getaddrinfo` 返回
       它自己 ⇒ `classify_resolution` 恒得 `public`），保留它的理由必须写清楚，否则下一轮
       一定会被当成噪声删掉：它守的是两类未来——①解析器被污染（`/etc/hosts`、恶意 DNS、
       企业 DoH 策略）把一个 IP 字面量映到别处；②本仓某天引入自定义 resolver 或 `httpx`
       的 transport 级解析钩子。门禁 G-B5 因此写成**注入解析替身**的白盒用例。
    2. `taxonomy.normalize_domain(host)` 能归一出一个非空注册域（拒绝畸形主机名）。
    """
    code = guard_url(url)
    if code:
        note_reject(code)
        return code
    host = (urlsplit(url).hostname or "").strip().lower()
    resolver = (cfg.resolver if cfg else "strict").strip().lower()
    if resolver != "auto":
        if await resolve_is_public(host):
            return None
        note_reject("private_host")
        return "private_host"
    extra = parse_cidrs(cfg.synthetic_cidrs if cfg else "")
    verdict = classify_resolution(await resolve_addresses(host), extra)
    if verdict == "public":
        return None
    if verdict == "unresolved":
        note_reject("dns_failed")
        return "dns_failed"
    if verdict == "private":
        note_reject("private_host")
        return "private_host"
    if _host_is_ip_literal(host) or not tax.normalize_domain(url):
        note_reject("private_host")
        return "private_host"
    return None


# ---------------- 抓取 ----------------
def _result(url: str, *, status: str, final_url: str = "", http_status: int = 0,
            byte_len: int = 0, peer_checked: bool = False) -> ReaderResult:
    return ReaderResult(url=url, status=status, final_url=final_url or url,
                        domain=tax.normalize_domain(url), http_status=http_status,
                        byte_len=byte_len, peer_checked=peer_checked)


def peer_verdict(response: httpx.Response, *, cfg: ReaderConfig) -> tuple[bool, str | None]:
    """出网**之后**的对端地址复核（轴 B-1c）：`(是否真的复核过, 拒因或 None)`。

    这是本轮把否决权还给「有因果关系的判据」的那一步——DNS 预判说的是「我这台机器怎么解析
    这个名字」，而对端地址说的是「这条连接实际通到哪」。它只增强、不放松，故三个档位下全开。

    **但「全开」被下面第 1 条收窄为「在能判的时候全开」**（评审 P0-1，阻断级）：

    1. **`cfg.proxy` 非空 ⇒ 整条跳过并返回 `(False, None)`。** `intel/evidence.py` 把
       `settings.proxy_url` 原样传进 `ReaderConfig.proxy`，`fetch_many` 用
       `httpx.AsyncClient(..., proxy=cfg.proxy or None)` 出网——**此时 TCP 对端是代理，
       不是目标站点**，而本项目目标用户的常见形态正是 `http://127.0.0.1:7890` 一类的本地代理，
       `classify_addr("127.0.0.1")` 判回环 ⇒ `private` ⇒ **每一篇都被拒**。那会把「TUN 机器上
       0 成功」原样换成「配了代理的机器上 0 成功」——与本轮 §二 R-2 总结的根因一字不差。
       跳过时如实记 `peer_checked=False`，由 B-2 的健康行说出「经代理出网，对端不可判」。
    1b. **对端是回环地址 ⇒ 同样整条跳过**（实施节点补，spec §实施过程发现的方案缺陷 I-2）。
       上面那条判据看不见 `httpx` 的 `trust_env` 代理与系统代理——它们不出现在 `cfg.proxy` 里，
       却同样会把 TCP 对端变成 `127.0.0.1`。本机实测就撞在这一格上，三篇全被判 `private_peer`。
       回环对端只可能意味着「这一跳经本机代理出去了」，见下方分支里的完整论证。
    2. **`server_addr` 是 `(host, port)` 元组**，归一由 `netpolicy.normalize_peer` 单点负责
       （含 IPv6 `%scope` 后缀）。这段跑在**已经拿到响应之后**——一个会抛的复核函数等于把
       「抓到了正文」变成「抓取器自己崩了」。
    3. **取不到对端（HTTP/2、非 httpcore transport、`MockTransport`、测试替身）⇒ 视为未知，
       不拒**。否则一次 `httpx` 版本变化会静默关掉整个功能（本仓已在「`websockets` 15 的
       `proxy` 默认值」上栽过一次同形的跟头）。

    `synthetic` 的对端**不拒**（TUN 下对端本来就是合成地址）；只有**真实私有**对端才拒。
    """
    if cfg.proxy:
        logger.debug("ai-trade intel reader peer check skipped (proxy configured)")
        return False, None
    raw = _peer_addr(response)
    if raw is None:
        return False, None
    if _is_loopback(raw):
        # 【实施节点在真机上量出来的第二个同形缺口，见 spec §实施过程发现的方案缺陷 I-2】
        # 设计只把「`cfg.proxy` 非空」当作代理豁免的判据，但 `httpx` 默认 `trust_env=True`：
        # 进程环境里的 `HTTPS_PROXY` / 系统代理**根本不会出现在 `cfg.proxy` 里**，而它们同样
        # 会把 TCP 对端变成 `127.0.0.1:7897`。本机实测正是这一格——三篇全被判 `private_peer`，
        # `reader_ok` 又回到 0。判据与被判定的事实之间再一次没有因果关系。
        #
        # **回环对端只可能有一个含义：这一跳是经本机代理出去的。** 它永远不可能是目标站点
        # 本身——「这个公开域名解析到 127.0.0.1」那一类早在解析闸（`private`）就被拒了，
        # 根本走不到这里。故这里放行不打开任何一道前面已经关上的门。
        logger.debug("ai-trade intel reader peer check skipped (loopback peer via local proxy)")
        return False, None
    extra = parse_cidrs(cfg.synthetic_cidrs)
    return True, ("private_peer" if classify_addr(raw, extra) == "private" else None)


def _is_loopback(raw: str) -> bool:
    """这一跳的 TCP 对端是不是本机（`127.0.0.0/8` / `::1`）。"""
    try:
        return ipaddress.ip_address(raw).is_loopback
    except ValueError:
        return False


def _peer_addr(response: httpx.Response) -> str | None:
    """从 `httpx` 响应上取真实对端地址；取不到 ⇒ `None`（视为未知，**不拒**）。

    `httpx` 把底层连接挂在 `response.extensions["network_stream"]`，`server_addr` 由它的
    `get_extra_info` 给出、形状是 `(host, port)`。HTTP/2、`MockTransport`、测试替身与未来的
    版本变化都可能让这条路取不到——**任何一步失败都退回「未知」**，绝不让一次库升级静默关掉
    整个抓取功能。
    """
    try:
        stream = (response.extensions or {}).get("network_stream")
        if stream is None:
            return None
        return normalize_peer(stream.get_extra_info("server_addr"))
    except Exception:  # noqa: BLE001 —— 见 docstring：复核取不到即未知
        return None


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
    """响应闸：**对端复核** → 状态码 → Content-Type → 流式大小 → 正文抽取。

    对端复核排在最前：它决定的是「这条连接该不该被读下去」，读完正文再判等于已经把内网服务的
    响应体读进了内存。
    """
    checked, reject = peer_verdict(response, cfg=cfg)
    if reject:
        note_reject(reject)
        logger.info("ai-trade intel reader blocked %s (%s)",
                    tax.normalize_domain(current) or "?", reject)
        return _result(url, status="blocked", final_url=current, peer_checked=True)
    if response.status_code >= 400:
        return _result(url, status="http_error", final_url=current,
                       http_status=response.status_code, peer_checked=checked)
    content_type = (response.headers.get("content-type") or "").split(";")[0].strip().lower()
    if not content_type.startswith(_HTML_PREFIXES):
        return _result(url, status="not_html", final_url=current,
                       http_status=response.status_code, peer_checked=checked)
    payload, over = await _read_bounded(response, max_bytes=cfg.max_bytes)
    if over:
        return _result(url, status="too_large", final_url=current,
                       http_status=response.status_code, byte_len=len(payload),
                       peer_checked=checked)
    encoding = response.charset_encoding or "utf-8"
    try:
        html = payload.decode(encoding, errors="replace")
    except LookupError:                      # 服务端给了一个 Python 不认识的 charset
        html = payload.decode("utf-8", errors="replace")
    extracted = extract_main_text(html, max_chars=cfg.body_max_chars)
    if not extracted.block_count:
        return _result(url, status="empty", final_url=current,
                       http_status=response.status_code, byte_len=len(payload),
                       peer_checked=checked)
    return ReaderResult(
        url=url, status="ok", final_url=current, domain=tax.normalize_domain(current),
        title=extracted.title, body=extracted.text, body_chars=len(extracted.text),
        byte_len=len(payload), http_status=response.status_code,
        truncated=extracted.truncated, peer_checked=checked)


async def _fetch_one(client: httpx.AsyncClient, url: str, *,
                     cfg: ReaderConfig) -> ReaderResult:
    """抓一篇（含手动重定向跟随）。**绝不抛**（`CancelledError` 除外）。

    重定向必须手动跟随：`httpx` 自动跟随时**中间跳不经过我们的闸**，一个
    `https://news.example/x` → `http://169.254.169.254/…` 的 302 就能穿过去。
    """
    current = url
    for _hop in range(max(0, int(cfg.max_redirects)) + 1):
        code = await guard_hop(current, cfg=cfg)
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
    note_reject("too_many_redirects")                          # 跳数超限：此前它静默进 blocked
    return _result(url, status="blocked", final_url=current)


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
