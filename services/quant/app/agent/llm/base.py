"""provider 无关的归一化消息 / 工具 / 流事件类型与 LLMClient 协议 (spec §2.2)。

loop 与工具层只认这套归一化类型；各 provider 把原生 SSE 翻译成 `StreamEvent`，
把归一化 `LlmMessage`/`ToolSchema` 转成自家请求格式——故上层对 provider 完全无感。
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Literal, Protocol, Union

Role = Literal["user", "assistant", "tool"]


# ---------------- 归一化消息块（内部表示） ----------------
@dataclass
class TextBlock:
    text: str
    type: str = "text"


@dataclass
class ToolUseBlock:
    id: str
    name: str
    input: dict
    type: str = "tool_use"


@dataclass
class ToolResultBlock:
    tool_use_id: str
    content: str
    is_error: bool = False
    type: str = "tool_result"


Block = Union[TextBlock, ToolUseBlock, ToolResultBlock]


@dataclass
class LlmMessage:
    """归一化消息：role + 块序列。tool 角色仅承载 tool_result 块（P1-B）。"""

    role: Role
    content: list[Block] = field(default_factory=list)


@dataclass
class ToolSchema:
    """工具声明（各 provider 各自转 input_schema↔parameters）。"""

    name: str
    description: str
    input_schema: dict


# ---------------- 归一化流事件 ----------------
StreamEventKind = Literal[
    "text_delta",
    "thinking_delta",
    "tool_use_start",
    "tool_use_args_delta",
    "tool_use_end",
    "message_done",
    "error",
]


@dataclass
class StreamEvent:
    """单一可辨识联合：按 `kind` 取用对应字段（其余为 None）。"""

    kind: StreamEventKind
    text: str | None = None          # text_delta / thinking_delta
    id: str | None = None            # tool_use_*
    name: str | None = None          # tool_use_start
    partial_json: str | None = None  # tool_use_args_delta
    stop_reason: str | None = None   # message_done
    usage: dict | None = None        # message_done
    code: str | None = None          # error
    message: str | None = None       # error


class LLMClient(Protocol):
    """流式 LLM 客户端协议。实现见 anthropic_client / openai_client。"""

    async def stream(
        self,
        *,
        system: str,
        messages: list[LlmMessage],
        tools: list[ToolSchema],
    ) -> AsyncIterator[StreamEvent]:
        """产出归一化流事件序列；非 2xx / 解析失败须 emit `error` 而非抛裸异常。"""
        ...
