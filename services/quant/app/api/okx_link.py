"""路由：OKX 链路健康只读端点（okx-link-resilience §4.1 / §4.2）。

一屏「连接可见」的后端面。设计立场：**「断线」不是一个需要用户去发现的现象**——顶栏那枚芯片
过去只能说「已连接 / 不可达」，而用户在断线时最想知道的三件事（断的是哪一路、断了多久、系统
正在做什么）它一件也答不了。

两条纪律钉死在本文件：

- **不声明 ``response_model``**（F-21）：声明之后加法键会被 FastAPI 静默剔除，本仓库已为此栽过
  一次（``edgeSource`` 那回）——一个「后端算了但前端永远拿不到」的字段比没有更糟。
- **本端点永不 502**：登记簿是**本地**状态，取不到就是 ``unknown``。一个用来观察故障的端点，
  自己不许在故障时也跟着倒下。
"""
from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter

from ..okx.call_stats import get_call_stats
from ..okx.clock import get_server_clock
from ..okx.link_state import get_link_registry
from ..okx.rest_client import get_rest_client

router = APIRouter(tags=["okx-link"])
logger = logging.getLogger(__name__)

# 手动探测单飞：in-flight 时直接返回当前快照，避免用户连点制造探活风暴。
_probe_lock = asyncio.Lock()


def _snapshot() -> dict:
    """登记簿快照 + 时钟偏移 + 调用统计三个加法键（``clockOffsetMs`` 在面板上解释 50102 类失败）。

    ``callStats``（silent-degradation-repair 轴 D-3）：轴 D-2 把 `httpx` 的 `INFO` 压掉之后，
    「哪个端点最热 / 谁在拒我」这个问题的答案只剩这一处。总闸关 ⇒ ``wire()`` 返回 ``{}`` ⇒
    该键**整段不出现**（逐字节回到今天）。本端点明文禁止声明 ``response_model``，故加法键
    不会被 FastAPI 静默剔除。
    """
    clock = get_server_clock()
    snap = get_link_registry().snapshot()
    snap["clockOffsetMs"] = round(clock.offset_ms)
    snap["clockSyncedTs"] = int(clock.synced_ts) if clock.synced_ts else None
    stats = get_call_stats().wire()
    if stats:
        snap["callStats"] = stats
    return snap


@router.get("/okx/connectivity")
async def get_connectivity() -> dict:
    """当前链路健康快照（只读、零 IO）。已停用的通道**不出现**在 ``channels`` 里。"""
    return _snapshot()


@router.post("/okx/connectivity/probe")
async def probe_connectivity() -> dict:
    """强制一次公共探活 + 一次服务器时钟同步，返回同形快照。

    **只读语义**：不触碰任何私有写路径，因此不需要 ``live_trading_enabled``。两次调用都走熔断
    豁免名单（§2.3.3）——这个端点的全部意义就是「人工强制探一次」，被本地开关挡住等于没有它。
    """
    if _probe_lock.locked():
        return _snapshot()
    async with _probe_lock:
        rest = get_rest_client()
        try:
            await rest.health()
        except Exception as exc:  # noqa: BLE001 —— 探活失败本就是一种结果，已记进登记簿
            logger.info("okx connectivity probe: health failed: %s", exc)
        try:
            # 手动探测**恒**同步一次时钟：`okx_link_clock_sync_interval_s=0` 关的是周期同步，
            # 而这个端点的全部意义就是「人工强制探一次」。
            await get_server_clock().sync(rest.get_server_time_ms)
        except Exception as exc:  # noqa: BLE001 —— 时钟同步失败不该让端点变 502
            logger.info("okx connectivity probe: clock sync failed: %s", exc)
    return _snapshot()
