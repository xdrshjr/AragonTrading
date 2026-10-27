"""AI-trade entry-edge audit: reproducible evidence for spec.md (read-only, offline after first run).

Usage:
    python edge_audit.py <copy-of-aragon.db> [exits|intents|gate|all]

Run from `services/quant` (imports `app.*`). Reuses the round-1 replay helpers and candle cache in
`../../ai-trade-profit-lock-and-entry-unblock/evidence/` (1m / 30m OKX candles, git-ignored).

Sub-commands:
    exits    Exit-side headroom after the round-1 profit lock: ATR hard-stop grid + lock-parameter
             grid with a chronological half split (is the remaining loss an exit problem?).
    intents  Directional skill of every LLM open intent of the latest group (the ones round 1
             unblocked): fixed-horizon directional returns, and a deterministic forward simulation
             (lock -> exchange SL 2xATR / TP 4xATR -> 12h time exit) against the flipped direction.
    gate     Sequential "evidence gate" study on the 131 real round trips: chronological, only
             matured trips count as evidence, bucket x policy grid, both with and without the lock.

Console output is ASCII-only on purpose (the host console is GBK).
"""
from __future__ import annotations

import collections
import json
import sqlite3
import statistics as st
import sys
from dataclasses import replace
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROUND1 = HERE.parents[1] / "ai-trade-profit-lock-and-entry-unblock" / "evidence"
sys.path.insert(0, str(ROUND1))
sys.path.insert(0, str(HERE.parents[3] / "services" / "quant"))

import lock_replay as lr  # noqa: E402
from app.ai_auto.evidence_gate import EvidenceRule, bucket_stats, resolve_state  # noqa: E402
from app.ai_auto.profit_lock import ProfitLockRule  # noqa: E402
from app.ai_auto.profit_lock_bar import LockBar, evaluate_lock_bar  # noqa: E402
from app.config import Settings  # noqa: E402
from app.engine.indicators import atr as atr_fn  # noqa: E402

MAKER, TAKER, HALF_SPREAD = 2.0, 5.0, 1.0
SL_ATR, TP_ATR, MAX_HOLD_S, LIMIT_TTL_S = 2.0, 4.0, 12 * 3600, 300


# ----------------------------------------------------------------------------- shared
def replay_rows(db: str) -> list[dict]:
    """Round-1 replay of the 131 real trips (entries unchanged); adds the 1m path window."""
    trips = lr.load_trips(sqlite3.connect(db), "2026-08-01")
    rule, hs = ProfitLockRule(), Settings().ai_trade_half_spread_fallback_bps
    rows = []
    for trip in trips:
        row = lr.replay(trip, rule, hs)
        bar_s = lr.BAR_S.get(trip.bar, 1800)
        minutes = lr.candles(trip.inst, "1m", trip.entry_ts, trip.exit_ts + bar_s)
        start = lr._fill_minute(minutes, trip.entry_ts, trip.entry_ts + bar_s, trip.entry_px)
        end = lr._fill_minute(minutes, max(trip.exit_ts, start or 0), trip.exit_ts + bar_s,
                              trip.exit_px)
        row["path"] = minutes[(minutes.ts >= (start or trip.entry_ts))
                              & (minutes.ts <= (end or trip.exit_ts))]
        row["start"], row["end"] = start or trip.entry_ts, end or trip.exit_ts
        rows.append(row)
    return rows


