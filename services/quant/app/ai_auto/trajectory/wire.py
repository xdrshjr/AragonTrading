"""轨迹记录的序列化原语（ai-trade-trajectory-capture §2.2.4 / §2.7.2）——纯函数、零 IO。

离线编译器（`reader` / `rewards` / `compile`）与在线采集链路**共用本模块**，故这里不得 import
任何会话、数据库或 provider 模块（RV-P2-16：编译 CLI 不连带 import recorder / db）。
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import time

SCHEMA_VERSION = 1
BLOB_REF_KEY = "$blob"


def canonical_json(obj) -> str:
    """稳定序列化：排序键、紧凑分隔、保留非 ASCII；未知对象退化为 `str`（不因类型抛错）。"""
    return json.dumps(obj, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), default=str)


def sha256_text(text: str) -> str:
    """UTF-8 字节的 sha256 十六进制摘要（blob 的内容寻址键）。"""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def block_wire(block) -> dict:
    """归一化消息块 → Anthropic 风格 dict；未知块尽力保真，绝不丢弃。"""
    kind = getattr(block, "type", None)
    if kind == "text":
        return {"type": "text", "text": getattr(block, "text", "")}
    if kind == "tool_use":
        return {"type": "tool_use", "id": getattr(block, "id", None),
                "name": getattr(block, "name", None), "input": getattr(block, "input", None)}
    if kind == "tool_result":
        return {"type": "tool_result", "tool_use_id": getattr(block, "tool_use_id", None),
                "content": getattr(block, "content", None),
                "is_error": bool(getattr(block, "is_error", False))}
    if isinstance(block, dict):
        return dict(block)
    if dataclasses.is_dataclass(block) and not isinstance(block, type):
        return dataclasses.asdict(block)
    return {"type": "unknown", "repr": repr(block)}


def message_wire(message) -> dict:
    """归一化消息 → `{"role", "content": [...]}`。

    `role` **保留归一化值**（含 `tool`）：翻译成 provider 的 user 轮是导出时的约定，由编译器在
    `report.json.conventions` 里声明，采集层不替下游选定训练框架。
    """
    if isinstance(message, dict):
        role, content = message.get("role"), message.get("content")
    else:
        role, content = getattr(message, "role", None), getattr(message, "content", None)
    if isinstance(content, str):
        return {"role": role, "content": [{"type": "text", "text": content}]}
    return {"role": role, "content": [block_wire(block) for block in (content or [])]}


def tools_wire(tools) -> list[dict]:
    """工具声明 → `[{"name", "description", "input_schema"}]`（与 Anthropic 请求体同形）。"""
    out = []
    for tool in tools or []:
        read = tool.get if isinstance(tool, dict) else (lambda key, t=tool: getattr(t, key, None))
        out.append({"name": read("name"), "description": read("description"),
                    "input_schema": read("input_schema")})
    return out


def freeze_request(system, messages, tools) -> tuple[str, tuple[str, ...], str]:
    """把一次调用的完整请求**逐条**冻结成 JSON 串。

    入队即冻结：`decider._context` 里的对象会被后续轮次继续引用，存引用 = 落盘时写下的可能是
    之后的状态。逐条序列化使写线程能按条做内容寻址去重。

    Returns:
        `(system 的 JSON 串, 每条 message 的 JSON 串, tools 的 JSON 串)`。
    """
    frozen_messages = tuple(canonical_json(message_wire(m)) for m in (messages or []))
    return (canonical_json(system if system is not None else ""), frozen_messages,
            canonical_json(tools_wire(tools)))


def decision_wire(decision) -> dict | None:
    """`TradeDecision` → dict（`dataclasses.asdict`：字段增删自动跟随，不存在逐键手抄的漂移面）。"""
    if decision is None:
        return None
    if dataclasses.is_dataclass(decision) and not isinstance(decision, type):
        return dataclasses.asdict(decision)
    return {"repr": repr(decision)}


def origin(epoch: str, group_id: str, inst_id: str, run_id: int | None) -> dict:
    """信封里「这条记录属于谁」的四个键（同一 epoch 内对同一标的恒定）。"""
    return {"epoch": epoch, "groupId": group_id, "instId": inst_id, "runId": run_id}


def envelope(kind: str, seq: int | None, owner: dict) -> dict:
    """公共信封（§2.7.2）：`{v, kind, seq, wallTs}` 加上 `origin(...)` 的四个键。

    `seq=None` 只用于写线程生成的记录（`segment_open` / `segment_close` / `blob` /
    `gap{scope:"record"}`）：它们不占用通道序号，编译器的连续性检查只看 `seq` 非空的记录。
    """
    return {"v": SCHEMA_VERSION, "kind": kind, "seq": seq, "wallTs": round(time.time(), 3),
            **owner}


def join_objects(first: str, second: str) -> str:
    """合并两个已序列化的 JSON 对象（信封 + 记录体）；调用方保证键不重复。

    在热路径与写线程上都避免「解析 → 合并 → 再序列化」一段可能数十 KB 的正文。
    """
    if second == "{}":
        return first
    if first == "{}":
        return second
    return first[:-1] + "," + second[1:]


def splice_value(head: str, key: str, value_json: str) -> str:
    """把一个已序列化的值作为新键拼进一个已序列化的 JSON 对象末尾。"""
    member = json.dumps(key) + ":" + value_json
    return "{" + member + "}" if head == "{}" else head[:-1] + "," + member + "}"
