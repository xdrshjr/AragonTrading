"""服务器时钟对齐（okx-link-resilience §2.4.2）——根因 E 的前半。

OKX 对签名请求的 ``OK-ACCESS-TIMESTAMP`` 有 ±30s 容忍，超出即 ``50102``。本机时钟漂移（尤其是
Windows 笔记本休眠唤醒之后）会让**全部**私有请求在同一瞬间一起失败，而归一码 ``OKX_TIME_SKEW``
之后此前没有任何自愈动作——用户只能自己去改系统时间。

本模块只做一件事：记住「服务器时间 − 本机时间」这个偏移，并按它产出签名用的 ISO 时间戳。
**输出格式与既有 ``private_client._iso_timestamp`` 一字不差**，签名串不会因此变化。
"""
from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone

# 单次读数的合理上限：超过 5 分钟的偏移更像是一次坏读（或读到了别人的时钟），
# 据它平移签名时间戳会把**全部**私有请求一起带偏，所以宁可丢弃、保留旧值。
_MAX_OFFSET_MS = 300_000.0


class ServerClock:
    """交易所时钟偏移追踪器。偏移单位毫秒，正数表示服务器比本机快。"""

    def __init__(self) -> None:
        self.offset_ms: float = 0.0
        self.synced_ts: float | None = None

    async def sync(self, fetch: Callable[[], Awaitable[float]]) -> float:
        """取一次服务器毫秒时间并更新偏移；返回**当前生效**的偏移（毫秒）。

        以请求前后本机时刻的中点作为「同一瞬间的本机读数」，抵掉一半的单程时延。
        读数异常（``|offset| > 300s``）一律丢弃并保留旧值——见模块 docstring。

        Args:
            fetch: 返回服务器毫秒时间戳的协程（生产上是 ``rest.get_server_time_ms``）。

        Raises:
            Exception: ``fetch`` 自身的异常原样上抛，由调用方决定要不要吞（周期同步吞、
                ``50102`` 自愈路径也吞——时钟修不好不该把业务请求变成另一种失败）。
        """
        started = time.time()
        server_ms = float(await fetch())
        local_mid_ms = (started + time.time()) / 2.0 * 1000.0
        offset = server_ms - local_mid_ms
        if abs(offset) <= _MAX_OFFSET_MS:
            self.offset_ms = offset
            self.synced_ts = time.time()
        return self.offset_ms

    def now_iso(self) -> str:
        """带偏移的 ISO8601 毫秒 UTC，形如 ``2026-06-22T08:15:42.123Z``（OKX 签名头要求）。"""
        now = datetime.now(timezone.utc) + timedelta(milliseconds=self.offset_ms)
        return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"

    def is_stale(self, interval_s: float, now: float | None = None) -> bool:
        """距上次同步是否已超过一个周期（``interval_s <= 0`` 即只在 50102 时同步）。"""
        if interval_s <= 0:
            return False
        if self.synced_ts is None:
            return True
        return (now if now is not None else time.time()) - self.synced_ts > interval_s

    def reset(self) -> None:
        """仅测试用：偏移归零（``tests/conftest.py`` 的 autouse 夹具调用）。"""
        self.offset_ms = 0.0
        self.synced_ts = None


_clock: ServerClock | None = None


def get_server_clock() -> ServerClock:
    """进程内单例服务器时钟。"""
    global _clock
    if _clock is None:
        _clock = ServerClock()
    return _clock
