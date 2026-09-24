"""Deterministic counterfactual replay of the profit lock (spec §7.5-3, RC-1).

Usage:
    python lock_replay.py <copy-of-aragon.db> [--since 2026-08-01] [--out lock-replay.md]

Entries are the REAL fills (time, side, price, size, fees). Only the exit changes: each trip is
replayed on OKX 1m candles with the SAME pure core the product runs
(`app.ai_auto.profit_lock_bar.evaluate_lock_bar`, default rule, confirm_s = 0). If the lock line is
crossed before the real exit, the trip is re-priced at the lock fill; fees stay the real ones.

Candles are fetched once from the public OKX endpoint and cached under ./candles/ (git-ignored).
Console output is ASCII-only on purpose (the host console is GBK); the report is UTF-8 markdown.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[3] / "services" / "quant"))

from app.ai_auto.profit_lock import ProfitLockRule  # noqa: E402
from app.ai_auto.profit_lock_bar import LockBar, evaluate_lock_bar  # noqa: E402
from app.config import Settings  # noqa: E402
from app.engine.indicators import atr  # noqa: E402

CACHE = HERE / "candles"
OKX = "https://www.okx.com/api/v5/market/history-candles"
BAR_S = {"1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800, "1H": 3600,
         "2H": 7200, "4H": 14400, "6H": 21600, "12H": 43200, "1D": 86400}


@dataclass
class Trip:
    run: int
    inst: str
    bar: str
    side: str
    entry_ts: int
    exit_ts: int
    entry_px: float
    exit_px: float
    fee: float
    gross: float
    net: float
    usd_per_bps: float
    reason: str


def _basis(config_json: str | None) -> str:
    try:
        return json.loads(config_json or "{}").get("costBasis") or "gross"
    except ValueError:
        return "gross"


def load_trips(conn, since: str) -> list[Trip]:
    """Pair open/close legs per run in causal (id) order (same pairing as loss_audit.py)."""
    runs = conn.execute(
        "select id, inst_id, bar, config_json from strategy_runs where mode='ai-trade' "
        "and created_at>=? order by id", (since,)).fetchall()
    trips = []
    for run_id, inst, bar, cfg in runs:
        legs = conn.execute(
            "select ts, price, fee, pnl, reason, position_after from trades "
            "where run_id=? order by id", (run_id,)).fetchall()
        opened = None
        for leg in legs:
            flat = leg[5] in (0, None)
            if opened is None and not flat and not leg[3]:
                opened = leg
            elif opened is not None and flat:
                trips.append(_trip(run_id, inst, bar, _basis(cfg), opened, leg))
                opened = None
    return trips


def _trip(run_id, inst, bar, basis, opened, closed) -> Trip:
    sign = 1 if opened[5] > 0 else -1
    fee = (opened[2] or 0.0) + (closed[2] or 0.0)
    pnl = closed[3] or 0.0
    gross = pnl + fee if basis == "net" else pnl
    move = (closed[1] - opened[1]) / opened[1] * 1e4 * sign
    # USD per bps of price move: from the realised gross when the move is measurable,
    # otherwise from the open fee at 5 bps taker (the configured default).
    usd_per_bps = gross / move if abs(move) >= 1 and gross / move > 0 else (opened[2] or 0) / 5
    return Trip(run_id, inst, bar, "long" if sign > 0 else "short", int(opened[0]),
                int(closed[0]), float(opened[1]), float(closed[1]), fee, gross, gross - fee,
                usd_per_bps, closed[4] or "")


def _fetch(inst: str, bar: str, end_ms: int, limit: int = 100) -> list[list]:
    url = f"{OKX}?instId={inst}&bar={bar}&after={end_ms}&limit={limit}"
    for attempt in range(5):
        try:
            # OKX 的 CDN 对没有 User-Agent 的请求一律 403。
            req = urllib.request.Request(url, headers={"User-Agent": "aragon-lock-replay/1.0"})
            with urllib.request.urlopen(req, timeout=20) as resp:
                body = json.loads(resp.read())
            if body.get("code") == "0":
                return body["data"]
        except OSError:
            pass
        time.sleep(1.0 + attempt)
    raise RuntimeError(f"fetch failed: {inst} {bar} {end_ms}")


def candles(inst: str, bar: str, start_s: int, end_s: int) -> pd.DataFrame:
    """[start_s, end_s] candles, oldest first; cached per (inst, bar, start, end)."""
    CACHE.mkdir(exist_ok=True)
    path = CACHE / f"{inst}_{bar}_{start_s}_{end_s}.json"
    if path.exists():
        rows = json.loads(path.read_text(encoding="utf-8"))
    else:
        rows, cursor = [], (end_s + BAR_S[bar]) * 1000
        while True:
            page = _fetch(inst, bar, cursor)
            if not page:
                break
            rows.extend(page)
            cursor = int(page[-1][0])
            if cursor <= start_s * 1000:
                break
            time.sleep(0.12)
        path.write_text(json.dumps(rows), encoding="utf-8")
    frame = pd.DataFrame(
        [(int(r[0]) // 1000, float(r[1]), float(r[2]), float(r[3]), float(r[4])) for r in rows],
        columns=["ts", "o", "h", "l", "c"]).drop_duplicates("ts").sort_values("ts")
    return frame[(frame.ts >= start_s) & (frame.ts <= end_s)].reset_index(drop=True)


def _fill_minute(minutes: pd.DataFrame, lo: int, hi: int, px: float) -> int | None:
    """First 1m candle inside [lo, hi) whose range contains px (trade ts is the bar ts)."""
    window = minutes[(minutes.ts >= lo) & (minutes.ts < hi)]
    hit = window[(window.l <= px) & (window.h >= px)]
    if not hit.empty:
        return int(hit.ts.iloc[0])
    return int(window.ts.iloc[0]) if not window.empty else None


def replay(trip: Trip, rule: ProfitLockRule, half_spread_bps: float) -> dict:
    bar_s = BAR_S.get(trip.bar, 1800)
    minutes = candles(trip.inst, "1m", trip.entry_ts, trip.exit_ts + bar_s)
    start = _fill_minute(minutes, trip.entry_ts, trip.entry_ts + bar_s, trip.entry_px)
    end = _fill_minute(minutes, max(trip.exit_ts, start or 0), trip.exit_ts + bar_s,
                       trip.exit_px)
    ctx = candles(trip.inst, trip.bar, trip.entry_ts - 20 * bar_s, trip.entry_ts - bar_s)
    atr_px = float(atr(ctx, 14).iloc[-1]) if len(ctx) >= 15 else None
    notional = trip.usd_per_bps * 1e4
    cost_bps = trip.fee / notional * 1e4 + 2 * half_spread_bps if notional > 0 else None
    sign = 1 if trip.side == "long" else -1
    path = minutes[(minutes.ts >= (start or trip.entry_ts)) & (minutes.ts <= (end or trip.exit_ts))]
    mfe = 0.0
    state, lock_exit = None, None
    for row in path.itertuples(index=False):
        favorable = row.h if trip.side == "long" else row.l
        mfe = max(mfe, (favorable - trip.entry_px) / trip.entry_px * 1e4 * sign)
        if lock_exit is not None:
            continue
        result = evaluate_lock_bar(state, rule, LockBar(
            seq=1, side=trip.side, entry_px=trip.entry_px, qty_coin=notional / trip.entry_px,
            ts=int(row.ts), opened_ts=int(start or trip.entry_ts), open_px=row.o,
            high_px=row.h, low_px=row.l, close_px=row.c, cost_bps=cost_bps,
            atr_bps=None if atr_px is None else atr_px / trip.entry_px * 1e4))
        state = result.state
        if result.verdict is not None and int(row.ts) < int(end or trip.exit_ts):
            lock_exit = (float(result.fill_px), result.verdict.note["lockStage"],
                         mfe)
    out = {"trip": trip, "mfeBps": mfe, "costBps": cost_bps, "atrPx": atr_px,
           "offNet": trip.net, "offPeakNet": mfe * trip.usd_per_bps - trip.fee,
           "onNet": trip.net, "lockStage": None, "onPeakNet": None}
    if lock_exit is not None:
        px, stage, mfe_at_exit = lock_exit
        gross = (px - trip.entry_px) / trip.entry_px * 1e4 * sign * trip.usd_per_bps
        out.update(onNet=gross - trip.fee, lockStage=stage,
                   onPeakNet=mfe_at_exit * trip.usd_per_bps - trip.fee)
    else:
        out["onPeakNet"] = out["offPeakNet"]
    return out


def _arm(rows: list[dict], key: str, peak_key: str) -> dict:
    nets = [r[key] for r in rows]
    g2r = [r for r in rows if r[peak_key] > 0 and r[key] <= 0]
    return {"net": sum(nets), "win": sum(1 for n in nets if n > 0) / max(1, len(nets)),
            "g2r": len(g2r), "giveback": sum(r[peak_key] - r[key] for r in g2r)}


def report(rows: list[dict], skipped: list[str], out: Path) -> dict:
    off = _arm(rows, "offNet", "offPeakNet")
    on = _arm(rows, "onNet", "onPeakNet")
    locked = [r for r in rows if r["lockStage"] is not None]
    swept = [r for r in locked if r["offNet"] > 0 and r["onNet"] < r["offNet"]]
    saved = [r for r in locked if r["onNet"] > r["offNet"]]
    by_stage = {s: sum(1 for r in locked if r["lockStage"] == s) for s in (1, 2)}
    g2r_cut = 1 - on["g2r"] / off["g2r"] if off["g2r"] else 0.0
    passed = g2r_cut >= 0.5 and on["net"] >= off["net"]
    lines = [
        "# 收益锁确定性反事实回放（spec §7.5-3 / RC-1）", "",
        "> **已知局限**：锁平之后，真实世界里的后续决策可能不同（路径依赖），本回放把各笔往返视为"
        "独立；1m K 线极值近似偏保守（先判不利极值、再推有利极值）；成交分钟按「该根 30m 内首根"
        "价格区间覆盖成交价的 1m K 线」近似（`trades.ts` 是 K 线时间戳，不是成交墙钟）。", "",
        f"- 样本：{len(rows)} 笔往返（入场全部沿用真实成交；跳过 {len(skipped)} 笔：K 线缺失）",
        "- 参数：spec §5.3 默认值（跑之前冻结），`confirm_s=0`，成本 = 真实手续费 bps + 2×半点差",
        "- 「盈转亏」口径：按 1m 路径，持仓期间峰值浮盈曾覆盖该笔全部手续费（净额曾为正），"
        "最终净额 ≤ 0。两臂同一口径。", "",
        "| 指标 | 关臂（真实） | 开臂（收益锁） |", "|---|---|---|",
        f"| 净收益 USDT | {off['net']:.2f} | {on['net']:.2f} |",
        f"| 胜率 | {off['win']:.1%} | {on['win']:.1%} |",
        f"| 盈转亏笔数 | {off['g2r']} | {on['g2r']} |",
        f"| 盈转亏回吐 USDT | {off['giveback']:.2f} | {on['giveback']:.2f} |",
        "",
        f"- 收益锁离场：{len(locked)} 笔（保本锁 {by_stage[1]} / 利润锁 {by_stage[2]}）",
        f"- 被锁改善的往返：{len(saved)} 笔，合计 +{sum(r['onNet'] - r['offNet'] for r in saved):.2f} USDT",
        f"- **被锁线提前扫出的赢家**：{len(swept)} 笔，合计少赚 "
        f"{sum(r['offNet'] - r['onNet'] for r in swept):.2f} USDT（R-1 的代价，单列不相抵）",
        "", "## 门槛", "",
        f"- 盈转亏笔数下降 {g2r_cut:.0%}（门槛 ≥ 50%）：{'通过' if g2r_cut >= 0.5 else '未通过'}",
        f"- 开臂净收益 {on['net']:.2f} ≥ 关臂 {off['net']:.2f}：{'通过' if on['net'] >= off['net'] else '未通过'}",
        f"- **结论：{'达标' if passed else '未达标，停在参数评审'}**", "",
        "## 逐笔（仅收益锁改变了结果的往返）", "",
        "| run | 标的 | 方向 | 真实净额 | 锁后净额 | 阶段 | MFE bps | 成本 bps |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in sorted(locked, key=lambda r: r["onNet"] - r["offNet"]):
        t = r["trip"]
        lines.append(f"| {t.run} | {t.inst} | {t.side} | {r['offNet']:.3f} | {r['onNet']:.3f} | "
                     f"{r['lockStage']} | {r['mfeBps']:.1f} | {r['costBps']:.1f} |")
    if skipped:
        lines += ["", "跳过：" + "、".join(skipped)]
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"off": off, "on": on, "locked": len(locked), "swept": len(swept),
            "g2rCut": g2r_cut, "passed": passed}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("db")
    parser.add_argument("--since", default="2026-08-01")
    parser.add_argument("--out", default=str(HERE / "lock-replay.md"))
    args = parser.parse_args()
    settings = Settings()
    rule = ProfitLockRule()
    trips = load_trips(sqlite3.connect(args.db), args.since)

    def run_one(trip: Trip):
        try:
            return replay(trip, rule, settings.ai_trade_half_spread_fallback_bps)
        except (RuntimeError, IndexError, KeyError) as exc:
            return f"run {trip.run} ({exc.__class__.__name__})"
        finally:
            print(f"done run {trip.run} {trip.inst}", flush=True)

    # 公共接口单请求在本机要 6-11 秒；按往返并发取数（结果顺序与样本顺序一致，缓存后离线复算）。
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(run_one, trips))
    rows = [r for r in results if isinstance(r, dict)]
    skipped = [r for r in results if isinstance(r, str)]
    summary = report(rows, skipped, Path(args.out))
    print(json.dumps({k: v for k, v in summary.items()}, default=str))


if __name__ == "__main__":
    main()
