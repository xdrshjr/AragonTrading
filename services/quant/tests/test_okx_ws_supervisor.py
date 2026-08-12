"""WS 连接状态机单测（okx-link-resilience §2.2）——根因 A / B 的复现与修复门禁。

「半开连接」用例是本轮的核心资产：它是过去所有断线故障的最小复现。代理中断、NAT 老化、笔记本
休眠唤醒、机场节点切换——这些最常见的断线形态都是「不 FIN 不 RST，就是不再来数据」，在本模块
出现之前**永远不会触发重连**，只表现为「K 线不动了」，最后由会话看门狗在 `bar × 3` 之后说一句
「行情连接异常」（1H 周期下就是 3 小时）。

全部注入假连接 + 极小阈值，**无真实网络、无长 sleep**。
"""
from __future__ import annotations

import asyncio
import json

from app.config import Settings
from app.okx.link_state import get_link_registry
from app.okx.rate_limit import get_limiters
from app.okx.ws_supervisor import StreamEvent, WSStreamConfig, WSSupervisor

_CFG = WSStreamConfig(
    url="wss://business.test/ws/v5/business",
    args=[{"channel": "candle1H", "instId": "BTC-USDT-SWAP"}],
    kind="wsBusiness",
    label="实时流(K 线 · BTC-USDT-SWAP 1H)",
    key="ws:business:BTC-USDT-SWAP:1H:live:s1",
)


def _settings(**over) -> Settings:
    base = dict(okx_link_ws_ping_interval_s=0.02, okx_link_ws_pong_timeout_s=0.05,
                okx_link_ws_silence_timeout_s=0.3, okx_link_ws_subscribe_timeout_s=0.05,
                okx_link_ws_healthy_after_s=0.5, okx_link_ws_backoff_base_s=0.001,
                okx_link_ws_backoff_max_s=0.01, okx_link_ws_connect_rate_per_s=0.0)
    base.update(over)
    return Settings(**base)


class _Conn:
    """可编程假连接：按 `frames` 逐帧回；帧用尽后按 `then` 决定挂起还是收尾。"""

    def __init__(self, frames: list[str], *, then: str = "hang", record: list | None = None):
        self._frames = list(frames)
        self._then = then
        self.sent: list[str] = []
        self.closed = False
        self._record = record

    async def __aenter__(self) -> "_Conn":
        return self

    async def __aexit__(self, *_exc) -> bool:
        return False

    async def send(self, payload: str) -> None:
        self.sent.append(payload)
        if self._record is not None:
            self._record.append(payload)

    async def recv(self) -> str:
        if self._frames:
            return self._frames.pop(0)
        if self._then == "close":
            raise ConnectionError("closed by peer")
        await asyncio.Event().wait()      # 半开：socket 还开着，但再也不来数据
        raise AssertionError("unreachable")

    async def close(self) -> None:
        self.closed = True


class _BareConn:
    """逐字复制既有替身的能力面：只有 `__aenter__/__aexit__/send/__aiter__/__anext__`。

    **没有 `recv`、没有 `close`、不可 await**（[评审 P1-10]）。刻意**不**继承 `_Conn`——继承会把
    `recv` / `close` 带进来，那样这条用例就守不住任何东西了。
    """

    def __init__(self, frames: list[str]):
        self._frames = list(frames)
        self.sent: list[str] = []

    async def __aenter__(self) -> "_BareConn":
        return self

    async def __aexit__(self, *_exc) -> bool:
        return False

    async def send(self, payload: str) -> None:
        self.sent.append(payload)

    def __aiter__(self) -> "_BareConn":
        return self

    async def __anext__(self) -> str:
        if self._frames:
            return self._frames.pop(0)
        await asyncio.Event().wait()
        raise StopAsyncIteration


_ACK = json.dumps({"event": "subscribe", "arg": {"channel": "candle1H",
                                                 "instId": "BTC-USDT-SWAP"}})
_DATA = json.dumps({"arg": {"channel": "candle1H", "instId": "BTC-USDT-SWAP"},
                    "data": [["1", "2"]]})


class _Factory:
    """替身 `connect_factory`：按序列发连接，并记录被调用次数与调用时刻。"""

    def __init__(self, conns: list) -> None:
        self._conns = list(conns)
        self.calls = 0
        self.at: list[float] = []

    def __call__(self, _url: str):
        self.calls += 1
        self.at.append(asyncio.get_running_loop().time())
        return self._conns[min(self.calls - 1, len(self._conns) - 1)]


