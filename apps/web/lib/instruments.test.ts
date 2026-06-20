import { describe, expect, it } from "vitest";
import type { Instrument } from "./types";
import {
  filterAndRankInstruments,
  inferInstType,
  MAX_VISIBLE_ROWS,
  resolveWatchlistRows,
} from "./instruments";

function inst(instId: string, partial: Partial<Instrument> = {}): Instrument {
  const parts = instId.split("-");
  return {
    instId,
    instType: inferInstType(instId),
    base: parts[0] ?? instId,
    quote: parts[1] ?? "",
    last: null,
    open24h: null,
    vol24h: null,
    volCcy24h: null,
    changePct: null,
    ...partial,
  };
}

describe("inferInstType", () => {
  it("infers SPOT / SWAP / FUTURES from the instId suffix", () => {
    expect(inferInstType("BTC-USDT")).toBe("SPOT");
    expect(inferInstType("BTC-USDT-SWAP")).toBe("SWAP");
    expect(inferInstType("BTC-USDT-250328")).toBe("FUTURES");
    expect(inferInstType("BTC-USD-20251226")).toBe("FUTURES");
  });
});

describe("filterAndRankInstruments", () => {
  const list = [
    inst("BTC-USDT-SWAP", { volCcy24h: 9e9 }),
    inst("ETH-USDT-SWAP", { volCcy24h: 5e9 }),
    inst("SOL-USDC-SWAP", { volCcy24h: 3e9 }),
    inst("BTC-USDT-250328", { volCcy24h: 1e9 }),
  ];

  it("returns the full set in input (volume-desc) order when query is empty", () => {
    const { rows, total, truncated } = filterAndRankInstruments(list, { query: "" });
    expect(rows.map((r) => r.instId)).toEqual([
      "BTC-USDT-SWAP",
      "ETH-USDT-SWAP",
      "SOL-USDC-SWAP",
      "BTC-USDT-250328",
    ]);
    expect(total).toBe(4);
    expect(truncated).toBe(false);
  });

  it("matches instId and base case-insensitively", () => {
    expect(filterAndRankInstruments(list, { query: "btc" }).rows.map((r) => r.instId)).toEqual([
      "BTC-USDT-SWAP",
      "BTC-USDT-250328",
    ]);
    expect(filterAndRankInstruments(list, { query: "ETH" }).total).toBe(1);
    expect(filterAndRankInstruments(list, { query: "usdc" }).rows[0].instId).toBe("SOL-USDC-SWAP");
  });

  it("filters by quote currency", () => {
    expect(filterAndRankInstruments(list, { query: "", quote: "USDC" }).total).toBe(1);
    expect(filterAndRankInstruments(list, { query: "", quote: "USDT" }).total).toBe(3);
    expect(filterAndRankInstruments(list, { query: "", quote: "ALL" }).total).toBe(4);
  });

  it("filters by instTypes: empty/undefined unlimited, [SWAP] perpetual, [FUTURES] dated", () => {
    expect(filterAndRankInstruments(list, { query: "" }).total).toBe(4);
    expect(filterAndRankInstruments(list, { query: "", instTypes: [] }).total).toBe(4);
    expect(filterAndRankInstruments(list, { query: "", instTypes: ["SWAP"] }).total).toBe(3);
    const dated = filterAndRankInstruments(list, { query: "", instTypes: ["FUTURES"] });
    expect(dated.total).toBe(1);
    expect(dated.rows[0].instId).toBe("BTC-USDT-250328");
  });

  it("truncates to limit and reports total/truncated", () => {
    const big = Array.from({ length: MAX_VISIBLE_ROWS + 5 }, (_, i) => inst(`AAA${i}-USDT-SWAP`));
    const { rows, total, truncated } = filterAndRankInstruments(big, { query: "" });
    expect(rows).toHaveLength(MAX_VISIBLE_ROWS);
    expect(total).toBe(MAX_VISIBLE_ROWS + 5);
    expect(truncated).toBe(true);
    expect(filterAndRankInstruments(big, { query: "", limit: 3 }).rows).toHaveLength(3);
  });

  it("returns empty for an empty/undefined list without throwing", () => {
    expect(filterAndRankInstruments([], { query: "btc" })).toEqual({
      rows: [],
      total: 0,
      truncated: false,
    });
    expect(filterAndRankInstruments(undefined, { query: "" }).total).toBe(0);
  });
});

describe("resolveWatchlistRows", () => {
  it("resolves quotes from the map and preserves watchlist order", () => {
    const byInstId = new Map<string, Instrument>([
      ["ETH-USDT-SWAP", inst("ETH-USDT-SWAP", { last: 3000, changePct: 0.02 })],
      ["BTC-USDT-SWAP", inst("BTC-USDT-SWAP", { last: 64000, changePct: -0.01 })],
    ]);
    const rows = resolveWatchlistRows(["BTC-USDT-SWAP", "ETH-USDT-SWAP"], byInstId);
    expect(rows.map((r) => r.instId)).toEqual(["BTC-USDT-SWAP", "ETH-USDT-SWAP"]);
    expect(rows[0].last).toBe(64000);
  });

  it("degrades missing instruments to a visible placeholder row", () => {
    const rows = resolveWatchlistRows(["PEPE-USDT", "DOOM-USDT-SWAP"], new Map());
    expect(rows).toHaveLength(2);
    expect(rows[0]).toMatchObject({ instId: "PEPE-USDT", base: "PEPE", quote: "USDT", last: null });
    expect(rows[1].instType).toBe("SWAP");
  });
});
