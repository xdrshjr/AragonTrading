"""账户工具结果在模型、历史和界面之间保留各自语义。"""
import json

import pytest

from app.agent.conversation import block_to_dict, dict_to_block
from app.agent.llm.base import ToolResultBlock
from app.agent.tools.base import Tool, ToolContext
from app.agent.tools.registry import ToolRegistry


def test_structured_history_keeps_model_content_separate_from_summary():
    payload = {"type": "account_snapshot", "items": []}
    content = '当前无持仓\n{"items":[]}'
    block = ToolResultBlock(
        "call-1", content, display_payload=payload,
        display_summary="当前无持仓", code="OK",
    )
    stored = block_to_dict(block)
    assert stored["summary"] == "当前无持仓"
    assert stored["content"] == content
    assert stored["payload"] == payload
    restored = dict_to_block(json.loads(json.dumps(stored)))
    assert restored == block


def test_legacy_history_still_restores_summary_as_model_content():
    restored = dict_to_block({
        "type": "tool_result", "toolUseId": "old", "ok": True,
        "summary": "历史摘要",
    })
    assert restored.content == "历史摘要"
    assert restored.display_payload is None


@pytest.mark.asyncio
async def test_unexpected_trading_error_never_exposes_exception(caplog):
    class BrokenTool(Tool):
        name = "private_read"
        category = "account"

        async def execute(self, args, ctx):
            raise RuntimeError("SENTINEL_PRIVATE_CREDENTIAL")

    result = await ToolRegistry([BrokenTool()]).execute(
        "private_read", {}, ToolContext(lambda: None),
    )
    assert not result.ok
    assert result.code == "TOOL_EXECUTION_FAILED"
    assert "SENTINEL_PRIVATE_CREDENTIAL" not in result.summary
    assert "SENTINEL_PRIVATE_CREDENTIAL" not in caplog.text


def test_providers_only_receive_model_content():
    from app.agent.llm.anthropic_client import _to_anthropic_message
    from app.agent.llm.openai_client import _to_openai_messages
    from app.agent.llm.base import LlmMessage
    block = ToolResultBlock(
        "call-1", '事实\n{"size":"2"}',
        display_summary="持仓摘要",
        display_payload={"type": "account_snapshot", "uiOnly": "隐藏"},
    )
    message = LlmMessage("tool", [block])
    for wire in (_to_anthropic_message(message), _to_openai_messages(message)):
        encoded = json.dumps(wire, ensure_ascii=False)
        assert "事实" in encoded
        assert "display_payload" not in encoded
        assert "uiOnly" not in encoded


def test_oversized_model_result_is_valid_bounded_json():
    from app.agent.loop import _tool_result_block
    from app.agent.tools.base import ToolResult
    block = _tool_result_block("call", ToolResult(
        True, "查询完成", model_payload={"items": ["数" * 30000]},
    ))
    summary, encoded = block.content.split("\n", 1)
    assert summary == "查询完成"
    assert len(encoded.encode("utf-8")) <= 24 * 1024
    assert json.loads(encoded)["truncated"] is True


async def test_each_tool_keeps_frozen_account_and_gets_own_call_id():
    from app.agent.loop import AgentSession, _RunStats
    from app.agent.llm.base import ToolUseBlock
    from app.agent.tools.base import ToolResult
    from app.agent_trading.schemas import AgentCallContext
    from app.config import Settings
    calls = []

    class ReadTool(Tool):
        name = "frozen_read"

        async def execute(self, args, ctx):
            calls.append(ctx.call)
            return ToolResult(True, "已读取", model_payload={"items": []},
                              payload={"type": "account_snapshot"})

    session = AgentSession("wire", None, ToolRegistry([ReadTool()]),
                           Settings(), lambda: None)
    stored = []

    async def persist(message):
        stored.append(message)

    session._append_and_persist = persist
    call = AgentCallContext("wire", "msg", account_id=7, binding_version=3)
    ctx = ToolContext(lambda: None, call=call)
    await session._execute_tool_calls([
        ToolUseBlock("first", "frozen_read", {}),
        ToolUseBlock("second", "frozen_read", {}),
    ], ctx, _RunStats())
    assert [item.tool_use_id for item in calls] == ["first", "second"]
    assert all(item.account_id == 7 and item.binding_version == 3
               for item in calls)
    assert call.tool_use_id == ""
    assert stored[0].content[0].display_payload["type"] == "account_snapshot"
