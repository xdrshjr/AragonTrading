"""私有链路连接池（okx-link-resilience §2.4.1）——根因 D 的正面修复。

今天全仓有 **17 处** ``OKXPrivateClient / OKXTradeClient`` 构造点，每个实例在 ``_get_client`` 里
新建一个 ``httpx.AsyncClient``、用完 ``aclose()``。于是 AI 自动交易那条 5 秒一次的盯市循环，每轮
reconcile 都要做一次完整的 TCP+TLS 握手：握手时延占了请求耗时的大头，代理链路上握手又是最容易
被打断的一段（``docs/diagnoses/desktop-paper-batch-start-socket-reset`` 的 ``socket hang up``
就发生在这类瞬时连接上）。

池按**凭证指纹**复用连接，故 17 处调用点一行都不用改。三条纪律：

- **不同账户绝不复用连接**：指纹含 ``sha256(api_key)`` + base_url + demo 标志 + 超时量化档。
- **池内不存任何凭证**：只存 ``httpx.AsyncClient``，签名头每请求现算。
- **失效即换代**：任何传输层异常触发 ``invalidate(fp)``，整池换代而不是让后续每个请求各自超时
  一遍——这正是「笔记本休眠唤醒后所有池化 socket 都是半死的」的解药。
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass

import httpx

logger = logging.getLogger("aragon.okx.pool")

# 池化连接的 httpx 上限：单账户 16 条足够覆盖 8 标的 × 2~3 请求的峰值，keepalive 90s 覆盖
# 5s 盯市循环的空档，避免刚握完手就被对端 idle-close。
POOL_LIMITS = httpx.Limits(max_connections=16, max_keepalive_connections=8,
                           keepalive_expiry=90.0)

# 退役条目的宽限期（秒）：``invalidate`` 之后引用计数因任何原因没有归零时的兜底关闭时限。
# 取值须**大于**单次私有请求的超时上限（``okx_account_timeout_seconds`` 默认 15s），否则宽限期
# 本身就会变成「关掉一个在飞请求」的新触发器——那正是 F3 要消灭的东西。
RETIRE_GRACE_S = 60.0


def fingerprint(api_key: str, base_url: str, demo: bool, timeout_s: float = 0.0) -> str:
    """``sha256(api_key|base|demo|timeout)[:16]``——**绝不**在任何日志 / 回执里出现原始 key。

    ``timeout_s`` 也进指纹：``AsyncClient`` 的超时是**建构**参数，池化后会由首个创建者的
    ``Settings`` 决定；不并进指纹的话，测试或多配置场景下两套超时会串味（代价为零，就加一段）。
    """
    raw = f"{api_key}|{base_url}|{1 if demo else 0}|{round(float(timeout_s), 1)}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


@dataclass
class _Entry:
    client: httpx.AsyncClient
    idle_ttl_s: float
    refcount: int = 0
    last_used: float = 0.0
    generation: int = 0
    retired_at: float | None = None   # F3：被 ``invalidate`` 摘下的时刻（None = 仍在服役）


class OKXClientPool:
    """按指纹持有 ``httpx.AsyncClient`` 的引用计数池。"""

    def __init__(self, *, retire_grace_s: float = RETIRE_GRACE_S) -> None:
        self._entries: dict[str, _Entry] = {}
        # F3：被 ``invalidate`` 摘下但仍有在飞请求的条目，等引用归零（或宽限期到）再关。
        self._retired: list[tuple[str, _Entry]] = []
        self._generations: dict[str, int] = {}
        self._retire_grace_s = float(retire_grace_s)
        self._lock = asyncio.Lock()

    async def acquire(self, fp: str, factory: Callable[[], httpx.AsyncClient], *,
                      idle_ttl_s: float = 120.0, now: float | None = None) -> httpx.AsyncClient:
        """取该指纹的共享客户端（不存在即用 ``factory`` 建一个），引用计数 +1。"""
        client, _generation = await self.acquire_hold(fp, factory, idle_ttl_s=idle_ttl_s, now=now)
        return client

    async def acquire_hold(self, fp: str, factory: Callable[[], httpx.AsyncClient], *,
                           idle_ttl_s: float = 120.0,
                           now: float | None = None) -> tuple[httpx.AsyncClient, int]:
        """同 ``acquire``，另回这份引用所属的**世代**——归还时必须带上它（见 ``release``）。"""
        stamp = now if now is not None else time.monotonic()
        async with self._lock:
            entry = self._entries.get(fp)
            if entry is None:
                entry = _Entry(client=factory(), idle_ttl_s=float(idle_ttl_s),
                               generation=self._generations.get(fp, 0))
                self._entries[fp] = entry
            entry.idle_ttl_s = float(idle_ttl_s)
            entry.refcount += 1
            entry.last_used = stamp
            return entry.client, entry.generation

    async def release(self, fp: str, *, generation: int | None = None,
                      now: float | None = None) -> None:
        """引用计数 −1，**不关闭**连接（TTL=0 时退化为「用完即关」的既有行为）。

        对**未曾 acquire 过**的指纹是 no-op：``probe_account`` 一类路径存在「构造了客户端但从未
        发请求就 aclose」的分支，引用计数不许被打成负数。

        F3（§8.3）：``generation`` 是这份引用被 ``acquire`` 时所属的世代，**必须带上**。
        ``_Entry`` 早就有 ``generation`` 字段，而此前 ``release`` 从不校验它：``invalidate`` 之后，
        其它实例手里那些**旧世代**的引用会被拿去减**新世代**条目的计数——轻则让 ``sweep`` 的判据
        失真，重则在 ``idle_ttl_s == 0``（代码明确支持的「用完即关」退路）下把新条目的计数打到 0，
        **当场关掉一个别人正在用的客户端**，即本轮所修异常的第二条独立触发路径。带上世代之后，
        旧世代的归还会被正确地记到退役条目上，并在它的引用归零时把它关掉。

        ``generation=None`` 表示调用方不记世代（旧签名的兼容取值）：仍按当前条目扣减。
        """
        stamp = now if now is not None else time.monotonic()
        victim: httpx.AsyncClient | None = None
        async with self._lock:
            entry = self._entries.get(fp)
            if entry is not None and (generation is None or generation == entry.generation):
                entry.refcount = max(0, entry.refcount - 1)
                entry.last_used = stamp
                if entry.idle_ttl_s <= 0 and entry.refcount == 0:
                    self._entries.pop(fp, None)
                    self._generations[fp] = entry.generation + 1
                    victim = entry.client
            elif generation is not None:
                victim = self._release_retired_locked(fp, generation)
        if victim is not None:
            await _safe_aclose(victim)

    def _release_retired_locked(self, fp: str, generation: int) -> httpx.AsyncClient | None:
        """把一份**旧世代**的引用还给退役条目；引用归零即交出待关客户端。须在持锁时调用。"""
        for idx, (retired_fp, entry) in enumerate(self._retired):
            if retired_fp != fp or entry.generation != generation:
                continue
            entry.refcount = max(0, entry.refcount - 1)
            if entry.refcount == 0:
                self._retired.pop(idx)
                return entry.client
            return None
        return None

    async def invalidate(self, fp: str, *, now: float | None = None) -> None:
        """把该指纹的连接从表里摘掉并置代：下一次 ``acquire`` 会建一条全新的。

        触发条件是「传输层异常」——也就是故障期间必然发生。从表里摘掉是为了让并发的其它请求
        **下一跳就拿到新客户端**，而不是继续撞一个已经半死的 socket。

        F3（§3.7 / §8.3）：**摘表与关闭必须分开**。``httpx.AsyncClient.aclose()`` 会关掉连接池里
        **正在服务某个请求**的连接；而 ``anyio.ClosedResourceError`` 在语义上恰恰等价于「本进程在
        别人的读还在飞的时候把那条 socket 关掉了」——也就是说，此前这里的立即 ``aclose()`` 不是
        故障的放大器，而是这一类异常的**触发器**：一个成员撞到一次真实抖动，就会把同分组其它成员
        在飞的私有请求一起打成非 httpx 的裸异常。

        摘表已经完整兑现了本方法的承诺（下一跳拿新连接），故关闭推迟到引用归零；引用因任何原因
        没能归零时，由 ``sweep`` 的宽限期（``RETIRE_GRACE_S``）兜底关闭，绝不泄漏。
        """
        stamp = now if now is not None else time.monotonic()
        victim: httpx.AsyncClient | None = None
        async with self._lock:
            entry = self._entries.pop(fp, None)
            self._generations[fp] = (entry.generation if entry else
                                     self._generations.get(fp, 0)) + 1
            if entry is not None:
                if entry.refcount <= 0:
                    victim = entry.client          # 无人在用：立刻关，行为与此前逐字相同
                else:
                    entry.retired_at = stamp
                    self._retired.append((fp, entry))
        if victim is not None:
            await _safe_aclose(victim)

    async def sweep(self, now: float) -> int:
        """关闭空闲超过 TTL 且引用计数为 0 的客户端；返回关闭数量。

        F3：同时为退役条目（``invalidate`` 摘下但引用未归零）做**宽限期兜底关闭**——推迟关闭不
        等于永不关闭，一个永远不会关的退役条目就是一条泄漏的连接。
        """
        async with self._lock:
            victims = [fp for fp, e in self._entries.items()
                       if e.refcount == 0 and e.idle_ttl_s > 0
                       and now - e.last_used > e.idle_ttl_s]
            entries = [self._entries.pop(fp) for fp in victims]
            for fp, entry in zip(victims, entries):
                self._generations[fp] = entry.generation + 1
            keep, expired = [], []
            for retired_fp, retired in self._retired:
                # `retired_at` 由 `invalidate` 在入表前必然打上；判 `is not None` 而不是靠真值，
                # 否则一个恰好为 0.0 的时间戳会被当成「没打过」而永远清扫不到。
                aged = (retired.retired_at is not None
                        and now - retired.retired_at > self._retire_grace_s)
                (expired if aged else keep).append((retired_fp, retired))
            self._retired = keep
            entries += [entry for _fp, entry in expired]
        for entry in entries:
            await _safe_aclose(entry.client)
        return len(entries)

    async def aclose_all(self) -> None:
        """应用关停：关闭全部客户端（含引用尚未归零的退役条目）。"""
        async with self._lock:
            entries = list(self._entries.values()) + [entry for _fp, entry in self._retired]
            self._entries.clear()
            self._retired.clear()
        for entry in entries:
            await _safe_aclose(entry.client)

    def reset(self) -> None:
        """仅测试用：丢弃全部引用（**不** await 关闭，夹具是同步的）。"""
        self._entries.clear()
        self._retired.clear()
        self._generations.clear()

    def stats(self) -> dict[str, int]:
        """池内实例数（观测用，不含任何凭证信息）。"""
        return {fp: entry.refcount for fp, entry in self._entries.items()}


async def _safe_aclose(client: httpx.AsyncClient) -> None:
    """关闭客户端；失败只记日志——关一个已经坏掉的连接失败是常态，不该炸调用方。"""
    try:
        await client.aclose()
    except Exception as exc:  # noqa: BLE001 —— 关闭失败不得影响换代
        logger.debug("okx client pool aclose skipped: %s", exc)


_pool: OKXClientPool | None = None


def get_client_pool() -> OKXClientPool:
    """进程内单例连接池。"""
    global _pool
    if _pool is None:
        _pool = OKXClientPool()
    return _pool
