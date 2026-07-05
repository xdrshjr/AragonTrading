import { describe, expect, it } from "vitest";
import {
  downloadCsv,
  downloadJson,
  equityToCsv,
  resultToJson,
  toCsv,
  tradesToCsv,
} from "./export";
import type { AiBacktestResult } from "./aiBacktestTypes";
import type { EquityPoint, Trade } from "./types";

describe("toCsv", () => {
  it("emits the header row first using column labels", () => {
    const out = toCsv(
      [{ a: 1, b: 2 }],
      [
        { key: "a", label: "A" },
        { key: "b", label: "B" },
      ],
    );
    expect(out).toBe("A,B\r\n1,2");
    expect(out.split("\r\n")[0]).toBe("A,B");
  });

  it("escapes fields containing comma, quote, or newline", () => {
    const out = toCsv([{ v: 'he said "hi", ok\nnext' }], [{ key: "v", label: "v" }]);
    expect(out).toBe('v\r\n"he said ""hi"", ok\nnext"');
  });

  it("renders null and undefined as empty string", () => {
    const out = toCsv(
      [{ a: null, b: undefined }],
      [
        { key: "a", label: "A" },
        { key: "b", label: "B" },
      ],
    );
    expect(out).toBe("A,B\r\n,");
  });
});

describe("tradesToCsv", () => {
  it("places risk snapshot columns after pnl and before positionAfter", () => {
    const trades: Trade[] = [
      {
        ts: 1609459200,
        side: "long",
        price: 100,
        qty: 0.5,
        fee: 0.1,
        pnl: 0,
        stopLossPct: 0.02,
        stopLossPrice: 98,
        takeProfitPct: 0.04,
        takeProfitPrice: 104,
        maxHoldBars: 12,
        positionAfter: 0.5,
        reason: "entry",
      },
      {
        ts: 1609462800,
        side: "close",
        price: 110,
        qty: 0.5,
        fee: 0.1,
        pnl: 5,
        stopLossPct: null,
        stopLossPrice: null,
        takeProfitPct: null,
        takeProfitPrice: null,
        maxHoldBars: null,
        positionAfter: 0,
        reason: "tp",
      },
    ];

    const lines = tradesToCsv(trades).split("\r\n");

    expect(lines[0].split(",").slice(6, 11)).toEqual([
      "止损比例(SL%)",
      "止损价格(SL)",
      "止盈比例(TP%)",
      "止盈价格(TP)",
      "最长持有K线",
    ]);
    expect(lines).toHaveLength(3);
    expect(lines[1]).toBe(
      "2021-01-01T00:00:00.000Z,long,100,0.5,0.1,0,0.02,98,0.04,104,12,0.5,entry",
    );
    expect(lines[2]).toBe("2021-01-01T01:00:00.000Z,close,110,0.5,0.1,5,,,,,,0,tp");
  });

  it("quotes a reason field containing a comma", () => {
    const trades: Trade[] = [
      {
        ts: 1609459200,
        side: "long",
        price: 1,
        qty: 1,
        fee: 0,
        pnl: 0,
        positionAfter: 1,
        reason: "rsi<30, dip",
      },
    ];
    const lines = tradesToCsv(trades).split("\r\n");
    expect(lines[1].endsWith('"rsi<30, dip"')).toBe(true);
  });
});

describe("equityToCsv", () => {
  it("maps equity rows with header and column order", () => {
    const eq: EquityPoint[] = [
      { ts: 1609459200, equity: 10000, drawdown: 0 },
      { ts: 1609462800, equity: 10120.5, drawdown: -0.01 },
    ];
    const lines = equityToCsv(eq).split("\r\n");
    expect(lines[0].split(",")).toHaveLength(3);
    expect(lines).toHaveLength(3);
    expect(lines[2]).toBe("2021-01-01T01:00:00.000Z,10120.5,-0.01");
  });
});

