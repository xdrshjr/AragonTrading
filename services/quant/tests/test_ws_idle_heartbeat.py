"""空闲期实时流的保活帧（resilience-and-hci-hardening D3）。

客户端 `apps/web/lib/ws.ts` 的 45s 静默看门狗对**任何**入站帧都算数，而这四条流在会话空闲时
一帧都不发——于是「面板开着、什么都没发生」这个最常见的状态会被误判成断连，每 46 秒断一次、
重连一次、重放一次整段快照。本文件把「静默即发保活帧」钉住。

**手法**：本仓 WS 用例一律 `_FakeWS` + 直接 `await` 路由协程（禁用 `TestClient`），
模板见 `tests/test_weight_train_ws.py:37/65`。会话流路由会先 `manager.get(sid)`，故用例把
**模块级 getter** 换成假管理器——不碰进程级单例，天然无跨用例污染。

反向用例同样重要：`/ws/market` 上「静默即证据」，给它加保活会把 45s 看门狗**致盲**
（行情停了却永不重连，比现状更糟）。`test_ws_market_stays_silent_by_design` 就是那道护栏。
"""
from __future__ import annotations

import asyncio
import importlib
import json

import pytest
from fastapi import WebSocketDisconnect


class _FakeWS:
    """最小 WS 替身：只记下发出去的帧（照抄 tests/test_weight_train_ws.py:37）。"""

    def __init__(self):
        self.sent: list = []
        self.closed = False
        self.query_params: dict = {}

    async def accept(self):
        pass

    async def close(self):
        self.closed = True

    async def send_text(self, text):
        self.sent.append(json.loads(text))


class _PingThenDisconnectWS(_FakeWS):
    """收到首个 `ping` 即模拟断线，使会话流路由协程返回（免无限阻塞）。"""

    async def send_text(self, text):
        await super().send_text(text)
        if json.loads(text).get("type") == "ping":
            raise WebSocketDisconnect()


class _FakeSession:
    """会话流路由要用到的三件套：subscribe / snapshot / unsubscribe。"""

    def __init__(self):
        self.queues: list[asyncio.Queue] = []
        self.unsubscribed = 0

    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue()
        self.queues.append(queue)
        return queue

    def snapshot(self) -> dict:
        return {"status": {"state": "running"}, "trades": [], "position": None}

    def unsubscribe(self, queue) -> None:
        self.unsubscribed += 1


class _FakeManager:
    def __init__(self, session):
        self._session = session

    def get(self, session_id: str):
        return self._session


# (模块, 路由协程名, 管理器 getter 名) —— 三条会话流的保活姿态逐字相同，用例也就该逐字相同。
SESSION_STREAMS = [
    ("app.api.paper", "ws_paper", "get_paper_manager"),
    ("app.api.live_auto", "ws_live_auto", "get_live_auto_manager"),
    ("app.api.ai_auto", "ws_ai_trade", "get_ai_trade_manager"),
]


def _wire_session_stream(monkeypatch, module_path: str, getter_name: str):
    """把心跳周期压到 0.05s 并换上假管理器；返回 (模块, 假会话)。"""
    module = importlib.import_module(module_path)
    session = _FakeSession()
    monkeypatch.setattr(module, "_WS_HEARTBEAT_SECONDS", 0.05)
    monkeypatch.setattr(module, getter_name, lambda: _FakeManager(session))
    return module, session


@pytest.mark.parametrize("module_path,route_name,getter_name", SESSION_STREAMS)
async def test_session_stream_pings_when_idle(monkeypatch, module_path, route_name, getter_name):
    """三条会话流：连上后不产生任何业务事件，也必须在一个心跳周期内下发 `{"type":"ping"}`。"""
    module, session = _wire_session_stream(monkeypatch, module_path, getter_name)

    ws = _PingThenDisconnectWS()
    await asyncio.wait_for(getattr(module, route_name)(ws, "sid-1"), timeout=2.0)

    types = [event["type"] for event in ws.sent]
    assert types[:2] == ["status", "snapshot"]     # 连接握手语义未变
    assert types[-1] == "ping"
    assert session.unsubscribed == 1               # 保活路径不得泄漏订阅


