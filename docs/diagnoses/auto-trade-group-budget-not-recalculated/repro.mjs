// Minimal numeric repro for: 多标的自动交易确认弹窗修改总预算后，各标的预算预览不实时更新。
//
// Replicates the EXACT pure-function logic used by AutoTradeGroupConfirmModal.tsx:
//   - deriveGroupDefaultBudget  (apps/web/lib/autoTrade.ts:84-87)
//   - clampGroupBudgetToCap     (apps/web/lib/autoTrade.ts:90-93)
//   - memberBudget              (apps/web/lib/autoTrade.ts:79-81)
//   - previewTotal memo         (AutoTradeGroupConfirmModal.tsx:99-103)
//
// Run: node docs/diagnoses/auto-trade-group-budget-not-recalculated/repro.mjs

const LIVE_AUTO_DEFAULT_CAPITAL_FRAC = 0.01; // autoTrade.ts:7
const LIVE_AUTO_MAX_CAPITAL_FRAC = 0.05; // autoTrade.ts:8

const deriveGroupDefaultBudget = (avail, n) =>
  Math.min(Math.max(0, avail) * LIVE_AUTO_DEFAULT_CAPITAL_FRAC * n, Math.max(0, avail) * LIVE_AUTO_MAX_CAPITAL_FRAC);

const clampGroupBudgetToCap = (budget, avail) => {
  const cap = Math.max(0, avail) * LIVE_AUTO_MAX_CAPITAL_FRAC;
  return cap > 0 ? Math.min(budget, cap) : budget;
};

const memberBudget = (total, pct) => (total * pct) / 100;

// previewTotal memo: capital.value ?? default, then clamp (modal lines 99-103)
const previewTotal = (avail, capitalValue, n) => {
  if (avail == null) return null;
  const raw = capitalValue ?? deriveGroupDefaultBudget(avail, n);
  return clampGroupBudgetToCap(raw, avail);
};

function scenario(title, { avail, members, typedSequence }) {
  console.log(`\n=== ${title} ===`);
  console.log(`available USDT = ${avail}, members = ${members.map((m) => `${m.id}:${m.pct}%`).join(", ")}`);
  const cap = avail * LIVE_AUTO_MAX_CAPITAL_FRAC;
  const def = deriveGroupDefaultBudget(avail, members.length);
  console.log(`5% hard cap = ${cap} USDT ; default total budget = ${def} USDT (default == cap? ${def === cap})`);

  for (const typed of typedSequence) {
    const total = previewTotal(avail, typed, members.length);
    const perSymbol = members.map((m) => `${m.id}=${memberBudget(total, m.pct).toFixed(2)}`).join("  ");
    const label = typed == null ? "默认总预算（随成员数缩放）" : "生效总预算（预览）";
    console.log(
      `  user types ${typed == null ? "<empty>" : typed.toString().padStart(6)} -> previewTotal = ${String(total).padStart(6)} USDT  [${label}]  per-symbol: ${perSymbol}`,
    );
  }
}

// Scenario A: >=5 members -> default already AT the cap; ANY entered budget >= cap is frozen at the default.
scenario("A. 5 members, avail=1000 (default == cap == 50)", {
  avail: 1000,
  members: [
    { id: "BTC", pct: 20 },
    { id: "ETH", pct: 20 },
    { id: "SOL", pct: 20 },
    { id: "DOGE", pct: 20 },
    { id: "PEPE", pct: 20 },
  ],
  typedSequence: [null, 100, 300, 500, 2000],
});

// Scenario B: small balance -> cap tiny; every realistic "increase" is clamped, looks frozen.
scenario("B. 3 members, avail=1000 (cap=50, default=30)", {
  avail: 1000,
  members: [
    { id: "BTC", pct: 50 },
    { id: "ETH", pct: 30 },
    { id: "SOL", pct: 20 },
  ],
  typedSequence: [null, 200, 500, 1000],
});