function minimalResult(): AiBacktestResult {
  return {
    runId: 1,
    metrics: { totalReturn: 0.1, winRate: 0.5, profitFactor: 1.2, maxDrawdown: -0.05, sharpe: 1, tradeCount: 2, avgPnl: 5 },
    aiStats: {
      decisionRounds: 2, actionCounts: { open_long: 1, open_short: 0, close: 1, hold: 0 },
      holdRate: 0, avgConfidence: 0.8, degradedRounds: 0, timeoutRounds: 0, summaryCount: 0,
      decisionEveryBars: 1, plannedRounds: 2, reachedRoundCap: false,
    },
    equity: [{ ts: 1, equity: 10000, drawdown: 0 }],
    trades: [], decisionsDigest: [], summaries: [],
    startTs: 1, endTs: 2, barsProcessed: 2, notes: [],
    benchmark: { equity: [], totalReturn: 0.05, maxDrawdown: -0.02, sharpe: 0.9, annualizedReturn: 0.1, alpha: 0.05 },
  };
}

describe("resultToJson", () => {
  it("serializes a result that round-trips through JSON.parse with all keys", () => {
    const result = minimalResult();
    const json = resultToJson(result);
    const parsed = JSON.parse(json);
    expect(parsed).toEqual(result);
    expect(parsed.benchmark.alpha).toBe(0.05);
  });
});

describe("downloadJson", () => {
  it("emits BOM-free application/json that JSON.parse can read directly", () => {
    const captured: { parts: BlobPart[]; opts?: BlobPropertyBag }[] = [];
    const OrigBlob = globalThis.Blob;
    const OrigCreate = URL.createObjectURL;
    const OrigRevoke = URL.revokeObjectURL;
    const OrigClick = HTMLAnchorElement.prototype.click;
    // @ts-expect-error test replacement
    globalThis.Blob = class {
      parts: BlobPart[];
      constructor(parts: BlobPart[], opts?: BlobPropertyBag) {
        this.parts = parts;
        captured.push({ parts, opts });
      }
    };
    URL.createObjectURL = () => "blob:mock";
    URL.revokeObjectURL = () => {};
    HTMLAnchorElement.prototype.click = () => {};
    try {
      downloadJson("ai-backtest.json", resultToJson(minimalResult()));
      expect(captured).toHaveLength(1);
      const content = String(captured[0].parts[0]);
      expect(content.charCodeAt(0)).not.toBe(0xfeff); // 无前导 BOM（不破坏 JSON.parse）
      expect(() => JSON.parse(content)).not.toThrow();
      expect(captured[0].opts?.type).toContain("application/json");
    } finally {
      globalThis.Blob = OrigBlob;
      URL.createObjectURL = OrigCreate;
      URL.revokeObjectURL = OrigRevoke;
      HTMLAnchorElement.prototype.click = OrigClick;
    }
  });
});

describe("downloadCsv", () => {
  it("prepends a BOM-like prefix and is jsdom-safe", () => {
    const captured: BlobPart[][] = [];
    const OrigBlob = globalThis.Blob;
    const OrigCreate = URL.createObjectURL;
    const OrigRevoke = URL.revokeObjectURL;
    const OrigClick = HTMLAnchorElement.prototype.click;
    // @ts-expect-error test replacement
    globalThis.Blob = class {
      parts: BlobPart[];
      constructor(parts: BlobPart[]) {
        this.parts = parts;
        captured.push(parts);
      }
    };
    URL.createObjectURL = () => "blob:mock";
    URL.revokeObjectURL = () => {};
    HTMLAnchorElement.prototype.click = () => {};
    try {
      const content = "a,b\r\n1,2";
      downloadCsv("aragon-trades.csv", content);
      expect(captured).toHaveLength(1);
      expect(String(captured[0][0]).endsWith(content)).toBe(true);
      expect(String(captured[0][0]).length).toBeGreaterThan(content.length);
    } finally {
      globalThis.Blob = OrigBlob;
      URL.createObjectURL = OrigCreate;
      URL.revokeObjectURL = OrigRevoke;
      HTMLAnchorElement.prototype.click = OrigClick;
    }
  });
});
