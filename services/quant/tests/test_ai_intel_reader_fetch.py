"""抓取本体：流式大小闸、Content-Type、死线与关停纪律（§7.1 · test_ai_intel_reader_fetch）。

全部走 `httpx.MockTransport` / 自定义 transport，**一个真实的出网请求都不发**。

G-4 的一半在这里：`fetch_many` 在任何输入下**都不抛**（`CancelledError` 除外，那是关停信号）。
"""
from __future__ import annotations

import asyncio
import socket

import httpx
import pytest

from app.ai_auto.intel import reader

_BODY_BLOCK = "这是一段足够长的正文，用来越过最短块阈值，它会被抽取器当成正文留下来。" * 2
_HTML = f"<html><head><title>标题</title></head><body><p>{_BODY_BLOCK}</p></body></html>"


@pytest.fixture(autouse=True)
def _public_dns(monkeypatch):
    """把解析闸固定成「公网」，让这一屏只测抓取本体。"""
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))])


async def _fetch(handler, *, cfg=None, url="https://news.example/a"):
    cfg = cfg or reader.ReaderConfig()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        return await reader._fetch_one(client, url, cfg=cfg)


@pytest.mark.asyncio
async def test_happy_path_extracts_body_and_title():
    result = await _fetch(lambda req: httpx.Response(
        200, headers={"content-type": "text/html; charset=utf-8"}, text=_HTML))
    assert result.status == "ok" and result.ok
    assert _BODY_BLOCK in result.body and result.title == "标题"
    assert result.domain == "news.example" and result.body_chars == len(result.body)


class _OversizeTransport(httpx.AsyncBaseTransport):
    """一个永远吐块的响应：用来验证「超限那一刻就中断」而不是读完再判。"""

    def __init__(self, chunk: bytes, chunks: int):
        self.sent = 0
        self._chunk = chunk
        self._chunks = chunks

    async def handle_async_request(self, request):
        async def stream():
            for _ in range(self._chunks):
                self.sent += len(self._chunk)
                yield self._chunk

        return httpx.Response(200, headers={"content-type": "text/html"},
                              stream=_Iter(stream()), request=request)


class _Iter(httpx.AsyncByteStream):
    def __init__(self, gen):
        self._gen = gen

    async def __aiter__(self):
        async for chunk in self._gen:
            yield chunk


@pytest.mark.asyncio
async def test_oversize_body_is_cut_mid_stream():
    """读完再判等于没有上限：一个 500MB 的响应会先把内存吃光再被礼貌地拒绝。"""
    transport = _OversizeTransport(b"x" * 1024, chunks=2000)
    cfg = reader.ReaderConfig(max_bytes=10_000)
    async with httpx.AsyncClient(transport=transport) as client:
        result = await reader._fetch_one(client, "https://news.example/a", cfg=cfg)
    assert result.status == "too_large"
    assert transport.sent < cfg.max_bytes + 4096, "必须在超限那一刻就停，不是读完再判"


@pytest.mark.asyncio
async def test_non_html_content_type_is_rejected():
    result = await _fetch(lambda req: httpx.Response(
        200, headers={"content-type": "application/pdf"}, content=b"%PDF-1.4"))
    assert result.status == "not_html"


@pytest.mark.asyncio
async def test_http_error_is_a_status_not_an_exception():
    result = await _fetch(lambda req: httpx.Response(503, text="upstream down"))
    assert result.status == "http_error" and result.http_status == 503


@pytest.mark.asyncio
async def test_timeout_translates_to_status_not_raise():
    def boom(request):
        raise httpx.ReadTimeout("slow", request=request)

    result = await _fetch(boom)
    assert result.status == "timeout"


@pytest.mark.asyncio
async def test_network_error_translates_to_status_not_raise():
    def boom(request):
        raise httpx.ConnectError("refused", request=request)

    result = await _fetch(boom)
    assert result.status == "network_error"


@pytest.mark.asyncio
async def test_empty_extraction_is_a_status_not_a_failure():
    result = await _fetch(lambda req: httpx.Response(
        200, headers={"content-type": "text/html"}, text="<body><p>首页</p></body>"))
    assert result.status == "empty" and result.body == ""


