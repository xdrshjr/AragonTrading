"""单条 OKX WebSocket 连接的状态机（okx-link-resilience §2.2）——根因 A / B 的正面修复。

今天 ``ws_client._run_endpoint`` 的存活判定只剩「``async for raw in ws`` 会不会抛异常」：
``ping_interval=None`` 同时关掉了库的协议级 ping 与 pong 超时，应用层虽然每 20s 发一个 ``"ping"``，
但**没有任何人检查 ``"pong"`` 回没回来**。代理中断、NAT 老化、笔记本休眠唤醒、机场节点切换——这些
最常见的断线形态恰恰都是「不 FIN 不 RST，就是不再来数据」，于是**这类断线现在永远不会重连**，
只表现为「K 线不动了」，最后由会话看门狗在 ``bar × 3`` 之后说一句「行情连接异常」（1H 周期下
就是 3 小时）。

本模块给一条连接补上三重活性判据（心跳-应答 / 静默看门狗 / 订阅确认）与一套不会被「连上就断」
骗到的退避，并把每一次状态迁移如实写进健康登记簿。

**它不 import 任何 live_auto / ai_auto / paper**：补根之类需要业务窗口口径的事，通过
``on_stream`` 回调交回给 session 自己完成。
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal

from ..config import Settings
from .link_state import Kind, get_link_registry
from .rate_limit import get_limiters
from .retry import RetryPolicy, next_delay, tick_timeout

logger = logging.getLogger("aragon.okx.ws")

WsState = Literal["connecting", "subscribing", "live", "degraded", "down"]
ConnectFactory = Callable[..., object]


@dataclass(frozen=True)
class WSStreamConfig:
    """一条 endpoint 连接的静态描述（URL + 订阅参数 + 它在登记簿里的身份）。"""

    url: str
    args: list[dict[str, str]]
    kind: Kind
    label: str
    key: str


@dataclass(frozen=True)
class StreamEvent:
    """链路状态迁移事件。``downtime_s`` 只在回到 ``live`` 时有意义（本次断了多久）。"""

    key: str
    state: WsState
    attempt: int
    downtime_s: float
    detail: str


StreamHandler = Callable[[StreamEvent], Awaitable[None]]


async def _noop_stream(_event: StreamEvent) -> None:
    return None


@dataclass
class _PumpState:
    """一次连接生命周期内的可变判据状态（拆出来是为了让 ``_pump`` 保持在 50 行内）。"""

    opened_at: float
    last_inbound: float
    pending: set[str]
    ping_sent_at: float | None = None
    got_data: bool = False
    acked: bool = False
    live: bool = False


class WSSupervisor:
    """把一条 WS 连接从建立、订阅确认、活性监测到重连的整个生命周期管起来。"""

    def __init__(self, cfg: WSStreamConfig, *, settings: Settings,
                 connect_factory: ConnectFactory,
                 on_raw: Callable[[str], Awaitable[None]],
                 on_stream: StreamHandler = _noop_stream) -> None:
        self._cfg = cfg
        self._settings = settings
        self._connect = connect_factory
        self._on_raw = on_raw
        self._on_stream = on_stream
        self._clock = time.monotonic
        self._down_since: float | None = None

    # ---------- 主循环 ----------
    async def run(self, stop: asyncio.Event) -> None:
        """一直跑到 ``stop`` 被置位；每一轮 = 一次建连尝试 + 一段收发 + 一次退避。"""
        attempt = 0
        delay = 0.0
        registry = get_link_registry()
        try:
            while not stop.is_set():
                if attempt >= 1:
                    await self._await_connect_token()
                if stop.is_set():
                    break
                healthy, reason = await self._one_connection(stop, attempt)
                if stop.is_set():
                    break
                registry.note_failure(self._cfg.key, kind=self._cfg.kind, label=self._cfg.label,
                                      code=_reason_code(reason), message=_reason_text(reason))
                await self._emit("degraded", attempt, reason)
                if healthy:            # 只有「真的健康过」才复位退避（根因 B）
                    attempt, delay = 0, 0.0
                delay = next_delay(delay, self._backoff_policy())
                logger.warning("OKX WS %s disconnected (%s); reconnecting in %.1fs",
                               self._cfg.key, reason, delay)
                await _sleep_or_stop(stop, delay)
                attempt += 1
        finally:
            registry.close_channel(self._cfg.key)

    async def _one_connection(self, stop: asyncio.Event, attempt: int) -> tuple[bool, str]:
        """建一次连接并收发到失活。返回 ``(本次是否算健康过, 终止原因)``。"""
        await self._emit("connecting", attempt, "connecting")
        try:
            # 工厂返回值**一律不得 await**，只能交给 `async with`：真实 `websockets.connect(...)`
            # 两者皆可，但既有测试替身 `_FakeConnection` 不可 await（tests/test_okx_ws_endpoints.py）。
            async with self._connect(self._cfg.url) as ws:
                state = await self._open(ws, attempt)
                reason = await self._pump(ws, state, stop)
                await _force_close(ws)
                healthy = (state.got_data and not state.pending) or self._alive_enough(state)
                return healthy, reason
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 —— 建连 / 收发的任何异常都只意味着「该重连了」
            return False, f"connect_error: {exc}"

    async def _open(self, ws, attempt: int) -> _PumpState:
        """发订阅并初始化判据状态；建连即喂一次静默看门狗。"""
        now = self._clock()
        state = _PumpState(opened_at=now, last_inbound=now,
                           pending={_channel_key(a) for a in self._cfg.args})
        await ws.send(json.dumps({"op": "subscribe", "args": self._cfg.args}))
        get_link_registry().note_ws(self._cfg.key, kind=self._cfg.kind, label=self._cfg.label,
                                    event="connected")
        downtime = 0.0 if self._down_since is None else max(0.0, now - self._down_since)
        await self._emit("subscribing", attempt, "subscribe_sent", downtime_s=downtime)
        return state

    async def _pump(self, ws, state: _PumpState, stop: asyncio.Event) -> str:
        """在一条已建立的连接上收发，直到判定失活。返回终止原因（写进登记簿）。"""
        cfg = self._settings
        while not stop.is_set():
            now = self._clock()
            idle = now - state.last_inbound
            expired = self._expired(state, now, idle)
            if expired is not None:
                return expired
            timeout = tick_timeout(idle, cfg.okx_link_ws_ping_interval_s)
            try:
                raw = await asyncio.wait_for(_recv(ws), timeout=timeout)
            except asyncio.TimeoutError:
                if (cfg.okx_link_ws_ping_interval_s > 0 and state.ping_sent_at is None
                        and idle >= cfg.okx_link_ws_ping_interval_s):
                    await ws.send("ping")
                    state.ping_sent_at = self._clock()
                continue
            except StopAsyncIteration:
                return "closed_by_peer"
            state.last_inbound = self._clock()
            state.ping_sent_at = None
            fatal = await self._consume(raw, state)
            if fatal is not None:
                return fatal
        return "stopped"

    def _expired(self, state: _PumpState, now: float, idle: float) -> str | None:
        """三重活性判据的纯判定（顺序即优先级）；任一命中即返回终止原因。"""
        cfg = self._settings
        if cfg.okx_link_ws_silence_timeout_s > 0 and idle >= cfg.okx_link_ws_silence_timeout_s:
            return "silence_timeout"
        if (state.ping_sent_at is not None and cfg.okx_link_ws_pong_timeout_s > 0
                and now - state.ping_sent_at >= cfg.okx_link_ws_pong_timeout_s):
            return "pong_timeout"
        if (state.pending and cfg.okx_link_ws_subscribe_timeout_s > 0
                and now - state.opened_at >= cfg.okx_link_ws_subscribe_timeout_s):
            return "subscribe_timeout"
        return None

    async def _consume(self, raw: str, state: _PumpState) -> str | None:
        """把一帧原样交给既有处理器，同时抽出订阅 ack / 错误。返回致命原因或 ``None``。

        **先转发再判定**：既有语义（``event:error`` 转发给 ``on_error`` 且不断链，F-4）一字不改，
        本层只是额外把「订阅一直没确认」这件事变成一个会触发重连的判据。
        """
        await self._on_raw(raw)
        registry = get_link_registry()
        if raw == "pong":
            return None
        try:
            msg = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return None
        if not isinstance(msg, dict):
            return None
        event = msg.get("event")
        if event == "subscribe":
            state.pending.discard(_channel_key(msg.get("arg") or {}))
            if not state.pending and not state.acked:
                state.acked = True
                registry.note_ws(self._cfg.key, kind=self._cfg.kind, label=self._cfg.label,
                                 event="resubscribed")
            return None
        if event == "error":
            return f"subscribe_error: {msg.get('code')}" if state.pending else None
        if event is not None:
            return None
        state.got_data = True
        # 数据比 ack 更硬：收到某频道的数据即视为该频道已订阅成功（漏掉一条 ack 不该拆掉一条
        # 正在送数据的连接）。
        state.pending.discard(_channel_key(msg.get("arg") or {}))
        registry.note_ws(self._cfg.key, kind=self._cfg.kind, label=self._cfg.label,
                         event="message")
        await self._mark_live(state)
        return None

    # ---------- 辅助 ----------
    async def _mark_live(self, state: _PumpState) -> None:
        """首帧业务数据 = 这条连接真的在送数据；此刻才算 ``live``，并结算本次断开时长。"""
        if state.live:
            return
        state.live = True
        downtime = 0.0 if self._down_since is None else max(0.0, self._clock() - self._down_since)
        self._down_since = None
        await self._emit("live", 0, "first_data", downtime_s=downtime)

    def _alive_enough(self, state: _PumpState) -> bool:
        """连接存活时长是否达到「可以复位退避」的门槛（0 即连上就复位，旧行为）。"""
        threshold = self._settings.okx_link_ws_healthy_after_s
        if threshold <= 0:
            return True
        return (self._clock() - state.opened_at) >= threshold

    def _backoff_policy(self) -> RetryPolicy:
        cfg = self._settings
        return RetryPolicy(max_attempts=1, base_delay_s=cfg.okx_link_ws_backoff_base_s,
                           max_delay_s=cfg.okx_link_ws_backoff_max_s, budget_s=0.0)

    async def _await_connect_token(self) -> None:
        """全局建连闸——**只作用于重连**（attempt ≥ 1），首次建连不取令牌。

        两个理由缺一不可：① 语义上首建的并发度本就由 ``live_auto_start_concurrency`` 与「一个浏览器
        一次订阅」约束住，真正会失控的是故障期间的重连风暴；② 桶是进程单例，若首建也取令牌，同进程
        连跑的既有用例会在第二个用例上等 0.5s，而它的等待器只 ``asyncio.sleep(0)`` 空转，墙钟推不动。
        """
        cfg = self._settings
        bucket = get_limiters().bucket("ws:connect", cfg.okx_link_ws_connect_rate_per_s, 2.0)
        await bucket.acquire()

    async def _emit(self, state: WsState, attempt: int, detail: str,
                    downtime_s: float = 0.0) -> None:
        if state in ("degraded", "down", "connecting") and self._down_since is None:
            self._down_since = self._clock()
        try:
            await self._on_stream(StreamEvent(key=self._cfg.key, state=state, attempt=attempt,
                                              downtime_s=downtime_s, detail=detail))
        except Exception as exc:  # noqa: BLE001 —— 观测回调绝不许打死连接
            logger.warning("okx ws stream handler failed (%s): %s", self._cfg.key, exc)


def _channel_key(arg: dict) -> str:
    return f"{arg.get('channel', '')}:{arg.get('instId', '')}"


def _reason_code(reason: str) -> str:
    head = reason.split(":", 1)[0].strip()
    return f"WS_{head.upper()}" if head else "WS_DISCONNECTED"


_REASON_TEXT = {
    "silence_timeout": "长时间无任何入站数据，已强制重连",
    "pong_timeout": "心跳应答超时，已强制重连",
    "subscribe_timeout": "订阅确认超时，已强制重连",
    "closed_by_peer": "连接被对端关闭，正在重连",
    "stopped": "连接已停止",
}


def _reason_text(reason: str) -> str:
    head = reason.split(":", 1)[0].strip()
    return _REASON_TEXT.get(head, reason)


async def _recv(ws):
    """读一帧：优先 ``recv()``，替身没有它时回退 ``__anext__()``。

    既有测试替身 ``_FakeConnection``（``tests/test_okx_ws_endpoints.py``）只实现了
    ``__aiter__/__anext__``，**既没有 ``recv`` 也没有 ``close``**；这是不改既有测试就能通过的
    唯一写法。新用例用自带 ``recv`` 的替身覆盖主路径。
    """
    recv = getattr(ws, "recv", None)
    if recv is not None:
        return await recv()
    return await ws.__anext__()


async def _force_close(ws) -> None:
    """判定失活后强制关掉 socket；替身没有 ``close`` 时依赖 ``async with`` 退出即可。"""
    close = getattr(ws, "close", None)
    if close is None:
        return
    try:
        await close()
    except Exception as exc:  # noqa: BLE001 —— 关一个已经坏掉的连接失败是常态
        logger.debug("okx ws force close skipped: %s", exc)


async def _sleep_or_stop(stop: asyncio.Event, delay: float) -> None:
    """可被 ``stop`` 打断的退避等待（停止时不再白等一整个退避）。"""
    if delay <= 0:
        return
    try:
        await asyncio.wait_for(stop.wait(), timeout=delay)
    except asyncio.TimeoutError:
        return