def exit_with(row: dict, rule: ProfitLockRule | None, stop_atr: float | None) -> float:
    """Net USDT of one real trip with the lock and/or an ATR hard stop (adverse first)."""
    trip, sign = row["trip"], (1 if row["trip"].side == "long" else -1)
    atr_bps = row["atrPx"] / trip.entry_px * 1e4 if row["atrPx"] else None
    stop_bps = stop_atr * atr_bps if (stop_atr and atr_bps) else None
    state = None
    for bar in row["path"].itertuples(index=False):
        if int(bar.ts) >= int(row["end"]):
            break
        if rule is not None:
            res = evaluate_lock_bar(state, rule, LockBar(
                seq=1, side=trip.side, entry_px=trip.entry_px, qty_coin=1.0, ts=int(bar.ts),
                opened_ts=int(row["start"]), open_px=bar.o, high_px=bar.h, low_px=bar.l,
                close_px=bar.c, cost_bps=row["costBps"], atr_bps=atr_bps))
            state = res.state
            if res.verdict is not None:
                return _net(trip, float(res.fill_px), sign)
        if stop_bps is not None and (state is None or state.lock.stage == 0):
            stop_px = trip.entry_px * (1 - sign * stop_bps / 1e4)
            adverse = bar.l if sign > 0 else bar.h
            if (adverse - stop_px) * sign <= 0:
                gap = (bar.o - stop_px) * sign <= 0
                return _net(trip, bar.o if gap else stop_px, sign)
    return trip.net


def _net(trip, px: float, sign: int) -> float:
    return (px - trip.entry_px) / trip.entry_px * 1e4 * sign * trip.usd_per_bps - trip.fee


def _line(label: str, nets: list[float]) -> str:
    wins = [n for n in nets if n > 0]
    losses = [n for n in nets if n <= 0]
    return (f"  {label:34s} n={len(nets):3d} net={sum(nets):7.2f} win={len(wins) / len(nets):.2f} "
            f"avgWin={st.mean(wins) if wins else 0:6.3f} avgLoss={st.mean(losses) if losses else 0:6.3f}")


