"""测试夹具：临时 SQLite + 假 OKX 客户端（不依赖网络）。"""
from __future__ import annotations

import os
import tempfile
import uuid

import pytest

# 必须在导入任何 app 模块前指定临时 DB 路径
_TMP_DB = os.path.join(tempfile.gettempdir(), f"aragon_test_{uuid.uuid4().hex}.db")
os.environ["DB_PATH"] = _TMP_DB
os.environ["ALLOW_CACHED_FALLBACK"] = "false"


def make_synthetic_candles(n: int = 200, start_ts: int = 1_700_000_000,
                           step: int = 3600, base: float = 100.0) -> list[dict]:
    """生成确定性合成 K 线（含一段下跌+长下影 与 一段上涨）。"""
    candles = []
    price = base
    for i in range(n):
        # 制造波动：正弦 + 漂移
        import math
        drift = math.sin(i / 8.0) * 5.0
        o = price
        c = base + drift + i * 0.05
        h = max(o, c) + 1.5
        l = min(o, c) - 1.5
        # 周期性插入长下影线（锤子）
        if i % 37 == 0 and i > 0:
            l = min(o, c) - 8.0
            c = max(o, c)
        vol = 1000 + (i % 10) * 50
        candles.append({"ts": start_ts + i * step, "o": round(o, 2), "h": round(h, 2),
                        "l": round(l, 2), "c": round(c, 2), "vol": float(vol)})
        price = c
    return candles


@pytest.fixture
def synthetic_candles() -> list[dict]:
    return make_synthetic_candles()


@pytest.fixture(autouse=True)
def _reset_okx_link_singletons():
    """每个用例前后重置 OKX 链路的四个进程单例（okx-link-resilience [评审 P1-11]）。

    本轮新增了 4 个进程级单例（健康登记簿 / 令牌桶登记簿 / 私有连接池 / 服务器时钟），而本文件
    此前**没有任何 autouse 夹具**——不重置的话它们会跨用例污染：一个用例耗尽的令牌会让下一个
    用例真的等半秒（而既有 WS 用例的等待器只 ``asyncio.sleep(0)`` 空转，墙钟根本推不动），
    一个用例登记的失败会让下一个用例读到 ``down``。

    ``import`` 放在函数体内：夹具在收集期就会被装配，而模块级导入 app 包会在**设置 DB_PATH 之前**
    触发 ``app.config`` 的加载顺序问题。
    """
    from app.okx.client_pool import get_client_pool
    from app.okx.clock import get_server_clock
    from app.okx.link_state import get_link_registry
    from app.okx.rate_limit import get_limiters

    singletons = [get_link_registry(), get_limiters(), get_client_pool(), get_server_clock()]
    for s in singletons:
        s.reset()
    yield
    for s in singletons:
        s.reset()
