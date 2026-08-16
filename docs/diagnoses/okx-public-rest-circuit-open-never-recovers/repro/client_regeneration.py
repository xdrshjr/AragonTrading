"""主因（§3.4）的最小复现与修复验收：公共 ``AsyncClient`` 卡住之后到底能不能自愈。

用法（仓库根目录）::

    python docs/diagnoses/okx-public-rest-circuit-open-never-recovers/repro/client_regeneration.py

**无外网、无 OKX 账户、约 15 秒跑完。**

复现的是 §3.4 评审注收窄后的那个机制：**连接池槽位账目被占满 ⇒ ``PoolTimeout``**。脚本起一个本机
HTTP 服务端，它对 ``/stall`` 一言不发、对其它路径秒回合法的 OKX 报文；再用一条 ``max_connections=1``
的真实 ``httpx.AsyncClient`` 把那唯一的槽位永久占住。此后**这个客户端**的每一次请求都只能在
``wait_for_connection`` 上等到 pool 超时，抛出一个 ``str()`` 为空串的 ``PoolTimeout`` ——
恒定耗时、异常文本恒空、进程级、公共侧独有、换个客户端就好，与生产四项观测逐条同形。

服务端**全程健康**，所以两条臂的差别只可能来自客户端本身：

- **对照臂（修复前的行为）**：把 ``_invalidate_client`` 换成 no-op。客户端永不换代 ⇒ 连续失败把
  熔断顶开 ⇒ 下单面板拿到那句 ``加载下单上下文失败：交易所连接暂时熔断…``；而且**连被豁免熔断的
  探针也一样失败**，于是熔断永远等不到那一次能让它复位的成功——4/4 生产事故的唯一出口是重启进程。
- **实验臂（F1′）**：第一次传输失败即换代 ⇒ 下一跳用的是一条全新的连接池 ⇒ **立刻**恢复，
  熔断一次都没打开，全程不重启进程。

Windows 控制台若为 GBK 代码页，中文会显示成乱码，属终端编码问题，不影响结论；
需要正常显示可先执行 ``chcp 65001``。
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO / "services" / "quant"))
os.chdir(REPO / "services" / "quant")

import httpx                                                    # noqa: E402
from app.config import Settings                                 # noqa: E402
from app.okx.link_state import get_link_registry                # noqa: E402
from app.okx.rest_client import OKXError, OKXRestClient          # noqa: E402

# 槽位等待超时；生产是 20.0s（`httpx.Timeout(20.0, connect=10.0)` 的 pool 档），这里压到 2s
# 只为让脚本跑得完，机制一字不差。
POOL_TIMEOUT_S = 2.0
INSTRUMENTS = "/api/v5/public/instruments"

_INSTRUMENT_ROW = {"instId": "BTC-USDT-SWAP", "ctVal": "0.01", "ctValCcy": "BTC",
                   "lotSz": "1", "minSz": "1", "tickSz": "0.1", "lever": "100",
                   "ctType": "linear", "settleCcy": "USDT"}


async def okx_stub(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    """本机 OKX 替身：``/stall`` 永不回应，其余路径秒回合法报文。**服务端全程是健康的。**"""
    try:
        while True:
            head = b""
            while not head.endswith(b"\r\n\r\n"):
                chunk = await reader.read(1)
                if not chunk:
                    return
                head += chunk
            path = head.split(b" ", 2)[1].decode()
            if path.startswith("/stall"):
                await asyncio.sleep(3600)      # 槽位就是被这一跳永久占住的
                return
            payload = {"code": "0", "data": [_INSTRUMENT_ROW]}
            if path.startswith("/api/v5/public/time"):
                payload = {"code": "0", "data": [{"ts": "1700000000000"}]}
            body = json.dumps(payload).encode()
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                         b"Content-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body)
            await writer.drain()
    except Exception:            # noqa: BLE001 —— 假服务端，异常无意义
        return
    finally:
        try:
            writer.close()
        except Exception:        # noqa: BLE001
            pass


class LocalRest(OKXRestClient):
    """真实 ``OKXRestClient``，只把建构参数换成「打本机、单槽位、2 秒超时」。"""

    def __init__(self, port: int, **over) -> None:
        base = dict(okx_rest_base=f"http://127.0.0.1:{port}",
                    https_proxy="", http_proxy="", all_proxy="",
                    okx_link_rest_max_attempts=1, okx_link_rest_budget_s=6.0,
                    okx_link_public_rate_per_s=0.0, okx_link_public_burst=0.0,
                    okx_link_breaker_failures=5, okx_link_breaker_cooldown_s=20.0)
        base.update(over)
        super().__init__(settings=Settings(**base))
        self.generations = 0

    def _new_client(self) -> httpx.AsyncClient:
        self.generations += 1
        return httpx.AsyncClient(
            base_url=self._settings.okx_rest_base,
            timeout=httpx.Timeout(POOL_TIMEOUT_S),
            limits=httpx.Limits(max_connections=1, max_keepalive_connections=1),
            trust_env=False,
        )


async def wedge(rest: LocalRest) -> asyncio.Task:
    """把这条客户端唯一的连接槽位永久占住（返回那个永不结束的请求任务）。"""
    client = await rest._get_client()                              # noqa: SLF001
    holder = asyncio.create_task(client.get("/stall", timeout=httpx.Timeout(None)))
    await asyncio.sleep(0.3)
    return holder


async def read_once(rest: LocalRest, tag: str) -> bool:
    """一次真实的下单面板取数（`/api/v5/public/instruments`）。返回是否成功。"""
    try:
        await rest.get_instrument_meta("SWAP", "BTC-USDT-SWAP")
    except OKXError as exc:
        print(f"  {tag}: [{exc.code}] {exc.message}")
        return False
    print(f"  {tag}: 200 OK（下单面板可用）")
    return True


def link_row() -> dict:
    return get_link_registry().snapshot()["channels"][0]


async def control_arm(port: int) -> None:
    """对照臂：客户端永不换代（修复前的行为）。"""
    print("=== 对照臂：公共 AsyncClient 永不换代（修复前） ===")
    rest = LocalRest(port)
    rest._invalidate_client = lambda stale: asyncio.sleep(0)       # noqa: SLF001
    holder = await wedge(rest)

    for i in range(1, 7):
        await read_once(rest, f"read #{i}")
    row = link_row()
    print(f"  链路面板：{row['label']} state={row['state']} circuit={row['circuit']}")

    print("  被豁免熔断的探针（/api/health 与 60s 清扫协程）也一样失败：")
    for i in range(1, 3):
        try:
            await rest.get_server_time_ms()
            print(f"  probe #{i}: 200 OK")
        except OKXError as exc:
            print(f"  probe #{i}: [{exc.code}] {exc.message}")
    # 打印一律用 ASCII 箭头：Windows 控制台默认 GBK，`⇒`（U+21D2）编不出来，脚本会在这一行
    # 以 `UnicodeEncodeError` 崩在半途——正文里那条 PowerShell 运行命令就是这么跑的。
    print(f"  客户端代数={rest.generations} => 探针永远失败 => 熔断等不到那次让它复位的成功。")
    print("  出口只剩：重启后端进程。\n")

    holder.cancel()
    await rest.aclose()


async def treatment_arm(port: int) -> None:
    """实验臂：F1′ 失效即换代。"""
    print("=== 实验臂：F1′ 传输失败即换代 ===")
    get_link_registry().reset()
    rest = LocalRest(port)
    holder = await wedge(rest)

    ok = [await read_once(rest, f"read #{i}") for i in range(1, 4)]
    row = link_row()
    print(f"  链路面板：{row['label']} state={row['state']} circuit={row['circuit']}")
    print(f"  客户端代数={rest.generations}（第一次传输失败即换代，下一跳用的是全新连接池）")
    print(f"  熔断 failures={rest._breaker.failures} opened_until={rest._breaker.opened_until:g}"
          "  => 一次都没打开，全程未重启进程。\n")     # noqa: SLF001
    assert ok[1:] == [True, True], "换代之后必须立刻恢复"

    holder.cancel()
    await rest.aclose()


async def main() -> None:
    asyncio.get_running_loop().set_exception_handler(lambda loop, ctx: None)
    server = await asyncio.start_server(okx_stub, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    print(f"本机 OKX 替身已启动于 127.0.0.1:{port}（服务端全程健康）\n")

    await control_arm(port)
    await treatment_arm(port)

    server.close()
    await server.wait_closed()


if __name__ == "__main__":
    asyncio.run(main())
