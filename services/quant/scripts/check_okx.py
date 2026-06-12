"""OKX 连通性预检脚本 (spec §7.1 / §15 step 0，对应 C1 — P0)。

经代理实跑一次 GET /api/v5/market/candles，打印连通性与样例真实行情。
用法（在 services/quant 下）：
    python scripts/check_okx.py
    python scripts/check_okx.py --inst ETH-USDT-SWAP --bar 5m

读取 HTTP_PROXY/HTTPS_PROXY/ALL_PROXY 环境变量（本机 Clash: 127.0.0.1:7897）。
预检失败应先解决网络/代理，再继续后续开发；严禁以假数据冒充真实行情。
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone

import httpx

# Windows 控制台默认 GBK，重配为 UTF-8 以正常打印 ✓ 与中文（spec R10）
try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:  # noqa: BLE001
    pass

OKX_BASE = os.environ.get("OKX_REST_BASE", "https://www.okx.com")


def _proxy() -> str | None:
    return (os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY")
            or os.environ.get("ALL_PROXY") or None)


def main() -> int:
    parser = argparse.ArgumentParser(description="OKX connectivity precheck")
    parser.add_argument("--inst", default="BTC-USDT-SWAP")
    parser.add_argument("--bar", default="1H")
    parser.add_argument("--limit", type=int, default=2)
    args = parser.parse_args()

    proxy = _proxy()
    print(f"[check_okx] base={OKX_BASE} proxy={proxy or '(none)'}")
    url = "/api/v5/market/candles"
    params = {"instId": args.inst, "bar": args.bar, "limit": str(args.limit)}
    try:
        with httpx.Client(base_url=OKX_BASE, timeout=20.0, proxy=proxy) as client:
            resp = client.get(url, params=params)
            resp.raise_for_status()
            body = resp.json()
    except Exception as exc:  # noqa: BLE001
        print(f"[check_okx] ✗ 请求失败: {exc}")
        print("[check_okx] 请检查代理/网络（HTTP_PROXY/HTTPS_PROXY），OKX 可能受地域限制。")
        return 2

    if str(body.get("code")) != "0":
        print(f"[check_okx] ✗ OKX 业务错误 code={body.get('code')} msg={body.get('msg')}")
        return 3

    data = body.get("data", [])
    if not data:
        print("[check_okx] ✗ 返回空数据")
        return 4

    print(f"[check_okx] ✓ 连通成功，取到 {len(data)} 根真实 K 线（{args.inst} {args.bar}）：")
    for row in data:
        ms = int(row[0])
        dt = datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        print(f"    {dt}  O={row[1]} H={row[2]} L={row[3]} C={row[4]} VOL={row[5]} confirm={row[8]}")
    print("[check_okx] ✓ 真实数据通道可用，可进入后续开发/验收。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
