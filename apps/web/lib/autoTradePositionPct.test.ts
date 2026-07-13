import { describe, expect, it } from "vitest";
import type { PaperPositionEvent } from "./types";
import type { LivePosition } from "./tradingTypes";
import { autoTradePositionPct } from "./autoTradePositionPct";

function livePosition(over: Partial<LivePosition> = {}): LivePosition {
  return {
    instId: "BTC-USDT-SWAP",
    instType: "SWAP",
    posSide: "long",
    pos: 2,
    avgPx: 30000,
    last: 30100,
    upl: 200,
    uplRatio: 0.01,
    lever: 2,
    marginMode: "cross",
    ...over,
  };
}

function runtimePosition(over: Partial<PaperPositionEvent> = {}): PaperPositionEvent {
  return {
    side: "long",
    size: 2,
    avgEntry: 30000,
    markPrice: 30100,
    unrealizedPnl: 200,
    positionPct: 0.4,
    ...over,
  };
}

describe("autoTradePositionPct", () => {
  it("matches ordinary, AI, and compatible single-session positions", () => {
    const live = livePosition();
    expect(autoTradePositionPct(live, {
      liveMembers: { [live.instId]: { position: runtimePosition() } },
    })).toBe(0.4);
    expect(autoTradePositionPct(live, {
      aiMembers: { [live.instId]: { position: runtimePosition({ positionPct: 0.3 }) } },
    })).toBe(0.3);
    expect(autoTradePositionPct(live, {
      single: { instId: live.instId, position: runtimePosition({ positionPct: 0.2 }) },
    })).toBe(0.2);
  });

  it("matches short and net-mode directions from the signed OKX size", () => {
    const short = runtimePosition({ side: "short", positionPct: 0.25 });
    expect(autoTradePositionPct(livePosition({ posSide: "short", pos: 2 }), {
      liveMembers: { "BTC-USDT-SWAP": { position: short } },
    })).toBe(0.25);
    expect(autoTradePositionPct(livePosition({ posSide: "net", pos: -2 }), {
      liveMembers: { "BTC-USDT-SWAP": { position: short } },
    })).toBe(0.25);
  });

  it.each([
    ["manual position", {}, livePosition()],
    ["wrong instrument", { single: { instId: "ETH-USDT-SWAP", position: runtimePosition() } }, livePosition()],
    ["wrong direction", { liveMembers: { "BTC-USDT-SWAP": { position: runtimePosition({ side: "short" }) } } }, livePosition()],
    ["unfinished size", { liveMembers: { "BTC-USDT-SWAP": { position: runtimePosition({ size: 1 }) } } }, livePosition()],
    ["flat runtime", { liveMembers: { "BTC-USDT-SWAP": { position: runtimePosition({ side: "flat" }) } } }, livePosition()],
    ["stopped ordinary member", { liveMembers: { "BTC-USDT-SWAP": {
      position: runtimePosition(), status: { state: "stopped" },
    } } }, livePosition()],
    ["stopped AI member", { aiMembers: { "BTC-USDT-SWAP": {
      position: runtimePosition(), session: { state: "stopped" },
    } } }, livePosition()],
    ["invalid percent", { liveMembers: { "BTC-USDT-SWAP": { position: runtimePosition({ positionPct: Infinity }) } } }, livePosition()],
    ["negative percent", { liveMembers: { "BTC-USDT-SWAP": { position: runtimePosition({ positionPct: -0.1 }) } } }, livePosition()],
    ["invalid live size", { liveMembers: { "BTC-USDT-SWAP": { position: runtimePosition() } } }, livePosition({ pos: NaN })],
  ])("returns null for %s", (_name, sources, live) => {
    expect(autoTradePositionPct(live, sources)).toBeNull();
  });
});
