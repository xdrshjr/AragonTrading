"""断线恢复后的缺根拉取（okx-link-resilience §2.2.4）——**只补证据，不补决策**。

一次 10 分钟的断线会在 K 线序列上留一个洞，而这个洞是最难查的一类错：指标窗口不连续，策略看到
的是一条被悄悄改写过的历史。本模块负责把洞填上，但**只负责取数**——「补进来的根要不要驱动信号 /
下单 / LLM 决策」由三处 session 各自决定（答案一律是「不要」，见 ``okx/session_link.py``）。

两条硬边界：

- **不新增取数路径**：数据源就是既有的 ``rest_client.get_candles``。
- **绝不返回未收线根**：把 forming 根当确认根喂进策略，等于让策略看见一根还在跳的 K 线。
"""
from __future__ import annotations

import time

from .rest_client import OKXRestClient, get_rest_client
from .types import BAR_SECONDS, Candle


def missing_bar_count(since_ts: int | None, bar_seconds: int, now: float) -> int:
    """从 ``since_ts`` 到现在**应该**已经收线的根数（纯函数，供截断判定与文案）。

    ``since_ts`` 为空（会话还没见过任何确认根）时返回 0：那不是空洞，是还没开始。
    """
    if not since_ts or bar_seconds <= 0:
        return 0
    closed_upto = int(now) - bar_seconds
    return max(0, (closed_upto - int(since_ts)) // bar_seconds)


async def fetch_missing(inst_id: str, bar: str, *, since_ts: int | None, max_bars: int,
                        rest: OKXRestClient | None = None) -> list[Candle]:
    """取 ``since_ts`` 之后的已确认历史根（升序，最多 ``max_bars`` 根，不含当前未收线根）。

    Args:
        inst_id: 合约 ID。
        bar: 基础周期。
        since_ts: 会话已见过的最后一根确认根的秒级 ts；``None`` 即无洞可补。
        max_bars: 本次至多补几根（``okx_link_gap_repair_max_bars``；``0`` 即关闭补根）。
        rest: 公共 REST 客户端（默认进程单例；测试注入替身）。

    Returns:
        升序的确认根列表；无洞 / 关闭 / 取不到时为空列表。

    Raises:
        OKXError: 取数失败原样上抛——补根的失败语义由调用方（session 侧独立 task）决定，
            本层**绝不**把一次取数失败伪装成「没有缺根」。
    """
    if max_bars <= 0 or not since_ts:
        return []
    bar_seconds = BAR_SECONDS.get(bar, 60)
    now = time.time()
    missing = missing_bar_count(since_ts, bar_seconds, now)
    if missing <= 0:
        return []
    client = rest or get_rest_client()
    # 多要一根：`/candles` 的最新一根是**未收线**的 forming 根，它会被下面的过滤器丢掉。按缺根数
    # 原样取，每次都会稳定地少补**最老的那一根**，而补完之后 `since_ts` 已经前移——那个洞再也不会
    # 被补上。上界 300 是 OKX 单页上限（也让取数留在 `/candles` 单次分支上）。
    limit = min(min(missing, max_bars) + 1, 300)
    # probe=True：补根与公共读故障高度同源，**恰好**发生在熔断最可能打开的窗口里（§2.3.3 豁免名单）。
    candles = await client.get_candles(inst_id, bar, limit=limit, probe=True)
    closed = [c for c in candles
              if int(c.ts) > int(since_ts) and int(c.ts) + bar_seconds <= now
              and getattr(c, "confirm", True)]
    closed.sort(key=lambda c: c.ts)
    return closed[-max_bars:] if len(closed) > max_bars else closed
