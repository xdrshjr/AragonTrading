"""采集点一：LLM 客户端的透明装饰器 `RecordingLlmClient`（ai-trade-trajectory-capture §2.2）。

**唯一**同时看得见「完整请求」与「完整流式输出」的位置是每一次 `LLMClient.stream(...)` 调用本身：
扩展思考从不在服务端累积，研判与反思在决策器里被截到 2 万字，重试与追问的中间调用只活在一次函数
调用的局部变量里。故采集点是包在客户端外面的装饰器，而不是往决策器里再加读点——本特性一行都不改
`decider.py`。

`stream` 的五条硬约束（用例 T-2 以 AST 冻结）：恰一个 `async for`、零个 `await`、恰一个 `yield`；
转发的是同一个事件对象；每个 `except` 以 `raise` 结尾；`finally` 里不 await；`error` 事件即终局。
"""
from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, replace
from functools import lru_cache
from typing import TYPE_CHECKING

from ...agent.llm.retry import normalize_usage
from .directory import THINKING_CAPTURED_PROVIDERS

if TYPE_CHECKING:
    from .channel import TrajectoryChannel

SITE_DECISION = "decision"
SITE_REFLECTION = "reflection"
SITE_SUMMARY = "summary"
SITE_DISTILL = "experience_distill"
SITE_OTHER = "other"
# 只有这三类会挂到当前回合；其余记为 `scope.phase = "out_of_round"`。
ROUND_SITES = frozenset({SITE_DECISION, SITE_REFLECTION, SITE_SUMMARY})

_BODY_EXCLUDED = frozenset({"system", "messages", "tools"})
_PARSE_ERROR_MAX = 500
_ERROR_MESSAGE_MAX = 2000


class RecordingLlmClient:
    """透明装饰器：原样转发 inner 的每个流事件，同时把完整请求与完整响应冻结成一条 `llm_call` 记录。"""

    def __init__(self, inner, channel: "TrajectoryChannel") -> None:
        self._inner = inner
        self._channel = channel
        self.provider = getattr(inner, "provider", "anthropic")   # decider._note_stream_outcome 读它

    def __getattr__(self, name):
        """私有字段（`_model` 等）照常可读：决策器与测试替身零感知。"""
        # RV-P2-1：`copy` / `pickle` 会在 `__init__` 之前查属性，不守卫即无限递归。
        if name in ("_inner", "_channel") or name.startswith("__"):
            raise AttributeError(name)
        return getattr(self._inner, name)

    async def stream(self, *, system, messages, tools):
        """与 `LLMClient.stream` 同签名；消费方看到的事件对象身份与顺序逐个不变。"""
        capture = self._channel.open_call(system=system, messages=messages, tools=tools,
                                          params=llm_params_of(self._inner, tools))
        status = "closed_early"
        try:
            async for event in self._inner.stream(system=system, messages=messages, tools=tools):
                if capture is not None:
                    capture.observe(event)
                yield event
            status = "completed"
        except asyncio.CancelledError:
            status = "cancelled"
            raise
        except GeneratorExit:
            status = "closed_early"
            raise
        except BaseException as exc:
            status = "exception"
            if capture is not None:
                capture.note_exception(exc)
            raise
        finally:
            if capture is not None:
                capture.finish(status)


def wrap_llm_clients(clients, channel: "TrajectoryChannel"):
    """三个调用点的客户端各包一层（`AiTradeLlmClients` 是 frozen dataclass ⇒ `replace`）。

    off 档三者可能是三个不同对象（`llm_budget` 三次调工厂），故逐个包装、不做 `is` 去重。LLM 的
    `_api_key` 只登记进写线程的剔除表，**绝不**进任何记录。
    """
    for inner in (clients.decision, clients.reflection, clients.summary):
        channel.register_secret(getattr(inner, "_api_key", None))
    return replace(clients, decision=RecordingLlmClient(clients.decision, channel),
                   reflection=RecordingLlmClient(clients.reflection, channel),
                   summary=RecordingLlmClient(clients.summary, channel))


def classify_site(tools) -> str:
    """调用点分类：**按工具名，不按客户端**（会话末蒸馏经 `decider.llm` 复用决策客户端）。"""
    names = {tool.get("name") if isinstance(tool, dict) else getattr(tool, "name", None)
             for tool in tools or []}
    names.discard(None)
    if not names:
        return SITE_SUMMARY                     # `_stream_summary` 恒传 tools=[]
    decision, reflection, distill = _tool_names()
    if decision in names:
        return SITE_DECISION
    if reflection in names:
        return SITE_REFLECTION
    if distill in names:
        return SITE_DISTILL
    return SITE_OTHER


