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
