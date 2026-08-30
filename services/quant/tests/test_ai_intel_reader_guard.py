"""出网安全闸的两段判据（evidence-grounding §7.1 · test_ai_intel_reader_guard）。

本文件**一个真实的出网请求都不发**：第一段是纯函数，第二段打桩 `getaddrinfo`。

G-3 / G-26 在这里：重定向的**每一跳**都要过完整两段闸，且**配了代理也照跑**——走代理时真正的
DNS 解析发生在代理侧，此时语法段是唯一真正的防线，少跑一次解析段的收益是零。
"""
from __future__ import annotations

import socket

import httpx
import pytest

from app.ai_auto.intel import reader


# ---------------- 第一段：语法闸（纯函数） ----------------
@pytest.mark.parametrize("url", [
    "http://10.0.0.1/x", "http://127.0.0.1/x", "https://169.254.169.254/latest/meta-data/",
    "http://172.16.5.4/x", "http://192.168.1.1/x", "http://100.64.0.1/x",
    "http://0.0.0.0/x", "http://224.0.0.1/x",
])
def test_rejects_private_ipv4_literal(url):
    assert reader.guard_url(url) == "private_host"


@pytest.mark.parametrize("url", ["http://[::1]/x", "http://[fc00::1]/x", "http://[fe80::1]/x",
                                 "http://[::ffff:127.0.0.1]/x"])
def test_rejects_private_ipv6_literal(url):
    assert reader.guard_url(url) == "private_host"


@pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://example.com/x",
                                 "javascript:alert(1)", "gopher://example.com/"])
def test_rejects_non_http_scheme(url):
    assert reader.guard_url(url) == "bad_scheme"


def test_rejects_userinfo_in_url():
    assert reader.guard_url("http://user:pw@example.com/x") == "bad_userinfo"


@pytest.mark.parametrize("url", ["http://example.com:8080/x", "https://example.com:22/x"])
def test_rejects_nonstandard_port(url):
    assert reader.guard_url(url) == "bad_port"


def test_allows_explicit_standard_ports():
    assert reader.guard_url("http://example.com:80/x") is None
    assert reader.guard_url("https://example.com:443/x") is None


@pytest.mark.parametrize("host", ["localhost", "printer.local", "db.internal",
                                  "box.localdomain", "gw.home.arpa", "abc.onion"])
def test_rejects_localhost_and_internal_suffixes(host):
    assert reader.guard_url(f"http://{host}/x") == "private_host"


def test_rejects_bad_host_shapes():
    assert reader.guard_url("http:///x") == "bad_host"
    assert reader.guard_url("http://" + "a" * 300 + ".com/x") == "bad_host"
    assert reader.guard_url("http://exa mple.com/x") == "bad_host"


def test_allows_a_normal_public_url():
    assert reader.guard_url("https://www.reuters.com/markets/a-b-c/") is None


# ---------------- 第二段：解析闸 ----------------
def _stub_getaddrinfo(monkeypatch, addresses: list[str]):
    def fake(host, port, family=0, type=0, proto=0, flags=0):   # noqa: A002
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (addr, 0)) for addr in addresses]

    monkeypatch.setattr(socket, "getaddrinfo", fake)


@pytest.mark.asyncio
async def test_rejects_when_dns_resolves_to_private(monkeypatch):
    _stub_getaddrinfo(monkeypatch, ["93.184.216.34", "10.1.2.3"])
    assert await reader.resolve_is_public("evil.example") is False, \
        "解析出的**每一个**地址都必须是公网地址"


@pytest.mark.asyncio
async def test_accepts_when_every_address_is_public(monkeypatch):
    _stub_getaddrinfo(monkeypatch, ["93.184.216.34", "1.1.1.1"])
    assert await reader.resolve_is_public("example.com") is True


@pytest.mark.asyncio
async def test_unresolvable_host_is_not_public(monkeypatch):
    def boom(*args, **kwargs):
        raise OSError("nxdomain")

    monkeypatch.setattr(socket, "getaddrinfo", boom)
    assert await reader.resolve_is_public("nope.example") is False


# ---------------- 逐跳复检与代理 ----------------
class _CountingTransport(httpx.AsyncBaseTransport):
    """记下**实际发出去**了几次请求；第一次回一个指向内网的 302。"""

    def __init__(self, location: str):
        self.requests: list[str] = []
        self._location = location

    async def handle_async_request(self, request):
        self.requests.append(str(request.url))
        return httpx.Response(302, headers={"location": self._location},
                              request=request)


@pytest.mark.asyncio
async def test_redirect_to_private_host_is_blocked_on_every_hop(monkeypatch):
    """G-3：302 → `169.254.169.254` 必须在**第二跳发出之前**被拦下。

    这条用例断言的不是「结果是 blocked」，而是**没有第二次出网**——`httpx` 自动跟随时中间跳
    不经过我们的闸，那正是这套手写循环存在的唯一理由。
    """
    _stub_getaddrinfo(monkeypatch, ["93.184.216.34"])
    transport = _CountingTransport("http://169.254.169.254/latest/meta-data/")
    cfg = reader.ReaderConfig(max_redirects=3)
    async with httpx.AsyncClient(transport=transport) as client:
        result = await reader._fetch_one(client, "https://news.example/x", cfg=cfg)
    assert result.status == "blocked"
    assert len(transport.requests) == 1, "第二跳绝不允许被发出去"


@pytest.mark.asyncio
async def test_proxy_does_not_skip_either_stage(monkeypatch):
    """G-26：配了代理也照跑两段闸，且**一个请求都不发**。

    走代理时真正的解析发生在代理侧，而桌面用户的代理常常就在 `127.0.0.1`——它本身能到达用户
    内网。此时语法段是唯一真正的防线，跳过解析段的收益是零。
    """
    calls: list[str] = []

    def fake(host, *args, **kwargs):
        calls.append(host)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.9", 0))]

    monkeypatch.setattr(socket, "getaddrinfo", fake)
    cfg = reader.ReaderConfig(proxy="http://127.0.0.1:7890")
    out = await reader.fetch_many(["http://192.168.0.5/x", "https://news.example/y"], cfg=cfg)
    assert out["http://192.168.0.5/x"].status == "blocked", "语法段：IP 字面量直接拦下"
    assert out["https://news.example/y"].status == "blocked", "解析段：代理下照跑"
    assert calls == ["news.example"], "语法段拦下的那条根本不该走到解析"


@pytest.mark.asyncio
async def test_disabled_reader_sends_nothing():
    cfg = reader.ReaderConfig(enabled=False)
    assert await reader.fetch_many(["https://news.example/x"], cfg=cfg) == {}


def test_select_targets_respects_per_domain_and_top_k():
    cfg = reader.ReaderConfig(top_k=3, max_per_domain=2)
    picked = reader.select_targets([
        "https://a.com/1", "https://a.com/2", "https://a.com/3",
        "https://b.com/1", "https://b.com/2", "ftp://c.com/1", "https://a.com/1",
    ], cfg=cfg)
    assert picked == ["https://a.com/1", "https://a.com/2", "https://b.com/1"]