@lru_cache(maxsize=1)
def _tool_names() -> tuple[str, str, str]:
    """三个工具名**从真源 import**；函数体内延迟导入以避免 `trajectory → experience → …` 的导入环。"""
    from ..experience.distill import DISTILL_TOOL_NAME
    from ..prompts import DECISION_TOOL_NAME, REFLECTION_TOOL_NAME

    return DECISION_TOOL_NAME, REFLECTION_TOOL_NAME, DISTILL_TOOL_NAME


def llm_params_of(inner, tools) -> dict:
    """真正发出去的采样参数（RV11：以 provider 自己的 `_body` 为唯一真源；绝不抛）。

    Anthropic 开扩展思考时 `_body` 强制 `temperature=1.0` 并删掉 `top_p`，OpenAI 端另带
    `tool_choice` / `stream_options`——逐个读私有字段会把行为策略的温度记错，而强化学习的重要性
    采样与离策略校正恰恰依赖它。`_body` 不含 `_api_key` 与 `_base_url`。
    """
    try:
        body = _provider_body(inner, tools)
        if body is not None:
            params = {key: value for key, value in body.items() if key not in _BODY_EXCLUDED}
            source = "provider_body"
        else:
            params = {"model": getattr(inner, "_model", None) or getattr(inner, "model", None),
                      "max_tokens": getattr(inner, "_max_tokens", None),
                      "temperature": getattr(inner, "_temperature", None),
                      "top_p": getattr(inner, "_top_p", None)}
            source = "attributes"
        budget = int(getattr(inner, "_thinking_budget", 0) or 0)
        provider = str(getattr(inner, "provider", "anthropic") or "anthropic")
    except Exception:  # noqa: BLE001 —— 参数只是记录的一部分，读不出来绝不影响调用本身
        params, source, budget, provider = {}, "unavailable", 0, "unknown"
    return {"requestParams": params, "paramsSource": source, "provider": provider,
            "thinkingBudgetTokens": budget}


def _provider_body(inner, tools) -> dict | None:
    build = getattr(inner, "_body", None)
    if not callable(build):
        return None
    try:
        body = build("", [], list(tools or []))
    except Exception:  # noqa: BLE001 —— 替身的 `_body` 可能是任何东西 ⇒ 回落逐字段读取
        return None
    return body if isinstance(body, dict) else None


@dataclass(frozen=True, slots=True)
class CallOpening:
    """开流那一刻就确定的事实：调用 id、调用点、回合归属、冻结的请求、采样参数与冻结耗时。"""

    call_id: str
    site: str
    placement: dict
    frozen: tuple
    params: dict
    serialize_ms: float


