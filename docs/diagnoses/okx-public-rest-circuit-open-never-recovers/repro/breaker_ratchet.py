"""公共读熔断器「棘轮」最小复现（无网络、无 OKX 账户、秒级跑完）。

用法（仓库根目录）::

    python docs/diagnoses/okx-public-rest-circuit-open-never-recovers/repro/breaker_ratchet.py

复用**真实**的 ``app.okx.rest_client.OKXRestClient`` / ``_Breaker``，只把「单次网络尝试」替换成
一个恒失败的替身（``httpx.ReadTimeout("")`` —— 生产日志里那 1748 行冒号后什么都没有的失败）。

它证明四件事：

1. 连续 5 次公共读失败即熔断打开，冷却 20s（``config.okx_link_breaker_failures/cooldown_s``）。
2. ``_Breaker.failures`` 在 ``_open()`` 时**不清零**，于是此后**每一次**失败都会再次 ``_open()``，
   把窗口重新推满。
3. 每一次半开探针失败都让冷却翻倍：20 → 40 → 80 → 120（封顶）。而做这个翻倍的失败，恰恰来自
   被**豁免**熔断的探针（60s 链路清扫协程的 ``get_server_time_ms``、15s 的 ``/api/health``）——
   当初豁免它们是为了让它们把熔断治好，实际效果相反。
   第三次翻倍之后窗口（120s）已长于探针间隔（80s），普通公共读从此再也拿不到半开名额。
4. 用户看到的 502 文案由 ``ensure_closed_or_raise`` 逐字产出。

修复（F2）之后再跑本脚本，第 2、3 两条应当**翻转**：``failures`` 在开闸时归零、探针失败不再喂
熔断器，于是冷却恒为 20s，不再出现 20 → 40 → 80 → 120 的单调上膛；第 4 条（502 文案）不变。

Windows 控制台若为 GBK 代码页，中文会显示成乱码，属终端编码问题，不影响结论；
需要正常显示可先执行 ``chcp 65001``。
"""
from __future__ import annotations

import asyncio
import contextlib
import os
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO / "services" / "quant"))
os.chdir(REPO / "services" / "quant")

import httpx                                            # noqa: E402
from app.okx.rest_client import OKXError, OKXRestClient  # noqa: E402


class FailingRest(OKXRestClient):
    """每一次网络尝试都按「代理隧道已死」的形状失败：20s 后 ReadTimeout，且**消息为空**。"""

    async def _attempt(self, path, params):
        return "retryable", None, httpx.ReadTimeout(""), None


def state(breaker) -> str:
    remaining = (max(0.0, breaker.opened_until - time.monotonic())
                 if breaker.opened_until else 0.0)
    return (f"failures={breaker.failures} cooldown={breaker._cooldown:g}s "
            f"open_for={remaining:.0f}s")


async def main() -> None:
    rest = FailingRest()
    breaker = rest._breaker                              # noqa: SLF001
    print(f"config: threshold={breaker._threshold} "
          f"base_cooldown={breaker._base_cooldown:g}s\n")

    print("--- 普通公共读（非探针）：熔断器计数 ---")
    for i in range(1, 7):
        try:
            await rest.get_ticker("BTC-USDT-SWAP")
        except OKXError as exc:
            print(f"read #{i}: [{exc.code}] {exc.message!r}")
        print(f"        {state(breaker)}")

    print("\n--- 故障持续；只剩被豁免的探针还能真的发出请求 ---")
    print("    （60s 链路清扫协程的 /api/v5/public/time，以及 /api/health）")
    for tick in range(1, 5):
        breaker.opened_until = time.monotonic() - 0.001   # 假装冷却刚好走完
        # 修复后半开名额**只发一个**且带租期，所以第二跳起这里会如实抛 OKX_CIRCUIT_OPEN；
        # 探针本就豁免熔断、从不申请半开名额，照发不误。
        with contextlib.suppress(OKXError):
            breaker.ensure_closed_or_raise("/api/v5/public/time")   # 翻半开，放行探针
        try:
            await rest.get_server_time_ms()
        except OKXError as exc:
            print(f"probe #{tick}: [{exc.code}] {exc.message!r}")
        print(f"        {state(breaker)}")

    print("\n--- 窗口armed期间下单面板拿到的东西 ---")
    try:
        breaker.ensure_closed_or_raise("/api/v5/public/instruments")
    except OKXError as exc:
        print("GET /api/trading/config -> HTTP 502")
        print(f'  {{"error": {{"code": "{exc.code}", "message": "{exc.message}"}}}}')
        print(f"  UI: 加载下单上下文失败：{exc.message}")

    await rest.aclose()


if __name__ == "__main__":
    asyncio.run(main())
