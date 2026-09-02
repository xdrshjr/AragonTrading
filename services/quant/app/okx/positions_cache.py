"""账户级持仓快照：单飞 + TTL + 按凭证指纹分桶（ai-trade-silent-degradation-repair 轴 C）。

它修的事实（§一 E-2）：`GET /api/v5/account/positions` 占了全部出网请求的 **78.3%**
（63.5 次/分 ≈ 每 0.95 秒一次），全量累计 1,318,294 次 200 与 **1,010 次 429**。而
`fetch_live_positions(creds)` 的签名里**根本没有 `instId`**（`trading.py`）——它返回的是
**全账户**持仓，5 个成员的盯市循环各自每 5 秒去拉一次**完全相同**的那一份。

**这不只是浪费。** 令牌桶是按账户的（`private:{fingerprint}`，5 次/秒），所以一次真实下单在最坏
情况下要排在若干份重复快照后面才拿到令牌——背景轮询与下单竞争的是同一个预算，而下单是有时效
的那一个。修法因此不是「把闸调紧」（调紧只会让下单等得更久），而是**把 N 份一模一样的读数合成
1 份**。

**为什么是新模块而不是塞进 `trading.py`**：`trading.py` 已 844 行，越过 CLAUDE.md 的 800 行
红线（`error_text.py` 的诞生就是同一个理由）。
"""
from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class PositionsSnapshot:
    """一份账户级持仓读数。

    `taken_wall` 是这份读数**真实取得**的时刻，**不是**放进缓存的时刻——C-4 的防御纵深
    （`taken_wall >= _last_private_write_wall`）判的正是「这份数据比我那次写更新吗」，
    用入缓存时刻会让一次慢请求的答案看起来比它实际更新。
    """

    rows: tuple[dict, ...]
    taken_wall: float


