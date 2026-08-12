"""令牌桶单测（okx-link-resilience §2.3.4）。时钟注入假 monotonic，**零真实 sleep**。"""
from __future__ import annotations

import asyncio

from app.okx.rate_limit import LimiterRegistry, TokenBucket, get_limiters


class _FakeClock:
    """可手推的单调时钟；`asyncio.sleep` 被替身接管后由它代表墙钟前进。"""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


async def _drain(bucket: TokenBucket, clock: _FakeClock, n: int) -> list[float]:
    """连取 n 次令牌；桶内的等待由假时钟“兑现”，故整段无真实墙钟开销。"""
    waits: list[float] = []
    real_sleep = asyncio.sleep

    async def fake_sleep(delay: float) -> None:
        clock.advance(delay)
        await real_sleep(0)

    original = asyncio.sleep
    asyncio.sleep = fake_sleep      # type: ignore[assignment]
    try:
        for _ in range(n):
            waits.append(await bucket.acquire())
    finally:
        asyncio.sleep = original    # type: ignore[assignment]
    return waits


async def test_burst_is_free_then_the_bucket_paces_at_the_configured_rate():
    """突发容量内不等待；耗尽后按速率放行（这正是它比「0.12s 最小间隔」好的那一点）。"""
    clock = _FakeClock()
    bucket = TokenBucket(rate_per_s=4.0, burst=2.0, clock=clock)

    waits = await _drain(bucket, clock, 4)

    assert waits[0] == 0.0 and waits[1] == 0.0        # burst=2 先放行两个
    assert waits[2] > 0 and waits[3] > 0              # 之后按 4/s 排队
    assert sum(waits) == 0.5                           # 2 个额外令牌 ÷ 4/s


async def test_rate_zero_disables_the_limiter_entirely():
    """`rate_per_s <= 0` = 关掉这个闸（本轮「每个机制都有一个关得掉的取值」纪律的落点）。"""
    bucket = TokenBucket(rate_per_s=0.0, burst=0.0, clock=_FakeClock())
    for _ in range(50):
        assert await bucket.acquire() == 0.0
    assert bucket.try_acquire() is True


async def test_refill_over_time_restores_the_burst():
    clock = _FakeClock()
    bucket = TokenBucket(rate_per_s=2.0, burst=2.0, clock=clock)
    await _drain(bucket, clock, 2)

    clock.advance(10.0)                                # 空闲十秒

    waits = await _drain(bucket, clock, 2)
    assert waits == [0.0, 0.0]                         # 桶回满（且被 burst 夹住，不会攒无限令牌）


async def test_concurrent_acquire_is_first_come_first_served():
    """桶内等待持锁授时 ⇒ 先到先得（与既有 `_RateLimiter` 的公平性一致）。

    这条用**真实**时钟跑（速率 500/s ⇒ 每跳 2ms，整段 <20ms）：公平性是锁的性质，而假时钟需要
    接管全局 `asyncio.sleep` 才推得动，那反过来会把补令牌循环的收敛性掩盖掉——正是本轮实测到的
    浮点空转（见 spec §十一 实施记录 D-6）。
    """
    bucket = TokenBucket(rate_per_s=500.0, burst=1.0)
    order: list[int] = []

    async def worker(i: int) -> None:
        await bucket.acquire()
        order.append(i)

    await asyncio.wait_for(asyncio.gather(*(worker(i) for i in range(5))), timeout=5)

    assert order == [0, 1, 2, 3, 4]


async def test_acquire_converges_even_when_the_clock_barely_moves():
    """回归：补令牌循环必须**一定**收敛。

    `deficit / rate` 睡完之后重算出的令牌数在浮点上常常差最后一个 ulp，严格 `>= n` 会让循环以
    纳秒级步长空转；Windows 的 `time.monotonic()` 分辨率约 15.6ms，睡 5ms 醒来墙钟可能一点没动，
    没有睡眠下限就是一个真正的热循环。两条一起构成「速率闸自己把进程转死」这种最难查的故障。
    """
    clock = _FakeClock()
    bucket = TokenBucket(rate_per_s=100.0, burst=1.0, clock=clock)
    await bucket.acquire()          # 先吃掉 burst

    import app.okx.rate_limit as rl

    original = asyncio.sleep   # 必须先抓住原件：`rl.asyncio` **就是**全局 asyncio 模块

    # 时钟只以「刚好差一个 ulp」的步长前进（模拟粗分辨率 + 浮点亏空）
    async def stingy_sleep(delay: float) -> None:
        clock.advance(delay * (1 - 1e-12))
        await original(0)

    rl.asyncio.sleep = stingy_sleep     # type: ignore[assignment]
    try:
        waited = await asyncio.wait_for(bucket.acquire(), timeout=5)
    finally:
        rl.asyncio.sleep = original     # type: ignore[assignment]

    assert waited > 0


async def test_a_bucket_smaller_than_the_request_still_lets_it_through():
    """回归：`burst < n`（例如把 `OKX_LINK_PUBLIC_BURST` 配成 0）不许把进程挂死。

    令牌数被 `min(burst, …)` 夹住，桶里就永远攒不到判据线，补令牌循环便以 1ms 的步长转到天荒
    地老——又一个「用来限速的闸自己把进程转死」，与 I-2 同形。容量不足时应退化为纯速率限制
    （每次等满 `n / rate` 秒），而不是永不放行。
    """
    clock = _FakeClock()
    bucket = TokenBucket(rate_per_s=10.0, burst=0.0, clock=clock)

    waits = await asyncio.wait_for(_drain(bucket, clock, 2), timeout=5)

    assert all(w > 0 for w in waits), "容量为 0 ⇒ 每一次都得等满一个令牌的时间"
    assert sum(waits) == 0.2
    assert bucket.try_acquire() is False, "非阻塞取法同样不许假装拿得到"


def test_registry_returns_the_same_bucket_per_scope_and_resets():
    """同 scope 复用同一个桶（进程级共享物），`reset()` 供 autouse 夹具隔离用例。"""
    registry = LimiterRegistry()
    first = registry.bucket("public", 8.0, 4.0)
    again = registry.bucket("public", 999.0, 999.0)    # 参数以首次登记为准
    other = registry.bucket("ws:connect", 2.0, 2.0)

    assert first is again
    assert first is not other
    assert again.rate_per_s == 8.0

    registry.reset()
    assert registry.bucket("public", 8.0, 4.0) is not first


def test_get_limiters_is_a_process_singleton():
    assert get_limiters() is get_limiters()
