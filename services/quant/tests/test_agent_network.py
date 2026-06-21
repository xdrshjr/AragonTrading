"""真实 KIMI 端到端冒烟（默认排除，opt-in: pytest -m network）。

覆盖"含工具调用"的真实往返（验证 KIMI 的 tool_use 流式解析，P1-A / P2-1）：
断言所配 agent_model 真实可用、tool_use 事件可被正确归一化。需有效 AGENT_API_KEY + 网络/代理。
"""
from __future__ import annotations

import pytest

from app.agent.llm.base import LlmMessage, TextBlock, ToolSchema
from app.agent.llm.factory import make_llm_client
from app.config import get_settings

pytestmark = pytest.mark.network

_TOOL = ToolSchema(
    name="list_instruments",
    description="列出某类型(SPOT/SWAP/FUTURES)成交额最高的标的",
    input_schema={
        "type": "object",
        "properties": {"instType": {"type": "string", "enum": ["SPOT", "SWAP", "FUTURES"]}},
        "required": ["instType"],
    },
)


async def test_real_kimi_tool_use_round_trip():
    settings = get_settings()
    if not settings.agent_api_key:
        pytest.skip("AGENT_API_KEY 未配置，跳过真实端到端冒烟")
    client = make_llm_client(settings)
    messages = [LlmMessage("user", [TextBlock(
        "请调用 list_instruments 工具查看现货(SPOT)里成交额最高的标的。必须调用工具。")])]
    kinds: list[str] = []
    tool_name = None
    async for ev in client.stream(system="你是交易助手，需要数据时务必调用工具。",
                                  messages=messages, tools=[_TOOL]):
        kinds.append(ev.kind)
        if ev.kind == "error":
            pytest.fail(f"LLM error: {ev.code} {ev.message}")
        if ev.kind == "tool_use_start":
            tool_name = ev.name
    assert "message_done" in kinds
    assert "tool_use_start" in kinds, "所配模型未触发 tool_use 流式事件"
    assert tool_name == "list_instruments"
