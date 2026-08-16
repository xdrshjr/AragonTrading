"""Agent WS 端点单测（假 LLM + 假 WebSocket，不依赖网络）。

韧性流式（iteration 3）：断连即 detach（run 在宽限内存活）、重连 resume 重放续流、
run 在宽限内自然完成则 resumed{ok:false,completed} 且不重放（RN-1 防回归）。
"""
from __future__ import annotations

import asyncio
import json
import time

import pytest
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


class _PausingLLM:
    """先吐两段文本即暂停（等待 resume_gate），收到放行后吐尾段并结束（驱动断连/续流时序）。"""

    def __init__(self):
        self.first_done = asyncio.Event()   # 已吐完前两段（A 可断连）
        self.resume_gate = asyncio.Event()  # 测试放行尾段

    async def stream(self, *, system, messages, tools):
        yield StreamEvent(kind="text_delta", text="A")
        yield StreamEvent(kind="text_delta", text="B")
        self.first_done.set()
        await self.resume_gate.wait()
        yield StreamEvent(kind="text_delta", text="C")
        yield StreamEvent(kind="message_done", stop_reason="end_turn")


_RUN_END_TIMEOUT_S = 10.0


async def _await_event(sent: list[dict], kinds: tuple[str, ...],
                       timeout: float = _RUN_END_TIMEOUT_S) -> None:
    """等到 `sent` 里出现任一 `kinds` 事件；**按墙钟死线**有界等待。

    绝不能用「循环 N 次 sleep(0)」计预算：run 要经 aiosqlite 线程池落库，那是真实耗时，
    而 sleep(0) 不推进时间——整套顺序跑时预算会在 done 到达前耗尽（见 spec D2）。
    超时即返回（不抛），由调用方的断言把真失败暴露成断言错误而不是超时噪声。
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if any(e.get("type") in kinds for e in sent):
            return
        await asyncio.sleep(0.005)


class _FakeWS:
    def __init__(self, inbound):
        self.inbound = list(inbound)
        self.sent = []
        self.headers: dict = {}   # 无 Origin ⇒ 非浏览器客户端，来源守卫放行（app/api/ws_guard.py）
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
        await _await_event(self.sent, ("done", "error"))
        raise WebSocketDisconnect()


class _FakeWSDisconnectAfter:
    """A 连接：发 user_message，待 run 吐完前两段（first_done）即断连（run 仍在跑）。"""

    def __init__(self, gate: asyncio.Event):
        self.gate = gate
        self.sent = []
        self.headers: dict = {}   # 无 Origin ⇒ 非浏览器客户端，来源守卫放行（app/api/ws_guard.py）
        self._sent_user = False

    async def accept(self):
        pass

    async def close(self):
        pass

    async def send_text(self, text):
        self.sent.append(json.loads(text))

    async def receive_text(self):
        if not self._sent_user:
            self._sent_user = True
            return json.dumps({"type": "user_message", "data": {"text": "hi", "ui": None}})
        await self.gate.wait()
        raise WebSocketDisconnect()


class _FakeWSResume:
    """B 连接：重连后发 resume{lastSeq}，可选放行 gate，等待 resumed/done 再断连。"""

    def __init__(self, last_seq: int, *, release: asyncio.Event | None = None,
                 wait_for: str = "resumed"):
        self.last_seq = last_seq
        self._release = release
        self._wait_for = wait_for
        self.sent = []
        self.headers: dict = {}   # 无 Origin ⇒ 非浏览器客户端，来源守卫放行（app/api/ws_guard.py）
        self._sent_resume = False

    async def accept(self):
        pass

    async def close(self):
        pass

    async def send_text(self, text):
        self.sent.append(json.loads(text))

    async def receive_text(self):
        if not self._sent_resume:
            self._sent_resume = True
            return json.dumps({"type": "resume", "data": {"lastSeq": self.last_seq}})
        if self._release is not None:
            self._release.set()  # 放行尾段，使 run 续流至 done
        await _await_event(self.sent, (self._wait_for,))
        raise WebSocketDisconnect()


_MANAGERS: list[AgentManager] = []


def _manager(api_key, llm, **settings_kw):
    settings = Settings(agent_api_key=api_key, agent_provider="anthropic", **settings_kw)
    manager = AgentManager(settings, llm=llm, registry=ToolRegistry(),
                           session_factory=get_sessionmaker())
    _MANAGERS.append(manager)
    return manager


@pytest.fixture(autouse=True)
async def _shutdown_managers():
    """用例结束即取消并**收敛**宽限计时器：否则 `_grace_countdown` 任务跨用例泄漏（spec D2 根因 2）。

    `shutdown()` 只**投递**取消（其体内无 await，见 manager.py:268-275），故必须自己把句柄
    抓出来再 await 一次——这正是 D4 写下的同一条纪律。`shutdown()` 会 `clear()` 掉
    `_grace_timers`，所以句柄只能在调用**之前**取。
    """
    yield
    # 用 while/pop 而不是 for：一条用例可能造多个 manager，且即便中途抛错也要保证列表被清空
    # ——否则泄漏会跨用例累积，把本条修的问题换个更隐蔽的样子留下来。
    while _MANAGERS:
        manager = _MANAGERS.pop()
        timers = [t for t in manager._grace_timers.values() if not t.done()]
        await manager.shutdown()                                   # 投递取消
        if timers:
            await asyncio.gather(*timers, return_exceptions=True)  # 真正收敛


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
        # run 事件均带 seq（连接级 ready/history 不带 seq，RS-1/RN-5）。
        assert "seq" not in ws.sent[0] and "seq" not in ws.sent[1]
        assert all("seq" in e for e in ws.sent if e["type"] in ("message_start", "done"))
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


async def test_ws_resume_replays_after_last_seq_and_continues_to_done(monkeypatch):
    """断连→宽限内重连（run 仍在跑）→resume→重放 seq>lastSeq + 实时续流至 done，无重复（RS-5）。"""
    await init_db()
    llm = _PausingLLM()
    manager = _manager("x", llm, agent_detach_grace_seconds=5.0)
    monkeypatch.setattr("app.api.agent.get_agent_manager", lambda: manager)
    try:
        ws_a = _FakeWSDisconnectAfter(llm.first_done)
        await ws_agent(ws_a, "conv-r1")  # A 吐完 A/B 即断连，run 暂停（仍在跑）
        last_seq = max(e["seq"] for e in ws_a.sent if "seq" in e)
        sess = manager._sessions["conv-r1"]
        assert sess.running is True  # 断连即脱离、run 未取消

        ws_b = _FakeWSResume(last_seq, release=llm.resume_gate, wait_for="done")
        await ws_agent(ws_b, "conv-r1")
        resumed = [e for e in ws_b.sent if e["type"] == "resumed"]
        assert resumed and resumed[0]["data"]["ok"] is True
        deltas = [e for e in ws_b.sent if e["type"] == "text_delta"]
        assert all(e["seq"] > last_seq for e in deltas)  # 仅 seq>lastSeq，无 A/B 重复
        assert any(e["data"]["text"] == "C" for e in deltas)  # 尾段续流到达
        assert "done" in _types(ws_b)
    finally:
        await dispose_engine()


async def test_ws_resume_after_grace_completion_acks_false_without_replay(monkeypatch):
    """RN-1 防回归（端到端）：run 已完成 → 重连 resume 得 resumed{ok:false,completed}，绝不重放。"""
    await init_db()
    llm = _ScriptedLLM([[
        StreamEvent(kind="text_delta", text="完整回答"),
        StreamEvent(kind="message_done", stop_reason="end_turn"),
    ]])
    manager = _manager("x", llm, agent_detach_grace_seconds=5.0)
    monkeypatch.setattr("app.api.agent.get_agent_manager", lambda: manager)
    try:
        ws_a = _FakeWS([{"type": "user_message", "data": {"text": "hi", "ui": None}}])
        await ws_agent(ws_a, "conv-r2")  # 完整跑完一轮
        assert any(e["type"] == "done" for e in ws_a.sent)
        sess = manager._sessions["conv-r2"]
        assert sess.running is False

        ws_b = _FakeWSResume(0, wait_for="resumed")
        await ws_agent(ws_b, "conv-r2")
        resumed = [e for e in ws_b.sent if e["type"] == "resumed"]
        assert resumed and resumed[0]["data"] == {"ok": False, "reason": "completed"}
        # 绝不重放 run 事件（DB history 为权威，前端 dropStreaming 后与 history 重建无重复，RN-1）。
        assert not any(e["type"] == "text_delta" for e in ws_b.sent)
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