@pytest.mark.parametrize("module_path,route_name,getter_name", SESSION_STREAMS)
async def test_session_stream_keepalive_does_not_swallow_events(monkeypatch, module_path,
                                                                route_name, getter_name):
    """保活是**加法**：排队中的业务事件照旧原样下发，不因超时分支被吞掉或改写。"""
    module, session = _wire_session_stream(monkeypatch, module_path, getter_name)

    ws = _PingThenDisconnectWS()
    task = asyncio.create_task(getattr(module, route_name)(ws, "sid-1"))
    while not session.queues:                       # 等路由跑到 subscribe()
        await asyncio.sleep(0)
    session.queues[0].put_nowait({"type": "trade", "data": {"id": "t1"}})
    await asyncio.wait_for(task, timeout=2.0)

    trades = [event for event in ws.sent if event["type"] == "trade"]
    assert trades == [{"type": "trade", "data": {"id": "t1"}}]


class _FakeAgentSession:
    running = False


class _FakeAgentManager:
    def __init__(self):
        self.detached = 0

    async def active_config(self) -> dict:
        return {"provider": "stub"}

    async def history(self, conversation_id: str) -> list:
        return []

    async def attach(self, conversation_id: str, sink):
        return _FakeAgentSession()

    async def detach(self, conversation_id: str, sink) -> None:
        self.detached += 1


class _AgentIdleWS(_FakeWS):
    """空闲到第一帧心跳出现为止，然后断线。

    不能靠 `send_text` 抛 `WebSocketDisconnect` 收尾：`agent._send_raw` 整段 `try/except`
    吞发送异常（那是取消路径的既有护栏），抛出去只会被吃掉、循环继续。
    """

    def __init__(self):
        super().__init__()
        self._beat = asyncio.Event()

    async def send_text(self, text):
        await super().send_text(text)
        if json.loads(text).get("type") == "heartbeat":
            self._beat.set()

    async def receive_text(self):
        await self._beat.wait()      # 心跳出现之前保持沉默——这正是被测的空闲态
        raise WebSocketDisconnect()


async def test_agent_stream_sends_heartbeat_when_idle(monkeypatch):
    """agent 流的心跳此前是 **run 作用域**的：会话空闲时一帧都没有，正是最常见的那个状态。

    帧类型必须是 `heartbeat` 而不是 `ping`——前端 `AgentPanel` 已有该 case（不入消息流、
    不推进 `lastSeq`），复用它前端零改动，也不污染 `lastSeq` 对账锚点，故这里也断言**无 `seq`**。
    """
    from app.api import agent as agent_api

    manager = _FakeAgentManager()
    monkeypatch.setattr(agent_api, "_WS_IDLE_SECONDS", 0.05)
    monkeypatch.setattr(agent_api, "get_agent_manager", lambda: manager)

    ws = _AgentIdleWS()
    await asyncio.wait_for(agent_api.ws_agent(ws, "conv-1"), timeout=2.0)

    types = [event["type"] for event in ws.sent]
    assert types[:2] == ["ready", "history"]
    beat = ws.sent[-1]
    assert beat["type"] == "heartbeat"
    assert "seq" not in beat and "seq" not in beat.get("data", {})
    assert manager.detached == 1                    # 断连即脱离的既有语义未变


async def test_ws_market_stays_silent_by_design(monkeypatch):
    """`/ws/market` **刻意**不发保活帧：那条流上静默即证据。

    上一轮引入 45s 看门狗要的正是「行情停了 ⇒ 重连」。给它加保活会让连接永远「活着」，
    而图表可以静止一小时无人察觉。这条反向用例存在的唯一理由，是让未来「顺手补齐」的那次改动变红。
    """
    from app.api import ws_market as market_api

    class _NoopClient:
        def __init__(self, *args, **kwargs):
            pass

        def start(self):
            pass

        async def stop(self):
            pass

    monkeypatch.setattr(market_api, "OKXMarketWSClient", _NoopClient)

    class _SilentWS(_FakeWS):
        async def receive_text(self):
            await asyncio.sleep(3600)

    ws = _SilentWS()
    task = asyncio.create_task(market_api.ws_market(ws))
    await asyncio.sleep(0.2)                  # 远超三条会话流 0.05s 的心跳周期
    assert ws.sent == []

    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
