"""LLM 传输层重试编排 + usage 归一（两 provider 复用的纯逻辑）(spec §2.A H1 / §2.D H5)。

把"建立连接 + 读取响应状态码"的 pre-stream 阶段包进有界指数退避重试；**一旦开始产出
SSE 事件即不再重试**（铁律：防重复输出，R1）。退避用 `asyncio.sleep`，可被 cancel /
`_drain` 中断（RN-6）。usage 归一确保两 provider 的原始 snake_case 字段统一为对外
camelCase 契约 `{inputTokens, outputTokens}`（RN-2）。
"""
from __future__ import annotations

import asyncio
import random
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass

import httpx

from ...okx.call_stats import FAMILY_LLM, get_call_stats
from .base import StreamEvent

# 可重试的 HTTP 状态（限流 / 网关瞬时故障）。
RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})
# 鉴权失败：专属错误码、零重试（H6）。
_AUTH_STATUS = frozenset({401, 403})
# 可重试的网络异常（连接 / 读超时 / 协议中断）。
_RETRYABLE_EXC = (
    httpx.ConnectError,
    httpx.ConnectTimeout,
    httpx.ReadTimeout,
    httpx.RemoteProtocolError,
)
_JITTER_MS = 250
# message_done 原始 usage → 对外 camelCase 的字段映射（缺失键回退 None）。
_USAGE_FIELD_MAP = {
    "anthropic": ("input_tokens", "output_tokens"),
    "openai": ("prompt_tokens", "completion_tokens"),
}


@dataclass
class RetryConfig:
    """pre-stream 重试参数（来自 Settings 的 AGENT_* 字段）。"""

    max_retries: int = 2
    base_ms: int = 500
    max_ms: int = 8000


def backoff_delay_ms(attempt: int, base_ms: int, max_ms: int) -> int:
    """第 ``attempt`` 次（0-based）的退避毫秒（指数、封顶，不含抖动，便于确定性单测）。"""
    return min(base_ms * (2 ** attempt), max_ms)


def parse_retry_after_ms(value: str | None, max_ms: int) -> int | None:
    """解析 ``Retry-After``：**仅采纳整数秒**形式并封顶 ``max_ms``（RN-5）。

    HTTP-date 等非整数形式解析失败时返回 ``None``（调用方回退到计算退避，绝不抛错）。
    """
    if not value:
        return None
    raw = value.strip()
    if not raw.isdigit():
        return None
    return min(int(raw) * 1000, max_ms)


def is_retryable_status(status: int) -> bool:
    """该 HTTP 状态码是否属于可重试集（限流 / 网关瞬时故障）。"""
    return status in RETRYABLE_STATUS


def normalize_usage(provider: str, raw: dict | None) -> dict | None:
    """把 provider 原始 usage 归一为对外 camelCase ``{inputTokens, outputTokens}``（RN-2）。

    Args:
        provider: ``anthropic`` | ``openai``（决定原始字段名）。
        raw: provider 原始 usage dict，或 ``None``。

    Returns:
        归一后的 ``{"inputTokens": int|None, "outputTokens": int|None}``；``raw`` 为空或
        两字段均缺失时返回 ``None``（前端 / 日志容忍缺失）。**绝不外泄原始 snake_case 字段名。**
    """
    if not raw:
        return None
    in_field, out_field = _USAGE_FIELD_MAP.get(
        (provider or "").lower(), _USAGE_FIELD_MAP["anthropic"])
    in_tokens = raw.get(in_field)
    out_tokens = raw.get(out_field)
    if in_tokens is None and out_tokens is None:
        return None
    return {"inputTokens": in_tokens, "outputTokens": out_tokens}


@dataclass
class _AttemptResult:
    """单次尝试的控制信号：是否需重试 / 退避覆盖值 / 是否已开始流式。"""

    should_retry: bool = False
    retry_after_ms: int | None = None
    streamed: bool = False


def _auth_error(status: int) -> StreamEvent:
    return StreamEvent(
        kind="error", code="AGENT_AUTH_ERROR",
        message=(f"模型密钥无效 / 过期 / 无权限（HTTP {status}）。"
                 "请更新 .env 或 userData/agent.env 中的 AGENT_API_KEY 后重启应用。"))


def _http_error(status: int, text: str) -> StreamEvent:
    return StreamEvent(kind="error", code="LLM_HTTP_ERROR",
                       message=f"HTTP {status}: {text[:400]}")


def _network_error(exc: Exception) -> StreamEvent:
    return StreamEvent(kind="error", code="LLM_NETWORK_ERROR", message=str(exc))