async def _run_briefly(sup: WSSupervisor, seconds: float) -> dict:
    """跑一小段后停掉，返回**停止之前**取的登记簿快照。

    必须在停止前取：`run()` 退出时会 `close_channel`，停用通道刻意不再出现在 `channels` 里
    （那正是 P0-3 的修复）——停止之后再取就什么也看不到了。
    """
    stop = asyncio.Event()
    task = asyncio.create_task(sup.run(stop))
    await asyncio.sleep(seconds)
    snapshot = get_link_registry().snapshot()
    stop.set()
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    return snapshot


def _make(cfg_settings: Settings, factory, events: list) -> WSSupervisor:
    async def on_raw(_raw: str) -> None:
        return None

    async def on_stream(event: StreamEvent) -> None:
        events.append(event)

    return WSSupervisor(_CFG, settings=cfg_settings, connect_factory=factory,
                        on_raw=on_raw, on_stream=on_stream)


# ---------------- ① 半开连接：pong 逾期即重连 ----------------


async def test_a_half_open_connection_is_detected_and_reconnected():
    """替身只收 ping、不回任何帧 ⇒ `pong_timeout` 内判失活并重连（今天这类断线永不重连）。"""
    conns = [_Conn([_ACK]) for _ in range(6)]
    factory = _Factory(conns)
    events: list[StreamEvent] = []
    sup = _make(_settings(), factory, events)

    snapshot = await _run_briefly(sup, 0.5)

    assert factory.calls >= 2, "半开连接必须被判失活并重连"
    assert conns[0].sent[0].startswith('{"op": "subscribe"')
    assert "ping" in conns[0].sent, "静默超过 ping_interval 就该主动探一次"
    assert conns[0].closed is True, "判定失活后必须强制关闭 socket，不做「原地等等看」"
    row = snapshot["channels"][0]
    assert row["lastErrorCode"] in ("WS_PONG_TIMEOUT", "WS_SILENCE_TIMEOUT")
    assert "心跳应答超时" in row["lastErrorMessage"] or "无任何入站数据" in row["lastErrorMessage"]


# ---------------- ② 订阅永不 ack ----------------


async def test_a_subscription_that_is_never_acked_forces_a_reconnect():
    """连上了、订阅发出去了，但对端一个 ack 也不回 —— 这条连接对上层毫无价值，必须换掉。"""
    # 每 ping 回一个 pong：活性判据看起来是健康的，唯一不对的就是订阅没被确认。
    conns = [_Conn(["pong"] * 50) for _ in range(6)]
    factory = _Factory(conns)
    events: list[StreamEvent] = []
    sup = _make(_settings(), factory, events)

    snapshot = await _run_briefly(sup, 0.4)

    assert factory.calls >= 2
    assert snapshot["channels"][0]["lastErrorCode"] == "WS_SUBSCRIBE_TIMEOUT"


# ---------------- ③ 「连上就断」的热重连 ----------------


async def test_backoff_is_not_reset_by_a_connection_that_dies_immediately():
    """根因 B：`send(subscribe)` 之后立刻复位退避 ⇒ 故障期间以 ~1s 的节奏反复建连，把自己拉黑。

    判据用**建连时刻的间隔**：只要退避真的在增长，第三次与第二次之间的间隔就必须严格大于
    第二次与第一次之间的间隔（注入确定性 rand 后即公式本身，此处直接看行为）。
    """
    conns = [_Conn([], then="close") for _ in range(8)]
    factory = _Factory(conns)
    events: list[StreamEvent] = []
    sup = _make(_settings(okx_link_ws_backoff_base_s=0.01, okx_link_ws_backoff_max_s=1.0,
                          okx_link_ws_healthy_after_s=5.0), factory, events)

    await _run_briefly(sup, 0.6)

    assert factory.calls >= 4
    gaps = [b - a for a, b in zip(factory.at, factory.at[1:])]
    assert gaps[-1] > gaps[0], f"退避未增长（连上就断被误判成成功）：{gaps}"


async def test_a_connection_that_delivered_data_resets_the_backoff():
    """反过来也要成立：真的健康过（ack 齐 + 收到过业务帧）就该复位，否则一次抖动会永久拖慢重连。"""
    events: list[StreamEvent] = []
    healthy = _Conn([_ACK, _DATA], then="close")
    factory = _Factory([healthy] + [_Conn([], then="close") for _ in range(6)])
    sup = _make(_settings(okx_link_ws_backoff_base_s=0.01, okx_link_ws_backoff_max_s=1.0),
                factory, events)

    await _run_briefly(sup, 0.3)

    states = [e.state for e in events]
    assert "live" in states, "收到业务帧即 live（这是唯一对上层意味着「行情可信」的状态）"


