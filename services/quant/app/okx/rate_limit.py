"""令牌桶限速（okx-link-resilience §2.3.4）——替代 ``rest_client._RateLimiter`` 的最小间隔。

与最小间隔的差别只有一处但很关键：**允许小突发**。首屏一次性发出的 4 个公共请求在 0.12s 最小
间隔下会被排成一条队；令牌桶让它们同时出发，而**持续**速率仍被压在同一水平。

``rate_per_s <= 0`` 即**不限速**（等价关闭该闸），这是本轮「每个新机制都有一个关得掉的取值」
纪律的落点，而不是新开一条 if 分支。
"""
from __future__ import annotations

import asyncio
import time
from collections.abc import Callable

# 令牌数比较的浮点容差与单次等待的下限；两者都是为了让补令牌循环**一定**收敛（见 `acquire`）。
_TOKEN_EPSILON = 1e-9
_MIN_SLEEP_S = 0.001


class TokenBucket:
    """单调时钟驱动的令牌桶；桶内等待持锁授时，保证先到先得（与既有节流器的公平性一致）。"""

    def __init__(self, rate_per_s: float, burst: float,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.rate_per_s = float(rate_per_s)
        self.burst = max(0.0, float(burst))
        self._clock = clock
        self._tokens = self.burst
        self._last = clock()
        self._lock = asyncio.Lock()

    async def acquire(self, n: float = 1.0) -> float:
        """取 ``n`` 个令牌，不足即等待。返回**实际等待秒数**（供登记簿与测试断言）。"""
        if self.rate_per_s <= 0:
            return 0.0
        capacity = self._capacity(n)
        async with self._lock:
            waited = 0.0
            while True:
                now = self._clock()
                self._tokens = min(capacity, self._tokens + (now - self._last) * self.rate_per_s)
                self._last = now
                # 判据带 ε：`deficit / rate` 睡完之后重算出来的令牌数在浮点上常常差最后一个
                # ulp（如 0.999999999999），严格 `>= n` 会让循环以纳秒级步长空转下去。
                if self._tokens >= n - _TOKEN_EPSILON:
                    self._tokens = max(0.0, self._tokens - n)
                    return waited
                # 睡眠下限同理但更硬：Windows 的 `time.monotonic()` 默认分辨率约 15.6ms，睡 5ms
                # 醒来后墙钟可能一点没动，没有下限就会退化成一个真正的热循环。
                delay = max(_MIN_SLEEP_S, (n - self._tokens) / self.rate_per_s)
                waited += delay
                await asyncio.sleep(delay)

    def _capacity(self, n: float) -> float:
        """本次取用的有效桶容量：**至少装得下这一次要的量**。

        桶装不下要取的量（``burst < n``，例如把 ``okx_link_public_burst`` 配成 0）时，令牌永远攒
        不到判据线——补令牌循环会以 1ms 的步长永远转下去，一个用来「限速」的闸自己把进程挂死。
        这正是 §十一之二 I-2 已经付过一次代价的形状，不该在另一个入口上再付一次：容量不足即
        退化为纯速率限制（每次都要等满 ``n / rate`` 秒），而不是永不放行。
        """
        return max(self.burst, n)

    def try_acquire(self, n: float = 1.0) -> bool:
        """非阻塞取令牌：够就扣、不够就 ``False``（供「拿不到就先不连」的场景）。"""
        if self.rate_per_s <= 0:
            return True
        now = self._clock()
        self._tokens = min(self._capacity(n), self._tokens + (now - self._last) * self.rate_per_s)
        self._last = now
        if self._tokens >= n - _TOKEN_EPSILON:
            self._tokens = max(0.0, self._tokens - n)
            return True
        return False


class LimiterRegistry:
    """按作用域持有令牌桶（``public`` / ``private:{fp}`` / ``ws:connect``）。

    同一 scope 的参数以**首次**登记为准：桶是进程级共享物，中途改速率会让已在排队的调用看到
    两套口径。需要改参数就重启进程（或在测试里 ``reset()``）。
    """

    def __init__(self) -> None:
        self._buckets: dict[str, TokenBucket] = {}

    def bucket(self, scope: str, rate_per_s: float, burst: float) -> TokenBucket:
        existing = self._buckets.get(scope)
        if existing is not None:
            return existing
        created = TokenBucket(rate_per_s, burst)
        self._buckets[scope] = created
        return created

    def reset(self) -> None:
        """仅测试用：清空全部桶（``tests/conftest.py`` 的 autouse 夹具调用）。"""
        self._buckets.clear()


_limiters: LimiterRegistry | None = None


def get_limiters() -> LimiterRegistry:
    """进程内单例限速登记簿。"""
    global _limiters
    if _limiters is None:
        _limiters = LimiterRegistry()
    return _limiters