class PositionsCache:
    """按凭证指纹的账户级持仓快照：单飞 + TTL。

    三条不可协商的语义：

    1. **失败不缓存。** 一次失败的读取绝不写进缓存；等在同一次飞行上的每一个调用者都拿到
       **同一个异常实例**并各自上抛——与今天「每个调用者自己发一次、自己吃自己的异常」在可
       观察行为上等价（`executor.reconcile` 的 `except OKXError` 分支、`_reconcile_fail_streak`
       的累加语义因此逐字不变）。N 个成员本来会各记一次失败，现在共享一次飞行 ⇒ **仍各记一次**
       （异常被每个等待者独立接住），故失败计数的推进速率不变。
    2. **单飞。** TTL 内的并发调用共享同一个 in-flight future，不产生第二次网络往返。这是 5×
       压降的来源，而不是 TTL 本身。
    3. **写即作废。** `invalidate(fingerprint)` 必须在任何一次**成功的私有写**之后被调用，
       否则一次刚成交的开仓可能撞上一份 4 秒前的空快照，而 `_on_reconcile_ok` 会把它合成为
       `external_close` 幻影平仓——那正是 `ai-trade-limit-orders` O-3 已经付过一次代价的形状，
       绝不允许因为一个缓存把它请回来。

    **本类不认识凭证、也不自己算指纹**：指纹一律由调用方（`OKXTradeClient._fingerprint()`）给。
    两份指纹一旦漂移，缓存就会跨账户串数据——那是本轴唯一一个会造成资金后果的实现错误，
    故它只允许有一个产出点（门禁 G-C12 用 AST 钉死 `trading.py` 里不出现第二处 `fingerprint(`）。
    """

    def __init__(self) -> None:
        self._entries: dict[str, PositionsSnapshot] = {}
        self._inflight: dict[str, asyncio.Future] = {}
        # 每个桶的**作废代次**：`invalidate` 自增一格。一次在写**之前**就发出去的飞行，落地时
        # 如果代次已经变了，它承载的仍然是写之前的事实——交给已经在等它的调用者可以（它们要的
        # 就是那一次读），但**绝不许把它写回缓存**：否则「写即作废」会被一次慢请求当场撤销掉，
        # 而随后整个 TTL 窗口里的每一个读者都会拿到一份写之前的持仓。
        self._epochs: dict[str, int] = {}

    async def get(self, fingerprint: str, fetch: Callable[[], Awaitable[list[dict]]], *,
                  ttl_s: float, force: bool = False) -> PositionsSnapshot:
        """取一份账户级持仓快照。

        Args:
            fingerprint: 凭证指纹（`OKXTradeClient._fingerprint()` 的产物，调用方给）。
            fetch: 真正的取数协程（无参；由调用方把凭证绑好）。
            ttl_s: 新鲜度窗口（秒）。`<= 0` ⇒ **完全旁路**，每次都真发。
            force: 强制穿透——**必发一次真实请求，且不写缓存、不动 TTL 计时**。
                后者是刻意的（G-C3）：一次强制读不该把后续所有成员的共享窗口整体前移。

        Returns:
            `PositionsSnapshot`（`rows` 不可变；调用方出口处 `list(...)` 一次再往下传）。

        Raises:
            任何 `fetch` 抛出的异常：**原样上抛且不写缓存**。同一次飞行上的每个等待者各自
            接住同一个异常实例。**取消是唯一的例外**：领飞者被取消时等待者不吃这次取消，
            而是接手自己再取一次（见下方 `except asyncio.CancelledError` 的理由）。
        """
        if force or ttl_s <= 0:
            return PositionsSnapshot(rows=tuple(await fetch()), taken_wall=time.time())
        while True:
            cached = self._entries.get(fingerprint)
            if cached is not None and time.time() - cached.taken_wall < ttl_s:
                return cached
            inflight = self._inflight.get(fingerprint)
            if inflight is None:
                break
            try:
                return await asyncio.shield(inflight)
            except asyncio.CancelledError:
                if not inflight.cancelled():
                    raise            # 被取消的是**我自己**：原样上抛
                continue             # 被取消的是领飞者：它已经退场，本轮由我接手再取一次
        epoch = self._epochs.get(fingerprint, 0)
        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()
        self._inflight[fingerprint] = future
        try:
            rows = await fetch()
        except asyncio.CancelledError:
            # **领飞者被取消，绝不能把取消传染给等待者**：它们是别的成员的盯市轮，谁都没有被
            # 取消。给 future 一个 `cancel()` 而不是 `set_exception`，等待者据此接手重取；
            # 若原样把 CancelledError 喂给它们，一次「停掉某一个成员」就会把同账户其余成员的
            # 循环一起打断——而那正是本轴最不该引入的耦合。
            self._inflight.pop(fingerprint, None)
            if not future.done():
                future.cancel()
            raise
        except BaseException as exc:      # noqa: BLE001 —— 原样上抛；失败**绝不**写缓存
            self._inflight.pop(fingerprint, None)
            if not future.done():
                future.set_exception(exc)
                # 没有任何等待者时，一个带异常且从未被 await 的 future 会在 GC 时打出
                # "Future exception was never retrieved"——一条与本轮无关的噪声告警。
                # 主动取一次即标记为已读取；异常本身仍由下面的 `raise` 原样上抛。
                future.exception()
            raise
        snapshot = PositionsSnapshot(rows=tuple(rows), taken_wall=time.time())
        self._inflight.pop(fingerprint, None)
        if self._epochs.get(fingerprint, 0) == epoch:
            self._entries[fingerprint] = snapshot     # 见 `_epochs`：作废过就不许再写回来
        if not future.done():
            future.set_result(snapshot)
        return snapshot

    def invalidate(self, fingerprint: str) -> None:
        """作废该账户的快照（**任何一次成功的私有写之后**必须调用；见语义 3）。

        只丢缓存条目，**不打断在途飞行**：那一次飞行是在写之前就发出去的，打断它只会让等在
        上面的调用者凭空吃一次失败，而它们下一轮本来就会重新取数。但它落地时**不再写回缓存**
        （代次已变，见 `_epochs`）——否则一次慢请求会把这次作废原地撤销掉。
        """
        self._entries.pop(fingerprint, None)
        self._epochs[fingerprint] = self._epochs.get(fingerprint, 0) + 1

    def reset(self) -> None:
        """仅测试用：清空全部桶（`tests/conftest.py` 的 autouse 夹具调用）。"""
        self._entries.clear()
        self._inflight.clear()
        self._epochs.clear()


_cache: PositionsCache | None = None


def get_positions_cache() -> PositionsCache:
    """进程内单例（与 `get_client_pool()` / `get_limiters()` / `get_call_stats()` 同形）。"""
    global _cache
    if _cache is None:
        _cache = PositionsCache()
    return _cache
