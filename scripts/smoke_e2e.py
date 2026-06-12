"""真实数据端到端冒烟 (F2)：OKX → candles → backtest 串起全链路并断言。

用法（仓库根目录，建议用后端 venv 解释器）：
    services/quant/.venv/Scripts/python scripts/smoke_e2e.py      # Windows
    services/quant/.venv/bin/python scripts/smoke_e2e.py          # POSIX

必须直连真实 OKX（经系统 / Clash 代理），**禁止 mock**；OKX 不可达时显式 FAIL 并提示代理。
退出码：0 = PASS，非 0 = FAIL（CI 可用）。
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# 让脚本能 import services/quant 下的 app.*
ROOT = Path(__file__).resolve().parents[1]
QUANT = ROOT / "services" / "quant"
sys.path.insert(0, str(QUANT))

# Windows 控制台默认 GBK，重配为 UTF-8 以正常打印 ✓ 与中文
try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:  # noqa: BLE001
    pass


async def _run() -> int:
    from app.engine.backtester import run_backtest
    from app.okx.rest_client import OKXError, OKXRestClient
    from app.strategies import STRATEGY_REGISTRY

    client = OKXRestClient()
    try:
        # 1) 真实 K 线
        print("[smoke] 取真实 OKX K 线 BTC-USDT-SWAP 1H ×120 …")
        try:
            candles = await client.get_candles("BTC-USDT-SWAP", "1H", limit=120)
        except OKXError as exc:
            print(f"[smoke] ✗ FAIL OKX 取数失败：{exc}")
            print("[smoke]   请检查代理 / 网络（HTTP_PROXY/HTTPS_PROXY），OKX 可能受地域限制。")
            return 2

        if len(candles) < 100:
            print(f"[smoke] ✗ FAIL K 线根数不足：{len(candles)} < 100")
            return 3
        ts = [c.ts for c in candles]
        if ts != sorted(ts):
            print("[smoke] ✗ FAIL ts 非升序")
            return 4
        if not all(c.ts < 2_000_000_000 for c in candles):
            print("[smoke] ✗ FAIL ts 非秒级（疑似毫秒）")
            return 5
        if not all(c.c > 0 for c in candles):
            print("[smoke] ✗ FAIL 收盘价非正")
            return 6
        print(f"[smoke] ✓ 取到 {len(candles)} 根真实 K 线，ts 升序且为秒级 UTC。")

        # 2) 真实数据跑回测
        strat = STRATEGY_REGISTRY["momentum_breakout"]()
        result = run_backtest(strat, candles, "1H", initial_capital=10000,
                              taker_fee_bps=5, slippage_bps=2)
        if not result.equity_points:
            print("[smoke] ✗ FAIL 回测净值为空")
            return 7
        if not result.start_ts < result.end_ts:
            print("[smoke] ✗ FAIL start_ts 未早于 end_ts")
            return 8
        need = {"totalReturn", "sharpe", "maxDrawdown", "winRate"}
        if not need <= set(result.metrics.keys()):
            print(f"[smoke] ✗ FAIL metrics 字段缺失：{need - set(result.metrics.keys())}")
            return 9
        print(f"[smoke] ✓ 回测通过：净值点 {len(result.equity_points)}，"
              f"totalReturn={result.metrics['totalReturn']:.4f}，"
              f"sharpe={result.metrics['sharpe']:.2f}。")

        print("[smoke] ✓ PASS 真实数据端到端链路可用。")
        return 0
    finally:
        await client.aclose()


def main() -> int:
    return asyncio.run(_run())


if __name__ == "__main__":
    sys.exit(main())
