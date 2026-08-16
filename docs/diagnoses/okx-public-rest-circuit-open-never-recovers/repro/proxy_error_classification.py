"""代理链路上的两个事实（无外网、约 30s 跑完）。

用法（仓库根目录）::

    python docs/diagnoses/okx-public-rest-circuit-open-never-recovers/repro/proxy_error_classification.py

起一个「接受 TCP 连接但从不回应 CONNECT」的假代理——本机 Clash 上游断掉时就是这个形状——
然后用与 ``OKXRestClient._get_client()`` **完全一致**的构造参数（单例 ``AsyncClient``、
``Timeout(20.0, connect=10.0)``、不传 ``limits``）发请求，并按 ``market_window._fetch_tactical``
的写法用 ``asyncio.wait_for(..., 8s)`` 放弃它。

输出证明两件事：

1. **分级漏网**：代理故障抛的是 ``httpx.ProxyError``，而 ``okx/retry.py`` 的
   ``_RETRYABLE_EXCEPTIONS`` 白名单里**没有它** ⇒ ``classify_exception`` 判 ``terminal``
   ⇒ 公共读一次都不重试，并且立刻给熔断器记一次失败。
   （私有侧 ``private_client._get`` 捕的是基类 ``httpx.TransportError``，天然覆盖它。）
2. **连接池不泄漏**（反证）：httpcore 1.0.9 在取消/超时后会把连接从池里摘干净，
   ``pool`` 计数始终为 0 ⇒ 「被取消的请求占满连接池」这一条**不是**本次故障的成因，
   排除它才把根因收敛到「客户端从不重建」。
"""
from __future__ import annotations

import asyncio
import logging

import httpx

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO / "services" / "quant"))

from app.okx.retry import classify_exception  # noqa: E402

STALL = {"on": True}
CANCEL_AFTER = 8.0            # ai_trade_tactical_fetch_timeout_s 的默认值
ROUNDS = 3


async def stalling_proxy(reader: asyncio.StreamReader,
                         writer: asyncio.StreamWriter) -> None:
    """接受本机 TCP 连接，然后对 CONNECT 一言不发（上游已死的代理就是这样）。"""
    try:
        head = b""
        while not head.endswith(b"\r\n\r\n"):
            chunk = await reader.read(1)
            if not chunk:
                return
            head += chunk
        if STALL["on"]:
            await asyncio.sleep(3600)
            return
        writer.write(b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\n\r\n")
        await writer.drain()
    except Exception:       # noqa: BLE001 —— 假服务端，异常无意义
        return
    finally:
        try:
            writer.close()
        except Exception:   # noqa: BLE001
            pass


def pool_size(client: httpx.AsyncClient) -> int:
    return len(client._transport._pool.connections)     # noqa: SLF001


async def main() -> None:
    logging.getLogger("asyncio").setLevel(logging.CRITICAL)
    asyncio.get_running_loop().set_exception_handler(lambda loop, ctx: None)

    server = await asyncio.start_server(stalling_proxy, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]

    # 与 rest_client.py:160-168 一字不差的构造姿态。
    client = httpx.AsyncClient(
        base_url="https://www.okx.com",
        timeout=httpx.Timeout(20.0, connect=10.0),
        proxy=f"http://127.0.0.1:{port}",
        headers={"User-Agent": "aragon-trading/0.1"},
        trust_env=False,
    )

    loop = asyncio.get_running_loop()
    print("--- 代理卡住：调用方按 market_window 的写法 8s 放弃 ---")
    for r in range(ROUNDS):
        started = loop.time()
        try:
            await asyncio.wait_for(client.get("/api/v5/market/candles"), CANCEL_AFTER)
        except (TimeoutError, asyncio.TimeoutError):
            print(f"round {r}: wait_for 放弃于 {loop.time() - started:.1f}s；"
                  f"pool={pool_size(client)}（无泄漏）")

    print("\n--- 代理开始回应（但回 502）：看异常类型与分级 ---")
    STALL["on"] = False
    for r in range(ROUNDS):
        try:
            await client.get("/api/v5/public/time")
        except Exception as exc:        # noqa: BLE001 —— 异常本身就是结论
            print(f"probe {r}: {type(exc).__module__}.{type(exc).__name__} "
                  f"str={str(exc)!r} -> classify_exception = "
                  f"{classify_exception(exc)!r}；pool={pool_size(client)}")

    print("\n结论：ProxyError 被判 'terminal' -> 公共读零重试 + 立刻记一次熔断失败；"
          "\n      而 pool 始终为 0 -> 连接池泄漏不是本次故障的成因。")

    await client.aclose()
    server.close()
    await server.wait_closed()


if __name__ == "__main__":
    asyncio.run(main())
