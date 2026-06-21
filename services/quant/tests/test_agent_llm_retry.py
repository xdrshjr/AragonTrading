"""LLM pre-stream 有界重试编排 + 退避纯函数单测（注入桩请求上下文，不依赖网络）。

覆盖：429→200 / 503→503→200 / ConnectError→200 自动重试且只产一份事件；已流式后断连
不重试（防重复输出，R1）；401/403→AGENT_AUTH_ERROR 零重试；退避指数 / 抖动上界 /
Retry-After 整数秒封顶 + HTTP-date 回退不抛错（RN-5）。
"""
from __future__ import annotations

from contextlib import asynccontextmanager

import httpx

from app.agent.llm.base import StreamEvent
from app.agent.llm.retry import (
    RetryConfig,
    backoff_delay_ms,
    is_retryable_status,
    open_stream_with_retry,
    parse_retry_after_ms,
)


class _FakeResp:
    """最小响应桩：status_code + 行迭代 + aread + headers。"""

    def __init__(self, status_code: int, *, lines=None, body: str = "err", headers=None):
        self.status_code = status_code
        self._lines = lines or []
        self._body = body
        self.headers = headers or {}

    async def aiter_lines(self):
        for line in self._lines:
            yield line

    async def aread(self) -> bytes:
        return self._body.encode("utf-8")


def _fast_config() -> RetryConfig:
    # base_ms=0/max_ms=0 → 退避≈0（仅 ≤250ms 抖动），保证测试快速。
    return RetryConfig(max_retries=2, base_ms=0, max_ms=0)


def _scripted_cm(script: list):
    """按 script 逐次产出请求上下文：每项为 _FakeResp，或要在进入时 raise 的异常实例。"""
    calls = {"n": 0}

    @asynccontextmanager
    async def make_cm():
        idx = calls["n"]
        calls["n"] += 1
        item = script[min(idx, len(script) - 1)]
        if isinstance(item, Exception):
            raise item
        yield item

    return make_cm, calls


async def _parse_lines(line_iter):
    """把每行作为一个 text_delta（够测重试编排）。"""
    async for line in line_iter:
        yield StreamEvent(kind="text_delta", text=line)


async def _collect(make_cm):
    return [ev async for ev in open_stream_with_retry(
        make_request_cm=make_cm, parse_sse=_parse_lines, config=_fast_config())]


async def test_retries_429_then_succeeds_once():
    make_cm, calls = _scripted_cm([_FakeResp(429), _FakeResp(200, lines=["hello"])])
    events = await _collect(make_cm)
    assert calls["n"] == 2
    assert [e.kind for e in events] == ["text_delta"]  # 只产一份事件，不重复
    assert events[0].text == "hello"


async def test_retries_503_twice_then_succeeds():
    make_cm, calls = _scripted_cm([_FakeResp(503), _FakeResp(503), _FakeResp(200, lines=["ok"])])
    events = await _collect(make_cm)
    assert calls["n"] == 3
    assert [e.text for e in events] == ["ok"]


async def test_retries_connect_error_then_succeeds():
    make_cm, calls = _scripted_cm([httpx.ConnectError("down"), _FakeResp(200, lines=["up"])])
    events = await _collect(make_cm)
    assert calls["n"] == 2
    assert [e.text for e in events] == ["up"]


async def test_no_retry_after_streaming_started():
    """已 yield text_delta 后中途断连 → 不重试、emit LLM_NETWORK_ERROR（铁律 R1）。"""

    async def parse_then_raise(line_iter):
        yield StreamEvent(kind="text_delta", text="partial")
        raise httpx.ReadTimeout("mid-stream drop")

    calls = {"n": 0}

    @asynccontextmanager
    async def make_cm():
        calls["n"] += 1
        yield _FakeResp(200, lines=["x"])

    events = [ev async for ev in open_stream_with_retry(
        make_request_cm=make_cm, parse_sse=parse_then_raise, config=_fast_config())]
    assert calls["n"] == 1  # 不重试
    assert events[0].kind == "text_delta"
    assert events[-1].kind == "error" and events[-1].code == "LLM_NETWORK_ERROR"


async def test_auth_error_zero_retry():
    for status in (401, 403):
        make_cm, calls = _scripted_cm([_FakeResp(status)])
        events = await _collect(make_cm)
        assert calls["n"] == 1  # 鉴权失败零重试
        assert events == [events[0]]
        assert events[0].kind == "error" and events[0].code == "AGENT_AUTH_ERROR"


async def test_non_retryable_4xx_emits_http_error_no_retry():
    make_cm, calls = _scripted_cm([_FakeResp(400, body="bad body")])
    events = await _collect(make_cm)
    assert calls["n"] == 1
    assert events[0].code == "LLM_HTTP_ERROR" and "400" in events[0].message


async def test_retry_exhausted_emits_http_error():
    make_cm, calls = _scripted_cm([_FakeResp(429), _FakeResp(429), _FakeResp(429)])
    events = await _collect(make_cm)
    assert calls["n"] == 3  # max_retries=2 → 3 次尝试
    assert events[-1].code == "LLM_HTTP_ERROR"


# ---------------- 退避 / Retry-After 纯函数 ----------------
def test_backoff_is_exponential_and_capped():
    assert backoff_delay_ms(0, 500, 8000) == 500
    assert backoff_delay_ms(1, 500, 8000) == 1000
    assert backoff_delay_ms(2, 500, 8000) == 2000
    assert backoff_delay_ms(10, 500, 8000) == 8000  # 封顶


def test_parse_retry_after_integer_seconds_only():
    assert parse_retry_after_ms("3", 8000) == 3000
    assert parse_retry_after_ms("  5 ", 8000) == 5000
    assert parse_retry_after_ms("99999", 8000) == 8000  # 封顶
    # HTTP-date 等非整数 → None（回退计算退避，不抛错）
    assert parse_retry_after_ms("Wed, 21 Oct 2015 07:28:00 GMT", 8000) is None
    assert parse_retry_after_ms("", 8000) is None
    assert parse_retry_after_ms(None, 8000) is None


def test_is_retryable_status():
    assert is_retryable_status(429) and is_retryable_status(503)
    assert not is_retryable_status(400) and not is_retryable_status(401)