# ---------------- ④ 建连闸只作用于重连（[评审 P0-2]） ----------------


async def test_the_first_connect_never_takes_a_connect_token():
    """把桶容量设到取不到令牌的程度，首连**仍然**发生——否则会打红既有 WS 用例。

    既有 `test_okx_ws_endpoints.py` 的等待器只 `asyncio.sleep(0)` 空转 200 次，墙钟根本推不到
    0.5s；建连闸若作用于首连，第二个用例的订阅记录就会是空的。
    """
    get_limiters().bucket("ws:connect", 0.0001, 0.0)     # 先占坑：几乎永远拿不到令牌
    conns = [_Conn([_ACK, _DATA])]
    factory = _Factory(conns)
    events: list[StreamEvent] = []
    sup = _make(_settings(okx_link_ws_connect_rate_per_s=0.0001), factory, events)

    await _run_briefly(sup, 0.15)

    assert factory.calls == 1, "首连必须零等待放行"
    assert conns[0].sent[0].startswith('{"op": "subscribe"')


async def test_reconnects_do_take_a_connect_token():
    """重连才是需要被压制的那一半：OKX 对建连有 3 次/秒/IP 的限制。"""
    get_limiters().bucket("ws:connect", 0.0001, 0.0)
    factory = _Factory([_Conn([], then="close") for _ in range(5)])
    events: list[StreamEvent] = []
    sup = _make(_settings(okx_link_ws_connect_rate_per_s=0.0001), factory, events)

    await _run_briefly(sup, 0.2)

    assert factory.calls == 1, "第 2 次建连须被闸挡在令牌桶里"


# ---------------- ⑤ 既有替身的能力面（[评审 P1-10]） ----------------


async def test_runs_against_a_stub_without_recv_or_close():
    """用**不带 `recv`、不带 `close`** 的替身跑通建连→判失活→重连，全程无 `AttributeError`。

    这是「不改既有测试就能通过」的唯一写法：工厂返回值不得 await，读走 `recv` 优先 / `__anext__`
    回退，强制关闭走 `getattr(ws, "close", None)`。
    """
    conns = [_BareConn([_ACK]) for _ in range(4)]
    factory = _Factory(conns)
    events: list[StreamEvent] = []
    sup = _make(_settings(), factory, events)
    assert not hasattr(conns[0], "recv") and not hasattr(conns[0], "close")

    await _run_briefly(sup, 0.4)

    assert factory.calls >= 2


async def test_the_connect_factory_return_value_is_never_awaited():
    """`async with await factory(...)` 会立刻打红三个既有用例——这条把它钉死。"""
    awaited = {"hit": False}

    class _NotAwaitable(_Conn):
        def __await__(self):
            awaited["hit"] = True
            raise AssertionError("connect_factory 的返回值不得被 await")

    factory = _Factory([_NotAwaitable([_ACK, _DATA])])
    events: list[StreamEvent] = []
    sup = _make(_settings(), factory, events)

    await _run_briefly(sup, 0.1)

    assert awaited["hit"] is False


# ---------------- 停止语义 ----------------


async def test_stop_is_idempotent_and_closes_the_channel_in_the_registry():
    """`run()` 退出即 `close_channel`：不这样做，用户换三次标的顶栏就再也回不到「已连接」。"""
    factory = _Factory([_Conn([_ACK, _DATA])])
    events: list[StreamEvent] = []
    sup = _make(_settings(), factory, events)
    stop = asyncio.Event()
    task = asyncio.create_task(sup.run(stop))
    await asyncio.sleep(0.05)

    stop.set()
    await asyncio.wait_for(task, timeout=2)
    stop.set()      # 幂等：再置一次不炸

    assert _CFG.key not in {r["key"] for r in get_link_registry().snapshot()["channels"]}


async def test_a_failing_stream_handler_never_kills_the_connection():
    """观测回调是观测，不是控制：它抛异常绝不许打死一条正在工作的连接。"""
    async def on_raw(_raw: str) -> None:
        return None

    async def boom(_event: StreamEvent) -> None:
        raise RuntimeError("观测层炸了")

    factory = _Factory([_Conn([_ACK, _DATA])])
    sup = WSSupervisor(_CFG, settings=_settings(), connect_factory=factory,
                       on_raw=on_raw, on_stream=boom)

    await _run_briefly(sup, 0.1)

    assert factory.calls == 1
