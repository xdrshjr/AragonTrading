"""Reference-policy parity check (spec v2 §7.6-3 / RC-4). Read-only, offline after first run.

Usage (from `services/quant`):
    python ../../docs/plans/ai-trade-evidence-gated-capital/evidence/ref_policy_parity.py <aragon.db copy>

Re-computes the 35 forward-simulated round trips of `edge_audit.py intents` with the shipped pure
core `app.ai_auto.shadow_ledger.advance_shadow` on execution-bar (30m) candles, and compares with the
1m reference simulation. Entry price, ATR and round-trip cost are taken from the 1m simulation so
the only differences are bar granularity and the ledger's admission rules (the intent bar is never
evaluated; a missing bar voids the sample).

Pass criteria: sign agreement >= 80% and |mean difference| <= 5 bps. 30m and 1m bars do not have to
agree trade by trade.

Console output is ASCII-only on purpose (the host console is GBK).
"""
from __future__ import annotations

import sqlite3
import statistics as st
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import edge_audit as ea  # noqa: E402  (also puts round-1 helpers and app.* on sys.path)
from app.ai_auto.shadow_ledger import (  # noqa: E402
    RefPolicy,
    advance_shadow,
    bar_seconds,
    open_shadow,
)
from app.config import Settings  # noqa: E402
from app.ai_auto.shadow_ledger import ref_policy_from_settings  # noqa: E402

SIGN_AGREEMENT_MIN = 0.80
MEAN_DIFF_MAX_BPS = 5.0


def shadow_net(policy: RefPolicy, inst: str, side: str, real: dict, m30) -> tuple[float | None, str]:
    """Push one simulated entry through the ledger on 30m bars; returns (net_bps, exit_kind)."""
    shadow = open_shadow(inst_id=inst, bar="30m", side=side, now_wall=real["t0"],
                         px=real["entry"], atr_px=real["atrPx"], cost_bps=real["cost"],
                         policy=policy, run_id=0, round_no=0, fallback_cost_bps=real["cost"])
    bar_s = bar_seconds("30m")
    frame = m30[inst]
    rows = frame[(frame.ts > shadow.opened_bar_ts)
                 & (frame.ts <= shadow.opened_bar_ts + (policy.max_hold_bars + 1) * bar_s)]
    for bar in rows.itertuples(index=False):
        shadow = advance_shadow(shadow, {"ts": int(bar.ts), "o": float(bar.o), "h": float(bar.h),
                                         "l": float(bar.l), "c": float(bar.c)}, policy)
        if shadow.status != "open":
            break
    return shadow.net_bps, (shadow.exit_kind or shadow.status)


def main() -> None:
    conn = sqlite3.connect(sys.argv[1])
    m1, m30 = ea._intent_frames(conn)
    sims = ea.forward_sims(ea.load_intents(conn), m1, m30)
    policy = ref_policy_from_settings(Settings())
    pairs, kinds = [], []
    for side, real, _flip, inst in sims:
        net, kind = shadow_net(policy, inst, side, real, m30)
        kinds.append(kind)
        if net is not None:
            pairs.append((real["net"], net))
    agree = sum(1 for a, b in pairs if (a > 0) == (b > 0)) / len(pairs)
    mean_1m = st.mean(a for a, _ in pairs)
    mean_30m = st.mean(b for _, b in pairs)
    diff = mean_30m - mean_1m
    print(f"[parity] policy={policy.version()}")
    print(f"  sims={len(sims)} compared={len(pairs)} voided={len(sims) - len(pairs)} "
          f"ledgerExits={dict((k, kinds.count(k)) for k in sorted(set(kinds)))}")
    print(f"  mean1m={mean_1m:.2f}bps mean30m={mean_30m:.2f}bps diff={diff:+.2f}bps "
          f"signAgreement={agree:.2f}")
    ok = agree >= SIGN_AGREEMENT_MIN and abs(diff) <= MEAN_DIFF_MAX_BPS
    print(f"  verdict={'PASS' if ok else 'FAIL'} (need agreement>={SIGN_AGREEMENT_MIN:.2f}, "
          f"|diff|<={MEAN_DIFF_MAX_BPS:.1f}bps)")


if __name__ == "__main__":
    main()
