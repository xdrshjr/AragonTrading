"""AI-trade loss audit: reproducible evidence for spec.md (read-only).

Usage:
    python loss_audit.py <copy-of-aragon.db> [--since 2026-08-01] [--group <id-suffix>]

Copy the production DB first (WAL): %APPDATA%/aragon-desktop/aragon.db(-wal/-shm).
Output is ASCII-only on purpose (the host console is GBK).
"""
from __future__ import annotations

import argparse
import collections
import json
import sqlite3
import statistics as st


def load_trips(conn, since):
    """Pair open/close legs per run in causal (id) order; returns trip dicts."""
    runs = [r[0] for r in conn.execute(
        "select id from strategy_runs where mode='ai-trade' and created_at>=? "
        "order by id", (since,))]
    trips = []
    for run_id in runs:
        legs = conn.execute(
            "select id,ts,price,qty,fee,pnl,reason,explain_json,position_after,"
            "peak_pnl from trades where run_id=? order by id", (run_id,)).fetchall()
        opened = None
        for leg in legs:
            flat = leg[8] in (0, None)
            if opened is None and not flat and not leg[5]:
                opened = leg
                continue
            if opened is not None and flat:
                trips.append(_trip(run_id, opened, leg))
                opened = None
    return trips


def _trip(run_id, opened, closed):
    side = 1 if opened[8] > 0 else -1
    move_bps = (closed[2] - opened[2]) / opened[2] * 1e4 * side
    pnl = closed[5] or 0.0
    fee = (closed[4] or 0.0) + (opened[4] or 0.0)
    reason = closed[6] or ""
    usd_per_bps = (pnl + fee) / move_bps if abs(move_bps) >= 1 else None
    peak_bps = None
    if usd_per_bps and usd_per_bps > 0 and closed[9] is not None:
        peak_bps = closed[9] / usd_per_bps
    return {"run": run_id, "side": "L" if side > 0 else "S", "pnl": pnl,
            "fee": fee, "moveBps": move_bps, "peakUsd": closed[9],
            "peakBps": peak_bps, "holdS": closed[1] - opened[1],
            "reason": reason if reason.isascii() else "agent_llm_text"}


def report_trips(trips):
    wins = [t for t in trips if t["pnl"] > 0]
    print("trips", len(trips), "net", round(sum(t["pnl"] for t in trips), 2),
          "fees", round(sum(t["fee"] for t in trips), 2),
          "winrate", round(len(wins) / max(1, len(trips)), 3))
    print("long/short", collections.Counter(t["side"] for t in trips))
    groups = collections.defaultdict(list)
    for t in trips:
        groups[t["reason"]].append(t)
    for reason, rows in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        green_red = [t for t in rows if t["pnl"] <= 0 and (t["peakUsd"] or 0) > 0]
        peaks = [t["peakBps"] for t in rows if t["peakBps"] is not None]
        print(f"  {reason:32s} n={len(rows):3d} "
              f"win={sum(1 for t in rows if t['pnl'] > 0) / len(rows):.2f} "
              f"pnl={sum(t['pnl'] for t in rows):8.2f} "
              f"moveBpsMed={st.median(t['moveBps'] for t in rows):7.1f} "
              f"peakBpsMed={(st.median(peaks) if peaks else float('nan')):6.1f} "
              f"greenToRed={len(green_red)} "
              f"giveback={sum(t['peakUsd'] - t['pnl'] for t in green_red):.2f}")
    losers = sorted(t["peakBps"] for t in trips if t["pnl"] <= 0
                    and t["peakBps"] and t["peakBps"] > 0)
    if losers:
        q = [round(losers[int(len(losers) * p)], 1) for p in (.25, .5, .75)]
        print("green-to-red losers peakBps quartiles", q)


def report_funnel(conn, group):
    rows = conn.execute(
        "select final_action,fill_ref_json,telemetry_json from "
        "ai_trade_decision_logs where group_id like ? and final_action!='hold'",
        (f"%{group}",)).fetchall()
    reasons = collections.Counter()
    for action, fill, _ in rows:
        ref = json.loads(fill or "{}")
        reasons[(action[:5], ref.get("reason"), ref.get("traded"))] += 1
    print("non-hold decisions by execution outcome:")
    for key, n in reasons.most_common():
        print("  ", n, key)
    lat = [json.loads(t or "{}").get("latencyMs") for *_, t in conn.execute(
        "select telemetry_json from ai_trade_decision_logs where group_id like ?",
        (f"%{group}",))]
    lat = sorted(x / 1000 for x in lat if x)
    if lat:
        print("llm latency s: median", round(st.median(lat), 1),
              "p90", round(lat[int(len(lat) * .9)], 1), "n", len(lat))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("db")
    parser.add_argument("--since", default="2026-08-01")
    parser.add_argument("--group", default="8b8c35fddac547188a48a6822c3c36b5")
    args = parser.parse_args()
    conn = sqlite3.connect(args.db)
    report_trips(load_trips(conn, args.since))
    report_funnel(conn, args.group)


if __name__ == "__main__":
    main()
