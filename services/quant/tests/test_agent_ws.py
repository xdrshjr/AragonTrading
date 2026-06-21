"""Agent WS 端点单测（假 LLM + 假 WebSocket，不依赖网络）。"""
from __future__ import annotations

import asyncio
import json

from starlette.websockets import WebSocketDisconnect

from app.agent.llm.base import StreamEvent
from app.agent.manager import AgentManager
from app.agent.tools.registry import ToolRegistry
from app.api.agent import _drain, ws_agent
from app.config import Settings
from app.db.session import dispose_engine, get_sessionmaker, init_db


class _ScriptedLLM:
    def __init__(self, turns):
        self._turns = turns
        self.calls = 0

    async def stream(self, *, system, messages, tools):
        turn = self._turns[min(self.calls, len(self._turns) - 1)]
        self.calls += 1
        for ev in turn:
            yield ev


class _FakeWS:
    def __init__(self, inbound):
        self.inbound = list(inbound)
        self.sent = []
        self.accepted = False
        self.closed = False
        self._idx = 0

    async def accept(self):
        self.accepted = True

    async def close(self):
        self.closed = True

    async def send_text(self, text):
        self.sent.append(json.loads(text))

    async def receive_text(self):
        if self._idx < len(self.inbound):
            msg = self.inbound[self._idx]
            self._idx += 1
            return json.dumps(msg)
        # 脚本耗尽：等到 run 结束（done/error）再断连，给后台任务时间推进事件。
        for _ in range(20000):
            if any(e.get("type") in ("done", "error") for e in self.sent):
                break
            await asyncio.sleep(0)
        raise WebSocketDisconnect()


def _manager(api_key, llm):
    settings = Settings(agent_api_key=api_key, agent_provider="anthropic")
    return AgentManager(settings, llm=llm, registry=ToolRegistry(),
                        session_factory=get_sessionmaker())


def _types(ws):
    return [e["type"] for e in ws.sent]


async def test_ws_emits_ready_history_and_full_run(monkeypatch):
    await init_db()
    llm = _ScriptedLLM([
        [
            StreamEvent(kind="text_delta", text="画图中"),
            StreamEvent(kind="tool_use_start", id="t1", name="show_chart"),
            StreamEvent(kind="tool_use_args_delta", id="t1", partial_json='{"instId":"BTC-USDT-SWAP"}'),
            StreamEvent(kind="message_done", stop_reason="tool_use"),
        ],
        [
            StreamEvent(kind="text_delta", text="完成"),
            StreamEvent(kind="message_done", stop_reason="end_turn"),
        ],
    ])
    manager = _manager("x", llm)
    monkeypatch.setattr("app.api.agent.get_agent_manager", lambda: manager)
    ws = _FakeWS([{"type": "user_message",
                   "data": {"text": "画出 BTC 永续日线", "ui": {"instId": "BTC-USDT-SWAP", "bar": "1D"}}}])
    try:
        await ws_agent(ws, "conv-ws-1")
        types = _types(ws)
        assert types[0] == "ready" and ws.sent[0]["data"]["configured"] is True
        assert types[1] == "history"
        assert "message_start" in types
        assert any(e["type"] == "tool_call" and e["data"]["name"] == "show_chart" for e in ws.sent)
        assert any(e["type"] == "ui_action" for e in ws.sent)
        assert any(e["type"] == "done" for e in ws.sent)
    finally:
        await dispose_engine()


async def test_ws_unconfigured_reports_not_configured(monkeypatch):
    await init_db()
    manager = _manager("", _ScriptedLLM([[]]))
    monkeypatch.setattr("app.api.agent.get_agent_manager", lambda: manager)
    ws = _FakeWS([{"type": "user_message", "data": {"text": "hi", "ui": None}}])
    try:
        await ws_agent(ws, "conv-ws-2")
        assert ws.sent[0]["type"] == "ready" and ws.sent[0]["data"]["configured"] is False
        errors = [e for e in ws.sent if e["type"] == "error"]
        assert errors and errors[0]["data"]["code"] == "AGENT_NOT_CONFIGURED"
    finally:
        await dispose_engine()


async def test_drain_cancels_orphan_run_on_timeout():
    """run 在客户端断连后仍未收束时，_drain 在超时内返回并 cancel 孤儿，绝不挂死（H4）。"""

    async def _hang():
        await asyncio.sleep(30)

    task = asyncio.create_task(_hang())
    await asyncio.sleep(0)  # 让任务起跑
    await _drain(task, timeout=0.05)
    assert task.cancelled()  # 孤儿被显式 cancel


async def test_drain_noop_when_task_already_done():
    async def _quick():
        return 1

    task = asyncio.create_task(_quick())
    await task
    await _drain(task, timeout=1.0)  # 已完成：直接返回，不抛
    assert task.done() and not task.cancelled()