async def _pre_stream_gate(resp: httpx.Response, attempt: int, config: RetryConfig,
                          result: _AttemptResult) -> StreamEvent | None:
    """pre-stream 状态闸门：放行 / 鉴权错误 / 安排重试 / 终止错误（返回需 emit 的错误事件）。"""
    status = resp.status_code
    if status < 400:
        return None
    if status in _AUTH_STATUS:
        return _auth_error(status)
    if is_retryable_status(status) and attempt < config.max_retries:
        result.should_retry = True
        result.retry_after_ms = parse_retry_after_ms(resp.headers.get("Retry-After"), config.max_ms)
        return None
    text = (await resp.aread()).decode("utf-8", "replace")
    return _http_error(status, text)


def _note_llm_call(resp: httpx.Response | None, exc: Exception | None = None) -> None:
    """把这一次 LLM 往返记进进程内调用统计（silent-degradation-repair 轴 D-3 · 接线点 4/5）。

    **落在两个 provider 的共用出口上，而不是各自的 client 里**：``anthropic_client`` 与
    ``openai_client`` 的响应都只经过 ``_run_attempt`` 这一处，在这里接线既覆盖了 spec 点名的
    那两个出口，又让未来第三个 provider 不可能被漏掉——一份「漏了一个 provider」的统计表，
    在「谁在拒我」这个问题上给出的答案是错的，而错的答案比没有答案更贵。

    键是 ``(POST, host)`` 而**不是** path：不同厂商的 path 形状不同
    （``/api/paas/v4/chat/completions`` vs ``/v1/messages``），按 path 归一会把同一个提供商
    拆成好几格。生产读数 ``bigmodel · 529×779`` / ``kimi · 403×408`` 今天**只**来自 `httpx`
    的 `INFO` 行，轴 D-2 把它压掉之后，这张表是它们唯一的宿主。
    """
    # **整段吞异常**（与 `CallStats.note` 自己那条纪律同源）：它挂在每一次 LLM 往返的出口上。
    # `httpx.Response.request` 在未绑定时**会抛 RuntimeError**（不是返回 None），而流式响应的
    # 替身更可能连这个属性都没有——一个统计函数把决策轮打断，是比「统计缺一格」贵得多的失败。
    try:
        source = resp if resp is not None else exc
        request = getattr(source, "request", None)
        url = str(getattr(request, "url", "") or "")
        method = str(getattr(request, "method", "POST") or "POST")
        get_call_stats().note(method, url, family=FAMILY_LLM,
                              status=getattr(resp, "status_code", None),
                              transport_fail=resp is None)
    except Exception:  # noqa: BLE001 —— 见上：统计绝不成为一次真实往返失败的原因
        return


async def _run_attempt(make_request_cm: Callable[[], AbstractAsyncContextManager],
                       parse_sse: Callable[[AsyncIterator[str]], AsyncIterator[StreamEvent]],
                       attempt: int, config: RetryConfig,
                       result: _AttemptResult) -> AsyncIterator[StreamEvent]:
    """单次尝试：成功则流式 yield；可重试错误置 ``result.should_retry``（不 yield）。"""
    try:
        async with make_request_cm() as resp:
            _note_llm_call(resp)
            gate_error = await _pre_stream_gate(resp, attempt, config, result)
            if gate_error is not None:
                yield gate_error
                return
            if result.should_retry:
                return
            async for event in parse_sse(resp.aiter_lines()):
                result.streamed = True
                yield event
    except _RETRYABLE_EXC as exc:
        _note_llm_call(None, exc)
        # 已开始流式 / 重试已穷尽 → 如实报错（铁律：流式后绝不重试，防重复输出）。
        if result.streamed or attempt >= config.max_retries:
            yield _network_error(exc)
        else:
            result.should_retry = True
    except httpx.HTTPError as exc:
        _note_llm_call(None, exc)
        yield _network_error(exc)


async def _sleep_backoff(attempt: int, config: RetryConfig, retry_after_ms: int | None) -> None:
    """退避等待（可被 cancel / `_drain` 中断，断连时立即让出，RN-6）。"""
    base = retry_after_ms if retry_after_ms is not None \
        else backoff_delay_ms(attempt, config.base_ms, config.max_ms)
    await asyncio.sleep((base + random.uniform(0, _JITTER_MS)) / 1000.0)


async def open_stream_with_retry(
    *, make_request_cm: Callable[[], AbstractAsyncContextManager],
    parse_sse: Callable[[AsyncIterator[str]], AsyncIterator[StreamEvent]],
    config: RetryConfig,
) -> AsyncIterator[StreamEvent]:
    """pre-stream 有界重试编排：成功流式即直通；可重试错误退避后重试，穷尽则 emit 错误。

    Args:
        make_request_cm: 无参工厂，每次尝试新建一次 ``httpx`` 流式请求的异步上下文管理器。
        parse_sse: 把响应行迭代器翻译成归一化 ``StreamEvent`` 的解析器。
        config: 重试参数。
    """
    for attempt in range(config.max_retries + 1):
        result = _AttemptResult()
        async for event in _run_attempt(make_request_cm, parse_sse, attempt, config, result):
            yield event
        if not result.should_retry:
            return
        await _sleep_backoff(attempt, config, result.retry_after_ms)