class CallCapture:
    """一次 LLM 调用的观察与装配（同步、绝不抛、幂等收尾）。"""

    def __init__(self, channel: "TrajectoryChannel", opening: CallOpening) -> None:
        self._channel = channel
        self.opening = opening
        self._t0 = time.monotonic()
        self._started_wall = round(time.time(), 3)
        self._first_event_ms: float | None = None
        self._thinking: list[str] = []
        self._text: list[str] = []
        self._tools: list[dict] = []
        self._tools_by_id: dict[str, dict] = {}
        self._last_tool: dict | None = None
        self._stop_reason = None
        self._usage = None
        self._saw_done = False
        self._error: dict | None = None
        self._exception: dict | None = None
        self._counts: dict[str, int] = {}
        self._args_without_id = 0
        self._done = False

    def observe(self, event) -> None:
        """累积一个流事件（§2.2.5）。`error` 事件即终局：立刻收尾入队，不等生成器被 GC 终结。"""
        try:
            self._observe(event)
        except Exception as exc:  # noqa: BLE001 —— 观察失败只计数，事件照常转发
            self._channel.note_error(exc)

    def note_exception(self, exc: BaseException) -> None:
        """记下内层抛出的异常（状态 `exception`）。"""
        try:
            self._exception = {"code": "exception", "type": type(exc).__name__,
                               "message": str(exc)[:_ERROR_MESSAGE_MAX]}
        except Exception as inner:  # noqa: BLE001 —— `str(exc)` 本身可能抛
            self._channel.note_error(inner)

    def finish(self, status: str) -> None:
        """幂等收尾：装配记录并交给通道入队。"""
        try:
            self._finish(status)
        except Exception as exc:  # noqa: BLE001 —— 见类 docstring
            self._channel.note_error(exc)

    def _observe(self, event) -> None:
        if self._done:
            return
        if self._first_event_ms is None:
            self._first_event_ms = round((time.monotonic() - self._t0) * 1000.0, 3)
        kind = getattr(event, "kind", None)
        self._counts[str(kind)] = self._counts.get(str(kind), 0) + 1
        handler = _HANDLERS.get(kind)
        if handler is not None:
            handler(self, event)

    def _finish(self, status: str) -> None:
        if self._done:
            return
        self._done = True
        final = "error" if self._error is not None else status
        self._channel.emit_call(self.opening, self._assemble(final))

    def _assemble(self, status: str) -> dict:
        params = self.opening.params
        provider = params.get("provider")
        return {
            "callId": self.opening.call_id, "site": self.opening.site,
            "scope": self.opening.placement, "params": params,
            "response": {"thinking": "".join(self._thinking), "text": "".join(self._text),
                         "toolUses": [_tool_wire(tool) for tool in self._tools],
                         "stopReason": self._stop_reason, "usage": self._usage,
                         "normalizedUsage": normalize_usage(provider or "", self._usage),
                         "sawMessageDone": self._saw_done},
            "timing": {"startedWallTs": self._started_wall, "firstEventMs": self._first_event_ms,
                       "durationMs": round((time.monotonic() - self._t0) * 1000.0, 3),
                       "serializeMs": round(self.opening.serialize_ms, 3)},
            "status": status,
            # RV-P2-7：摘要调用见 message_done 即被消费方 break，它的 closed_early 是完整响应。
            "responseComplete": status == "completed" or (status == "closed_early"
                                                          and self._saw_done),
            "error": self._error or (self._exception if status == "exception" else None),
            "eventCounts": dict(self._counts), "argsDeltaWithoutId": self._args_without_id,
            "capabilities": {"thinkingCaptured": provider in THINKING_CAPTURED_PROVIDERS,
                             "thinkingEnabled": int(params.get("thinkingBudgetTokens") or 0) > 0},
        }


def _on_thinking(capture: CallCapture, event) -> None:
    capture._thinking.append(event.text or "")


def _on_text(capture: CallCapture, event) -> None:
    capture._text.append(event.text or "")


def _on_tool_start(capture: CallCapture, event) -> None:
    tool = {"id": event.id, "name": event.name, "parts": [], "ended": False}
    capture._tools.append(tool)
    if event.id is not None:
        capture._tools_by_id[event.id] = tool
    capture._last_tool = tool


def _on_tool_args(capture: CallCapture, event) -> None:
    """OpenAI 流式里后续分片只带 index（`id=None`，§七 R7）⇒ 归到最近开启的工具并计数。"""
    tool = capture._tools_by_id.get(event.id) if event.id is not None else None
    if event.id is None:
        capture._args_without_id += 1
        tool = capture._last_tool
    elif tool is None:                          # 没见过开始事件的 id：单列一个工具，绝不丢分片
        _on_tool_start(capture, event)
        tool = capture._last_tool
    if tool is not None:
        tool["parts"].append(event.partial_json or "")


def _on_tool_end(capture: CallCapture, event) -> None:
    tool = capture._tools_by_id.get(event.id) if event.id is not None else capture._last_tool
    if tool is not None:
        tool["ended"] = True


def _on_done(capture: CallCapture, event) -> None:
    # 与 `decider._stream_decision` 同一「非空者胜」口径：首个 message_done 之后仍可能有 usage 帧。
    capture._stop_reason = event.stop_reason or capture._stop_reason
    capture._usage = event.usage or capture._usage
    capture._saw_done = True


def _on_error(capture: CallCapture, event) -> None:
    capture._error = {"code": event.code, "message": event.message}
    capture._finish("error")


_HANDLERS = {"thinking_delta": _on_thinking, "text_delta": _on_text,
             "tool_use_start": _on_tool_start, "tool_use_args_delta": _on_tool_args,
             "tool_use_end": _on_tool_end, "message_done": _on_done, "error": _on_error}


def _tool_wire(tool: dict) -> dict:
    """工具调用 → `{id, name, argumentsJson, input, parseError, ended}`（入参原文逐字节保留）。"""
    arguments = "".join(tool["parts"])
    try:
        parsed = json.loads(arguments or "{}")
        error = None if isinstance(parsed, dict) else "arguments is not a JSON object"
    except (ValueError, TypeError) as exc:
        parsed, error = None, str(exc)[:_PARSE_ERROR_MAX]
    return {"id": tool["id"], "name": tool["name"], "argumentsJson": arguments,
            "input": parsed if error is None else None, "parseError": error,
            "ended": tool["ended"]}
