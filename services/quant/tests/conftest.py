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
    from app.okx.call_stats import get_call_stats
    from app.okx.client_pool import get_client_pool
    from app.okx.clock import get_server_clock
    from app.okx.link_state import get_link_registry
    from app.okx.positions_cache import get_positions_cache
    from app.okx.rate_limit import get_limiters

    # `CallStats`（silent-degradation-repair 轴 D-3）是第 5 个进程单例：不复位的话，一个用例
    # 造出来的调用计数会让下一个用例读到「上一轮的 429」，而它正是 `/okx/connectivity` 的加法键。
    # `PositionsCache`（轴 C）是第 6 个进程单例：不复位的话，一个用例写进去的持仓快照会在
    # TTL 窗口内被下一个用例读到——而它决定的是「这个标的现在有没有仓」，跨用例污染即假绿。
    singletons = [get_link_registry(), get_limiters(), get_client_pool(), get_server_clock(),
                  get_call_stats(), get_positions_cache()]
    for s in singletons:
        s.reset()
    _reset_intel_reject_counter()
    yield
    for s in singletons:
        s.reset()
    _reset_intel_reject_counter()


def _reset_intel_reject_counter() -> None:
    """情报抓取的**进程内**拒因计数器（silent-degradation-repair 轴 B-2）。

    它不是一个带 `reset()` 的对象而是一个模块级 dict，故单列一个复位点。不复位的话，一个用例
    造出来的拒因会漏进下一个用例读到的 `rejectCodesSinceStart`——而那个键正是设置卡上「本次
    运行以来为什么一篇都没抓到」的唯一来源。
    """
    from app.ai_auto.intel import reader as intel_reader

    intel_reader.reset_reject_counts()


@pytest.fixture(autouse=True)
def _reset_ai_trade_pause_state():
    """每个用例前后清掉 AI 自动交易「可续跑契约」的跨用例残留（ai-trade-pause-resume）。

    与 ``_reset_okx_link_singletons`` 同一条理由：``services/quant`` 的用例**共用一个临时 DB**
    与一批进程级单例，不复位就会跨用例污染。本轮之后这条尤其要紧：``init_db()`` 的启动对账会把
    上一个用例残留的 ``running`` run 置成 ``paused`` 并把契约行一并置 ``paused``，于是下一个
    用例的 ``start`` 会被 ``AI_TRADE_PAUSED_GROUP_EXISTS`` **正确地**挡下来——那是生产上正确的
    行为，只是把既有用例之间早已存在的状态泄漏暴露了出来。

    用同步 ``sqlite3`` 而不是 async 引擎：夹具是同步的，而 async 引擎的连接池与创建它的事件
    循环绑定，在夹具里另起一个循环去用它并不安全。表尚未建出（纯单元用例）时静默跳过。
    """
    def _wipe() -> None:
        import sqlite3
        try:
            conn = sqlite3.connect(_TMP_DB, timeout=5)
            try:
                conn.execute("DELETE FROM ai_trade_group_states")
                conn.commit()
            finally:
                conn.close()
        except sqlite3.Error:
            pass          # 表还没建 / 库被占：本夹具只做清扫，绝不因它让用例红

    _wipe()
    yield
    _wipe()
    from app.ai_auto import get_ai_trade_manager

    manager = get_ai_trade_manager()
    manager._paused_ref = None
    manager._hydration_failed = False
