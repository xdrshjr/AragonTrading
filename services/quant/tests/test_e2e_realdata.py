"""真实数据端到端断言 (F2)：经代理直连真实 OKX，禁止 mock。

默认随 `addopts = "-m 'not network'"` 被排除（不污染离线 CI，C4）；
opt-in 显式启用：`pytest -m network`。
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.network


async def test_real_okx_candles_and_backtest():
    from app.engine.backtester import run_backtest
    from app.okx.rest_client import OKXRestClient
    from app.strategies import STRATEGY_REGISTRY

    client = OKXRestClient()
    try:
        candles = await client.get_candles("BTC-USDT-SWAP", "1H", limit=120)
        # 真实数据：根数充足、ts 升序、秒级 UTC、价格合理量级
        assert len(candles) >= 100
        ts = [c.ts for c in candles]
        assert ts == sorted(ts)
        assert all(c.ts < 2_000_000_000 for c in candles)   # 秒级（非毫秒）
        assert all(c.c > 0 for c in candles)

        # 真实 K 线跑回测：净值非空、起止时间合理、指标齐全
        strat = STRATEGY_REGISTRY["momentum_breakout"]()
        result = run_backtest(strat, candles, "1H", initial_capital=10000,
                              taker_fee_bps=5, slippage_bps=2)
        assert len(result.equity_points) > 0
        assert result.start_ts < result.end_ts
        assert set(result.metrics.keys()) >= {"totalReturn", "sharpe", "maxDrawdown"}
    finally:
        await client.aclose()
