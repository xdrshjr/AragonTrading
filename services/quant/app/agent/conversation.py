"""消息序列化：归一化 block ↔ 存储/前端 dict ↔ DB 行 (spec §4.2 / §5.1)。

存储与前端共用同一份 camelCase block 形状，使 resume（重建内部 messages）与 history
重放（下发前端）两条链路共用同一无损序列（P1-B）。
"""
from __future__ import annotations

import json

from ..db.models import AgentMessage
from .llm.base import Block, LlmMessage, TextBlock, ToolResultBlock, ToolUseBlock


def block_to_dict(block: Block) -> dict:
    """归一化 block → 存储/前端 dict（camelCase）。"""
    if isinstance(block, TextBlock):
        return {"type": "text", "text": block.text}
    if isinstance(block, ToolUseBlock):
        return {"type": "tool_use", "id": block.id, "name": block.name, "input": block.input}
    if isinstance(block, ToolResultBlock):
        result = {
            "type": "tool_result", "toolUseId": block.tool_use_id,
            "ok": not block.is_error,
            "summary": (block.display_summary if block.display_summary
                        is not None else block.content),
        }
        if block.display_summary is not None:
            result["content"] = block.content
        if block.display_payload is not None:
            result["payload"] = block.display_payload
        if block.code is not None:
            result["code"] = block.code
        return result
    raise TypeError(f"unknown block: {block!r}")


def dict_to_block(data: dict) -> Block:
    """存储/前端 dict → 归一化 block。"""
    btype = data.get("type")
    if btype == "text":
        return TextBlock(text=data.get("text", ""))
    if btype == "tool_use":
        return ToolUseBlock(id=data.get("id", ""), name=data.get("name", ""),
                            input=data.get("input") or {})
    if btype == "tool_result":
        return ToolResultBlock(tool_use_id=data.get("toolUseId", ""),
                               content=data.get("content", data.get("summary", "")),
                               is_error=not data.get("ok", True),
                               display_payload=data.get("payload"),
                               display_summary=(data.get("summary")
                                                if "content" in data else None),
                               code=data.get("code"))
    raise ValueError(f"unknown block dict: {data!r}")


def blocks_to_json(blocks: list[Block]) -> str:
    return json.dumps([block_to_dict(b) for b in blocks], ensure_ascii=False)


def row_to_llm_message(row: AgentMessage) -> LlmMessage:
    try:
        dicts = json.loads(row.content_json or "[]")
    except json.JSONDecodeError:
        dicts = []
    return LlmMessage(role=row.role, content=[dict_to_block(d) for d in dicts])


def row_to_client_dict(row: AgentMessage) -> dict:
    """DB 行 → 前端 AgentMessage（id/role/ts/blocks）。"""
    try:
        blocks = json.loads(row.content_json or "[]")
    except json.JSONDecodeError:
        blocks = []
    return {"id": row.id, "role": row.role, "ts": row.created_ts, "blocks": blocks}
