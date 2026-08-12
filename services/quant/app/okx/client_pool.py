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


class OKXClientPool:
    """按指纹持有 ``httpx.AsyncClient`` 的引用计数池。"""

    def __init__(self) -> None:
        self._entries: dict[str, _Entry] = {}
        self._generations: dict[str, int] = {}
        self._lock = asyncio.Lock()

    async def acquire(self, fp: str, factory: Callable[[], httpx.AsyncClient], *,
                      idle_ttl_s: float = 120.0, now: float | None = None) -> httpx.AsyncClient:
        """取该指纹的共享客户端（不存在即用 ``factory`` 建一个），引用计数 +1。"""
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
            return entry.client

    async def release(self, fp: str, *, now: float | None = None) -> None:
        """引用计数 −1，**不关闭**连接（TTL=0 时退化为「用完即关」的既有行为）。

        对**未曾 acquire 过**的指纹是 no-op：``probe_account`` 一类路径存在「构造了客户端但从未
        发请求就 aclose」的分支，引用计数不许被打成负数。
        """
        stamp = now if now is not None else time.monotonic()
        async with self._lock:
            entry = self._entries.get(fp)
            if entry is None:
                return
            entry.refcount = max(0, entry.refcount - 1)
            entry.last_used = stamp
            if entry.idle_ttl_s <= 0 and entry.refcount == 0:
                self._entries.pop(fp, None)
                self._generations[fp] = entry.generation + 1
                await _safe_aclose(entry.client)

    async def invalidate(self, fp: str) -> None:
        """关闭该指纹的连接并置代：下一次 ``acquire`` 会建一条全新的。

        触发条件是「传输层异常」——也就是故障期间必然发生。之所以立刻从表里摘掉再关闭，是为了
        让并发的其它请求**下一跳就拿到新客户端**，而不是继续撞一个已经半死的 socket。
        """
        async with self._lock:
            entry = self._entries.pop(fp, None)
            self._generations[fp] = (entry.generation if entry else
                                     self._generations.get(fp, 0)) + 1
        if entry is not None:
            await _safe_aclose(entry.client)

    async def sweep(self, now: float) -> int:
        """关闭空闲超过 TTL 且引用计数为 0 的客户端；返回关闭数量。"""
        async with self._lock:
            victims = [fp for fp, e in self._entries.items()
                       if e.refcount == 0 and e.idle_ttl_s > 0
                       and now - e.last_used > e.idle_ttl_s]
            entries = [self._entries.pop(fp) for fp in victims]
            for fp, entry in zip(victims, entries):
                self._generations[fp] = entry.generation + 1
        for entry in entries:
            await _safe_aclose(entry.client)
        return len(entries)

    async def aclose_all(self) -> None:
        """应用关停：关闭全部客户端。"""
        async with self._lock:
            entries = list(self._entries.values())
            self._entries.clear()
        for entry in entries:
            await _safe_aclose(entry.client)

    def reset(self) -> None:
        """仅测试用：丢弃全部引用（**不** await 关闭，夹具是同步的）。"""
        self._entries.clear()
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
