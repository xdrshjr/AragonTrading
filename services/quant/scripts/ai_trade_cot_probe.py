"""思维链深度探针（ai-trade-deep-reasoning-chain §4.5 / G-3 的**唯一**权威判据）。

对**同一份真实 payload 集合**分别以 `depth=off`（= 本特性合入前的形态）与 `depth=deep` 各跑
K 轮，输出每轮的 `chars / stages / outputTokens / elapsedMs`，并打印判据行：

    baseline(off)  median output tokens = 487
    deep           median output tokens = 2914
    ratio = 5.98x   → PASS (>= 5.0)

退出码 0=PASS / 1=FAIL。**这是 G-3 对真实模型的唯一权威判据**——离线单测无法度量真实模型的
话痨程度，把 5× 写成离线断言只会得到一条自欺的绿灯。CI 里的影子判据是
`tests/test_ai_trade_reasoning_assembly.py` 的
`test_deep_profile_prompt_is_at_least_five_times_the_off_profile`
（它只能挡住「协议没被装配」与「协议被删剩一句」两类回归）。

opt-in：需要真实 provider 与 API key（读 `.env` 的 `AGENT_*`），会**真的花钱**。不下任何单、
不写任何库、不建任何会话——只是把同一批 payload 喂给两档 prompt 各跑一次。

用法（在 services/quant 下）：
    .venv/Scripts/python scripts/ai_trade_cot_probe.py --inst BTC-USDT-SWAP --bar 30m \\
        --rounds 6 --out ../../docs/plans/ai-trade-deep-reasoning-chain/probe.json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agent.llm.factory import make_llm_client_from_config          # noqa: E402
from app.agent.providers import provider_config_from_settings          # noqa: E402
from app.ai_auto.decider import AiTradeDecider                         # noqa: E402
from app.ai_auto.llm_budget import build_ai_trade_llm_clients          # noqa: E402
from app.ai_auto.market_context import build_market_context            # noqa: E402
from app.ai_auto.market_digest import build_market_digest              # noqa: E402
from app.ai_auto.prompts import build_ai_trade_system_prompt           # noqa: E402
from app.ai_auto.prompts_en import SystemPromptTexts                   # noqa: E402
from app.ai_auto.reasoning_protocol import (                           # noqa: E402
    build_deliberation_protocol,
    count_stages,
    profile_by_name,
)
from app.config import Settings                                        # noqa: E402
from app.engine.backtester import candles_to_df                        # noqa: E402
from app.okx.types import Candle                                       # noqa: E402

# G-3 的判据阈值（spec §1.1）：output token 中位数之比。
_RATIO_FLOOR = 5.0
# 每轮 payload 的回看窗口（要 ≥ market_digest 的 _MIN_BARS=35，取 120 留足余量）。
_WINDOW = 120

try:                                       # Windows 控制台默认 GBK，重配为 UTF-8
    sys.stdout.reconfigure(encoding="utf-8")        # type: ignore[attr-defined]
    sys.stderr.reconfigure(encoding="utf-8")        # type: ignore[attr-defined]
except Exception:                          # noqa: BLE001
    pass


def _fetch_candles(inst: str, bar: str, limit: int) -> list[Candle]:
    """经公共 REST 取真实 K 线（无需 API key；代理走 HTTP(S)_PROXY 环境变量）。"""
    base = os.environ.get("OKX_REST_BASE", "https://www.okx.com")
    proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY")
    url = f"{base}/api/v5/market/candles"
    with httpx.Client(timeout=20.0, proxy=proxy) as client:
        resp = client.get(url, params={"instId": inst, "bar": bar, "limit": str(limit)})
        resp.raise_for_status()
        rows = resp.json().get("data") or []
    # OKX 返回倒序，转正序；字段 [ts, o, h, l, c, vol, ...]。
    out = [Candle(ts=int(r[0]) // 1000, o=float(r[1]), h=float(r[2]), l=float(r[3]),
                  c=float(r[4]), vol=float(r[5]), confirm=True) for r in reversed(rows)]
    return out


def _payloads(candles: list[Candle], bar: str, settings: Settings, rounds: int) -> list[dict]:
    """把真实 K 线切成 K 份**同一批**决策 payload（两档共用，杜绝「两次跑的不是同一道题」）。"""
    payloads: list[dict] = []
    for k in range(rounds):
        end = len(candles) - (rounds - 1 - k)
        window = candles[max(0, end - _WINDOW):end]
        if len(window) < 40:
            raise SystemExit(f"K 线不足：需要 ≥ {_WINDOW + rounds} 根，实得 {len(candles)}")
        df = candles_to_df(window)
        last = window[-1]
        payloads.append({
            "round": k + 1, "barTs": last.ts, "lastPx": last.c, "position": None,
            "sessionPnlUsd": 0.0, "budgetUsd": 1000.0,
            "takeProfitUsd": None, "stopLossUsd": None,
            "marketContext": build_market_context(df, bar, settings),
            "marketDigest": build_market_digest(df, bar, settings),
            "signalBoard": [{"strategyId": 1, "name": "probe", "stance": "flat",
                             "latestSignal": "open_long", "strength": 1.0,
                             "reason": "probe fixture"}]})
    return payloads


def _build_decider(depth_name: str, inst: str, bar: str, settings: Settings) -> AiTradeDecider:
    """按档位装配一个**与生产同路径**的决策器（同一份 `build_ai_trade_llm_clients`）。"""
    depth = profile_by_name(depth_name)
    cfg = provider_config_from_settings(settings)
    if not (cfg.api_key or "").strip():
        raise SystemExit("未配置 AGENT_API_KEY —— 本探针需要真实 provider（opt-in，会真的花钱）")
    clients = build_ai_trade_llm_clients(cfg, settings, profile=depth,
                                         make_client=make_llm_client_from_config)
    system_prompt = build_ai_trade_system_prompt(
        inst, bar, 1000.0, 3.0,
        texts=SystemPromptTexts(
            reasoning_text=build_deliberation_protocol(
                depth, reserve_tokens=settings.ai_trade_reasoning_reserve_tokens)))
    return AiTradeDecider(clients.decision, system_prompt, context_refresh_rounds=10_000,
                          settings=settings, reflection_llm=clients.reflection,
                          summary_llm=clients.summary, depth_profile=depth,
                          allow_reasoning_digest=depth.enabled)


async def _run_lane(depth_name: str, payloads: list[dict], inst: str, bar: str,
                    settings: Settings) -> list[dict]:
    """跑一档的全部轮次；每轮一次真实 LLM 调用，记录四个读数。"""
    decider = _build_decider(depth_name, inst, bar, settings)
    rows: list[dict] = []
    for payload in payloads:
        body = dict(payload)
        if decider.depth_name:
            body["reasoningDepth"] = decider.depth_name
        started = time.monotonic()
        decision, degrade = await decider.decide(body)
        elapsed_ms = round((time.monotonic() - started) * 1000)
        meta = decider.last_reasoning
        rows.append({
            "round": payload["round"], "depth": depth_name,
            "chars": len(decider.last_analysis),
            "stages": count_stages(decider.last_analysis, "S"),
            "outputTokens": None if meta is None else meta.output_tokens,
            "inputTokens": None if meta is None else meta.input_tokens,
            "elapsedMs": elapsed_ms, "action": decision.action, "degrade": degrade})
        print(f"  [{depth_name}] round {payload['round']}: "
              f"chars={rows[-1]['chars']} stages={rows[-1]['stages']} "
              f"outTok={rows[-1]['outputTokens']} {elapsed_ms}ms "
              f"action={decision.action} degrade={degrade}")
    return rows


def _median(values: list) -> float | None:
    clean = [float(v) for v in values if v is not None]
    return round(statistics.median(clean), 1) if clean else None


async def _main() -> int:
    ap = argparse.ArgumentParser(description="AI 自动交易思维链深度探针（G-3 权威判据）")
    ap.add_argument("--inst", default="BTC-USDT-SWAP")
    ap.add_argument("--bar", default="30m")
    ap.add_argument("--rounds", type=int, default=6)
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    settings = Settings()
    print(f"provider={settings.agent_provider} model={settings.agent_model} "
          f"inst={args.inst} bar={args.bar} rounds={args.rounds}")
    candles = _fetch_candles(args.inst, args.bar, _WINDOW + args.rounds + 5)
    payloads = _payloads(candles, args.bar, settings, args.rounds)

    off_rows = await _run_lane("off", payloads, args.inst, args.bar, settings)
    deep_rows = await _run_lane("deep", payloads, args.inst, args.bar, settings)

    off_tok = _median([r["outputTokens"] for r in off_rows])
    deep_tok = _median([r["outputTokens"] for r in deep_rows])
    off_chars = _median([r["chars"] for r in off_rows])
    deep_chars = _median([r["chars"] for r in deep_rows])
    # provider 不回 usage 时（半个 provider 家族的老网关）退到字符比：它比 token 比粗，但总好过
    # 「取不到数就不判」——那正是 P0-2 要消除的静默失效。
    basis = "outputTokens" if off_tok and deep_tok else "chars"
    lo, hi = (off_tok, deep_tok) if basis == "outputTokens" else (off_chars, deep_chars)
    ratio = round(hi / lo, 2) if lo else None
    verdict = "PASS" if ratio is not None and ratio >= _RATIO_FLOOR else "FAIL"

    print()
    print(f"baseline(off)  median {basis} = {lo}")
    print(f"deep           median {basis} = {hi}")
    print(f"ratio = {ratio}x   → {verdict} (>= {_RATIO_FLOOR})")
    stage_hits = [r for r in deep_rows if r["stages"] >= 10]
    print(f"stage coverage: {len(stage_hits)}/{len(deep_rows)} rounds reached >= 10/12 stages")

    report = {"inst": args.inst, "bar": args.bar, "rounds": args.rounds,
              "provider": settings.agent_provider, "model": settings.agent_model,
              "basis": basis, "medianOff": lo, "medianDeep": hi, "ratio": ratio,
              "floor": _RATIO_FLOOR, "verdict": verdict,
              "stageCoverage": f"{len(stage_hits)}/{len(deep_rows)}",
              "off": off_rows, "deep": deep_rows}
    if args.out:
        Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=2),
                                  encoding="utf-8")
        print(f"report → {args.out}")
    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