# ----------------------------------------------------------------------------- exits
def cmd_exits(db: str) -> None:
    rows = replay_rows(db)
    base = ProfitLockRule()
    print("[exits] 131 real trips; is the residual loss after the lock an exit problem?")
    print(_line("real (no lock)", [r["trip"].net for r in rows]))
    print(_line("lock (round-1 default)", [exit_with(r, base, None) for r in rows]))
    for k in (0.5, 0.75, 1.0, 1.25, 1.5, 2.0):
        print(_line(f"lock + hard stop {k}xATR", [exit_with(r, base, k) for r in rows]))
    unlocked = [r for r in rows if r["lockStage"] is None and r["onNet"] <= 0]
    q = lambda xs: [round(sorted(xs)[int(len(xs) * p)], 1) for p in (.25, .5, .75)]  # noqa: E731
    print(f"  unlocked losers n={len(unlocked)} sum={sum(r['onNet'] for r in unlocked):.2f} "
          f"mfeBps q={q([r['mfeBps'] for r in unlocked])} "
          f"sides={dict(collections.Counter(r['trip'].side for r in unlocked))}")
    split = sorted(r["trip"].entry_ts for r in rows)[len(rows) // 2]
    first = [r for r in rows if r["trip"].entry_ts < split]
    second = [r for r in rows if r["trip"].entry_ts >= split]
    print("  lock-parameter grid (first half / second half / all):")
    for kw in ({}, {"keep_ratio": 0.65}, {"keep_ratio": 0.35}, {"arm_atr_mult": 0.5},
               {"arm_atr_mult": 0.25}, {"trail_atr_mult": 0.6}, {"trail_atr_mult": 1.0},
               {"buffer_bps": 5.0}, {"arm_cost_mult": 2.5}):
        rule = replace(base, **kw)
        a = sum(exit_with(r, rule, None) for r in first)
        b = sum(exit_with(r, rule, None) for r in second)
        print(f"    {json.dumps(kw):32s} {a:7.2f} {b:7.2f} {a + b:7.2f}")


# ----------------------------------------------------------------------------- intents
def _intent_frames(conn):
    spans = {r[0]: (int(r[1]) // 86400 * 86400 - 86400, (int(r[2]) // 86400 + 2) * 86400)
             for r in conn.execute(
                 "select inst_id, min(wall_ts), max(wall_ts) from ai_trade_decision_logs "
                 "where action like 'open%' group by inst_id")}
    m1 = {i: lr.candles(i, "1m", lo, hi) for i, (lo, hi) in spans.items()}
    m30 = {}
    for i, (lo, hi) in spans.items():
        frame = lr.candles(i, "30m", lo - 3 * 86400, hi)
        m30[i] = frame.assign(atr=atr_fn(frame, 14).values)
    return m1, m30


def _forward(m1, m30, inst, side, wall, ord_type, limit_px):
    """Deterministic reference exit: lock -> SL 2xATR / TP 4xATR -> 12h time exit (net bps)."""
    sign = 1 if side == "long" else -1
    minutes = m1[inst]
    after = minutes[minutes.ts >= (int(wall) // 60 + 1) * 60]
    if after.empty:
        return None
    if ord_type == "limit" and limit_px:
        window = after[after.ts < after.ts.iloc[0] + LIMIT_TTL_S]
        hit = window[window.l <= limit_px] if sign > 0 else window[window.h >= limit_px]
        if hit.empty:
            return None
        t0 = int(hit.ts.iloc[0])
        opn = float(hit.o.iloc[0])
        entry = min(limit_px, opn) if sign > 0 else max(limit_px, opn)
        fee_in = MAKER
    else:
        t0, entry, fee_in = int(after.ts.iloc[0]), float(after.o.iloc[0]), TAKER + HALF_SPREAD
    prev = m30[inst][m30[inst].ts + 1800 <= t0]
    atr_bps = float(prev.atr.iloc[-1]) / entry * 1e4
    cost = fee_in + TAKER + HALF_SPREAD
    sl = entry * (1 - sign * SL_ATR * atr_bps / 1e4)
    tp = entry * (1 + sign * TP_ATR * atr_bps / 1e4)
    state, exit_px, kind, t_exit = None, None, "time", None
    for bar in minutes[(minutes.ts >= t0) & (minutes.ts < t0 + MAX_HOLD_S)].itertuples(index=False):
        res = evaluate_lock_bar(state, ProfitLockRule(), LockBar(
            seq=1, side=side, entry_px=entry, qty_coin=1.0, ts=int(bar.ts), opened_ts=t0,
            open_px=bar.o, high_px=bar.h, low_px=bar.l, close_px=bar.c, cost_bps=cost,
            atr_bps=atr_bps))
        state = res.state
        if res.verdict is not None:
            exit_px, kind, t_exit = float(res.fill_px), "lock", int(bar.ts)
            break
        adverse, favorable = (bar.l, bar.h) if sign > 0 else (bar.h, bar.l)
        if (adverse - sl) * sign <= 0:
            exit_px, kind, t_exit = (bar.o if (bar.o - sl) * sign <= 0 else sl), "sl", int(bar.ts)
            break
        if (favorable - tp) * sign >= 0:
            exit_px, kind, t_exit = tp, "tp", int(bar.ts)
            break
    if exit_px is None:
        tail = minutes[(minutes.ts >= t0) & (minutes.ts < t0 + MAX_HOLD_S)]
        exit_px, t_exit = float(tail.c.iloc[-1]), int(tail.ts.iloc[-1])
    gross = (exit_px - entry) / entry * 1e4 * sign
    fee_out = MAKER if kind == "tp" else TAKER + HALF_SPREAD
    return {"net": gross - fee_in - fee_out, "gross": gross, "kind": kind, "t1": t_exit,
            "t0": t0, "entry": entry, "atrPx": float(prev.atr.iloc[-1]), "cost": cost}


def forward_sims(intents, m1, m30) -> list[tuple]:
    """The 35 one-position-per-run forward simulations (model side, flipped side)."""
    busy, sims = {}, []
    for run, inst, wall, action, final, ord_type, limit_px in [
            r for r in intents if r[4].startswith("open")]:
        if busy.get(run, 0) > wall:
            continue
        side = "long" if action == "open_long" else "short"
        real = _forward(m1, m30, inst, side, wall, ord_type, limit_px)
        if real is None:
            continue
        flip = _forward(m1, m30, inst, "short" if side == "long" else "long", wall, "market", None)
        busy[run] = real["t1"]
        sims.append((side, real, flip, inst))
    return sims


def load_intents(conn):
    return conn.execute(
        "select run_id, inst_id, wall_ts, action, final_action, ord_type, limit_px "
        "from ai_trade_decision_logs where action like 'open%' order by wall_ts").fetchall()


def cmd_intents(db: str) -> None:
    conn = sqlite3.connect(db)
    m1, m30 = _intent_frames(conn)
    intents = load_intents(conn)
    print(f"[intents] {len(intents)} LLM open intents (stage-1 action, before any gate)")
    horizons = (15, 30, 60, 120, 240, 480)
    per = {h: [] for h in horizons}
    for _, inst, wall, action, *_ in intents:
        minutes, sign = m1[inst], (1 if action == "open_long" else -1)
        after = minutes[minutes.ts >= (int(wall) // 60 + 1) * 60]
        if after.empty:
            continue
        t0, entry = int(after.ts.iloc[0]), float(after.o.iloc[0])
        for h in horizons:
            window = minutes[(minutes.ts >= t0) & (minutes.ts < t0 + h * 60)]
            if len(window) >= h * 0.9:
                per[h].append((float(window.c.iloc[-1]) - entry) / entry * 1e4 * sign)
    for h in horizons:
        xs = per[h]
        print(f"  horizon {h:4d}m n={len(xs)} meanDirRet={st.mean(xs):6.1f}bps "
              f"median={st.median(xs):6.1f} hit={sum(1 for v in xs if v > 0) / len(xs):.2f} "
              "(overlapping samples; not independent)")
    sims = forward_sims(intents, m1, m30)
    nets = [s[1]["net"] for s in sims]
    print(f"  forward sim (one position per run): n={len(sims)} meanNet={st.mean(nets):.1f}bps "
          f"win={sum(1 for n in nets if n > 0) / len(nets):.2f} "
          f"flippedDirectionMeanNet={st.mean(s[2]['net'] for s in sims if s[2]):.1f}bps "
          f"exits={dict(collections.Counter(s[1]['kind'] for s in sims))}")


# ----------------------------------------------------------------------------- gate
def _sequential(rows, key, bucket, n_min, z, unproven_live):
    """Chronological: only trips that exited before this entry count as evidence.

    The three-state verdict is delegated to the shipped pure core (`app.ai_auto.evidence_gate`),
    so this replay measures the very code that gates real capital (spec v2, P0-1 / RC-1).
    """
    rule = EvidenceRule(min_samples=n_min, window=30, z=z)
    history, pending, live = {}, [], 0.0
    counts = collections.Counter()
    for row in rows:
        trip = row["trip"]
        for item in [p for p in pending if p[0] <= trip.entry_ts]:
            history.setdefault(item[1], []).append(item[2])
            pending.remove(item)
        sample = history.get(bucket(trip), [])[-rule.window:]
        state = resolve_state(bucket_stats(sample, rule), None, rule).state
        counts[state] += 1
        if state == "proven" or (state == "unproven" and unproven_live):
            live += row[key]
        pending.append((trip.exit_ts, bucket(trip), row[key] / trip.usd_per_bps))
    return live, counts


def cmd_gate(db: str) -> None:
    rows = sorted(replay_rows(db), key=lambda r: r["trip"].entry_ts)
    buckets = {"global": lambda t: "all", "side": lambda t: t.side,
               "inst*side": lambda t: (t.inst, t.side)}
    print("[gate] live net USDT if capital only goes where matured evidence allows")
    for key, label in (("offNet", "no lock"), ("onNet", "with lock")):
        print(f"  {label}: ungated net={sum(r[key] for r in rows):.2f}")
        for name, fn in buckets.items():
            for n_min, z in ((10, 1.0), (10, 0.5), (20, 1.0)):
                for unproven_live in (False, True):
                    live, counts = _sequential(rows, key, fn, n_min, z, unproven_live)
                    mode = "shadow-until-proven" if not unproven_live else "block-if-disproven"
                    print(f"    {name:9s} n_min={n_min:2d} z={z} {mode:19s} live={live:7.2f} "
                          f"{dict(counts)}")


def main() -> None:
    db, what = sys.argv[1], (sys.argv[2] if len(sys.argv) > 2 else "all")
    for name, fn in (("exits", cmd_exits), ("intents", cmd_intents), ("gate", cmd_gate)):
        if what in (name, "all"):
            fn(db)


if __name__ == "__main__":
    main()
