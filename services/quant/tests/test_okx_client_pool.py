"""私有连接池单测（okx-link-resilience §2.4.1）——根因 D，以及 [评审 P1-1] 的阻断级修复。

根因 D：全仓 17 处私有客户端构造点，每次调用都新建一条 TLS 连接。AI 自动交易那条 5 秒一次的
盯市循环，每轮 reconcile 都要做一次完整的 TCP+TLS 握手——握手时延占了请求耗时的大头，代理链路上
握手又是最容易被打断的一段。

P1-1（阻断级）：`_get_client` **不得**把池里的客户端记进 `self._client`。`invalidate(fp)` 关掉底层
`AsyncClient` 之后，仍持有旧引用的实例下一次请求会撞 `Cannot send a request, as the client has
been closed.`——而 `invalidate` 的触发条件正是「传输层异常」，也就是故障期间必然发生，于是
「换代自愈」会变成「换代自爆」。
"""
from __future__ import annotations

import httpx

from app.config import Settings
from app.okx.client_pool import OKXClientPool, fingerprint, get_client_pool
from app.okx.private_client import OKXPrivateClient


def _factory():
    return lambda: httpx.AsyncClient(base_url="https://example.test")


# ---------------- 指纹 ----------------


def test_fingerprint_never_leaks_the_raw_key_and_separates_accounts():
    fp_a = fingerprint("key-A", "https://www.okx.com", False, 15.0)
    fp_b = fingerprint("key-B", "https://www.okx.com", False, 15.0)
    assert fp_a != fp_b
    assert "key-A" not in fp_a and len(fp_a) == 16


def test_fingerprint_separates_demo_base_url_and_timeout_bucket():
    """超时是 `AsyncClient` 的**建构**参数，池化后由首个创建者决定——不并进指纹就会串味。"""
    args = ("key", "https://www.okx.com", False, 15.0)
    assert fingerprint(*args) != fingerprint("key", "https://www.okx.com", True, 15.0)
    assert fingerprint(*args) != fingerprint("key", "https://aws.okx.com", False, 15.0)
    assert fingerprint(*args) != fingerprint("key", "https://www.okx.com", False, 5.0)
    assert fingerprint(*args) == fingerprint("key", "https://www.okx.com", False, 15.04)


# ---------------- 池语义 ----------------


async def test_same_fingerprint_reuses_the_same_instance():
    pool = OKXClientPool()
    a = await pool.acquire("fp1", _factory())
    b = await pool.acquire("fp1", _factory())
    assert a is b
    await pool.aclose_all()


async def test_different_accounts_never_share_a_connection():
    """凭证串号是安全事故，不是性能问题。"""
    pool = OKXClientPool()
    a = await pool.acquire("fp-account-1", _factory())
    b = await pool.acquire("fp-account-2", _factory())
    assert a is not b
    await pool.aclose_all()


async def test_release_keeps_the_connection_alive_for_the_next_caller():
    """调用方仍然「用完就 aclose」，只是不再销毁 TCP 连接——17 处调用点零改动的关键。"""
    pool = OKXClientPool()
    first = await pool.acquire("fp1", _factory(), idle_ttl_s=120.0)
    await pool.release("fp1")
    second = await pool.acquire("fp1", _factory(), idle_ttl_s=120.0)
    assert second is first
    assert not second.is_closed
    await pool.aclose_all()


async def test_ttl_zero_degrades_to_the_old_close_on_release_behaviour():
    """TTL=0 即退化为「用完即关」的既有行为（关得掉的取值，不是第二条代码路径）。"""
    pool = OKXClientPool()
    first = await pool.acquire("fp1", _factory(), idle_ttl_s=0.0)
    await pool.release("fp1")
    assert first.is_closed
    assert await pool.acquire("fp1", _factory(), idle_ttl_s=0.0) is not first
    await pool.aclose_all()


async def test_releasing_a_fingerprint_never_acquired_is_a_no_op():
    """`probe_account` 一类路径存在「构造了客户端但从未发请求就 aclose」的分支。"""
    pool = OKXClientPool()
    await pool.release("never-seen")        # 不炸，且不把引用计数打成负数
    client = await pool.acquire("fp1", _factory())
    await pool.release("fp1")
    await pool.release("fp1")               # 多还一次也不许把计数打负
    assert pool.stats()["fp1"] == 0
    assert not client.is_closed
    await pool.aclose_all()


async def test_invalidate_closes_and_bumps_the_generation():
    pool = OKXClientPool()
    old = await pool.acquire("fp1", _factory())
    await pool.invalidate("fp1")
    assert old.is_closed
    new = await pool.acquire("fp1", _factory())
    assert new is not old and not new.is_closed
    await pool.aclose_all()