@pytest.mark.asyncio
async def test_fetch_many_never_raises_on_any_failure():
    def mixed(request):
        if request.url.path == "/boom":
            raise httpx.ConnectError("refused", request=request)
        return httpx.Response(200, headers={"content-type": "text/html"}, text=_HTML)

    cfg = reader.ReaderConfig(top_k=4)
    # `fetch_many` 自己开 client，故这里用 monkeypatch 之外的路径：直接验证它不抛。
    out = await _fetch_many_with(mixed, ["https://a.example/boom", "https://b.example/ok"],
                                 cfg=cfg)
    assert out["https://a.example/boom"].status == "network_error"
    assert out["https://b.example/ok"].status == "ok"


async def _fetch_many_with(handler, urls, *, cfg):
    """用 MockTransport 替掉 `fetch_many` 内部那个 client（它按请求创建，故只能这样注入）。"""
    real = httpx.AsyncClient

    class Patched(real):
        def __init__(self, *args, **kwargs):
            kwargs.pop("proxy", None)
            super().__init__(*args, transport=httpx.MockTransport(handler), **kwargs)

    httpx.AsyncClient = Patched
    try:
        return await reader.fetch_many(urls, cfg=cfg)
    finally:
        httpx.AsyncClient = real


@pytest.mark.asyncio
async def test_round_deadline_stops_remaining_fetches():
    """本轮墙钟预算是**硬死线**；被掐掉的目标仍然各有一行结果（否则分母会系统性变小）。"""
    class SlowTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            await asyncio.sleep(5)
            return httpx.Response(200, request=request)

    cfg = reader.ReaderConfig(round_deadline_s=1.0, top_k=2)
    real = httpx.AsyncClient

    class Patched(real):
        def __init__(self, *args, **kwargs):
            kwargs.pop("proxy", None)
            super().__init__(*args, transport=SlowTransport(), **kwargs)

    httpx.AsyncClient = Patched
    try:
        out = await reader.fetch_many(["https://a.example/1", "https://b.example/1"], cfg=cfg)
    finally:
        httpx.AsyncClient = real
    assert set(out) == {"https://a.example/1", "https://b.example/1"}
    assert all(res.status == "timeout" for res in out.values())


@pytest.mark.asyncio
async def test_max_per_domain_is_respected():
    seen: list[str] = []

    def handler(request):
        seen.append(str(request.url))
        return httpx.Response(200, headers={"content-type": "text/html"}, text=_HTML)

    cfg = reader.ReaderConfig(max_per_domain=1, top_k=5)
    await _fetch_many_with(handler, ["https://a.example/1", "https://a.example/2",
                                     "https://b.example/1"], cfg=cfg)
    assert len(seen) == 2, "同一域名一轮最多抓 max_per_domain 篇"


@pytest.mark.asyncio
async def test_cancelled_error_propagates():
    """关停纪律：`CancelledError` **绝不**被吞成一个 `network_error`。"""
    def boom(request):
        raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await _fetch(boom)


def test_reader_config_holds_no_client():
    """C-7：reader **只持配置**。挂一个长生命周期 client 上去，「不新增关停步骤」就不成立。"""
    cfg = reader.ReaderConfig()
    for value in vars(cfg).values():
        assert not isinstance(value, httpx.AsyncClient)
    assert not hasattr(cfg, "client")


def test_reader_source_carries_no_local_facts():
    """§3.7：抓取请求**零本地事实**——不带持仓、预算、账户，连 instId 都不出现。

    扫的是**去掉注释与 docstring 之后的代码**：模块 docstring 里必须写清「不带 Cookie、不带
    instId」，按原文扫会把那句纪律本身判成一次违规（按原文扫的用例迟早会被人用「改一下注释」
    绕过去，那比没有这条用例更坏）。
    """
    from app.ai_auto.intel import reader as module

    code = _code_without_docstrings(module)
    for forbidden in ("position", "budget", "instId", "avgEntry", "leverage", "Cookie"):
        assert forbidden not in code, f"reader.py 的代码里出现了本地量 {forbidden}"


def _code_without_docstrings(module) -> str:
    """模块源码 → 去掉全部注释与 docstring 的等价代码（`ast.unparse` 天然丢注释）。"""
    import ast
    from pathlib import Path

    tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if not isinstance(body, list) or not body:
            continue
        head = body[0]
        if (isinstance(head, ast.Expr) and isinstance(head.value, ast.Constant)
                and isinstance(head.value.value, str)):
            body.pop(0)
            if not body:
                body.append(ast.Pass())
    return ast.unparse(ast.fix_missing_locations(tree))