async def test_sweep_only_closes_idle_unreferenced_clients():
    pool = OKXClientPool()
    idle = await pool.acquire("fp-idle", _factory(), idle_ttl_s=10.0, now=100.0)
    busy = await pool.acquire("fp-busy", _factory(), idle_ttl_s=10.0, now=100.0)
    await pool.release("fp-idle", now=100.0)

    closed = await pool.sweep(now=200.0)

    assert closed == 1
    assert idle.is_closed and not busy.is_closed
    await pool.aclose_all()


async def test_aclose_all_closes_everything_and_reset_drops_references():
    pool = OKXClientPool()
    a = await pool.acquire("fp1", _factory())
    await pool.aclose_all()
    assert a.is_closed and pool.stats() == {}

    await pool.acquire("fp2", _factory())
    pool.reset()
    assert pool.stats() == {}


def test_get_client_pool_is_a_process_singleton():
    assert get_client_pool() is get_client_pool()


# ---------------- [评审 P1-1] 换代自愈，而不是换代自爆 ----------------


async def test_the_same_private_client_instance_keeps_working_after_invalidate():
    """复现 P1-1：`invalidate` 之后，**同一个** `OKXPrivateClient` 实例的下一次请求走新连接。

    如果 `_get_client` 记忆了 `self._client`，这条会撞
    `RuntimeError: Cannot send a request, as the client has been closed.`
    ——而 `invalidate` 的触发条件正是故障期间必然发生的传输层异常。
    """
    settings = Settings(okx_link_pool_idle_ttl_s=120.0)
    client = OKXPrivateClient("ak", "sk-123456", "pp-123456", settings=settings)

    first = await client._get_client()
    await client._invalidate_pooled()
    assert first.is_closed

    second = await client._get_client()

    assert second is not first
    assert not second.is_closed
    await get_client_pool().aclose_all()


async def test_aclose_returns_the_client_to_the_pool_and_clears_the_reference():
    settings = Settings(okx_link_pool_idle_ttl_s=120.0)
    client = OKXPrivateClient("ak", "sk-123456", "pp-123456", settings=settings)
    pooled = await client._get_client()

    await client.aclose()

    assert client._client is None
    assert not pooled.is_closed, "aclose = release，不销毁 TCP 连接"
    await get_client_pool().aclose_all()


async def test_every_hop_that_took_a_reference_gives_it_back():
    """回归：重试循环里**每一跳**都 `acquire` 一次，只还一次会让引用计数只增不减。

    计数只增不减 ⇒ `sweep` 永远清扫不到空闲连接，`okx_link_pool_idle_ttl_s=0`（用完即关）那条
    退路也永远不生效——一个「关得掉的取值」实际上关不掉。
    """
    settings = Settings(okx_link_pool_idle_ttl_s=120.0)
    client = OKXPrivateClient("ak", "sk-123456", "pp-123456", settings=settings)
    for _ in range(3):                       # 模拟一次三跳重试
        await client._get_client()

    await client.aclose()

    assert get_client_pool().stats()[client._fingerprint()] == 0
    await get_client_pool().aclose_all()


async def test_a_client_that_never_sent_a_request_returns_nothing_to_the_pool():
    """回归：一次都没取过就无脑 `release`，减掉的是**别的实例**正持有的那份引用。

    `probe_account` 一类路径存在「构造了客户端但从未发请求就 aclose」的分支；TTL=0 下那一还会
    把另一个实例正在用的连接当场关掉。
    """
    settings = Settings(okx_link_pool_idle_ttl_s=0.0)
    busy = OKXPrivateClient("ak", "sk-123456", "pp-123456", settings=settings)
    idle = OKXPrivateClient("ak", "sk-123456", "pp-123456", settings=settings)
    pooled = await busy._get_client()

    await idle.aclose()

    assert not pooled.is_closed, "别的实例正在用这条连接"

    await busy.aclose()

    assert pooled.is_closed, "TTL=0 ⇒ 最后一份引用还回去时才关"
    await get_client_pool().aclose_all()


async def test_an_injected_client_is_owned_by_whoever_injected_it():
    """既有签名用例直接给实例塞一个 MockTransport 客户端——那条注入口必须保留，且谁建谁关。"""
    settings = Settings()
    client = OKXPrivateClient("ak", "sk-123456", "pp-123456", settings=settings)
    injected = httpx.AsyncClient(base_url="https://example.test")
    client._client = injected

    assert await client._get_client() is injected
    await client.aclose()

    assert injected.is_closed and client._client is None
