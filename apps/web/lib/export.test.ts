import { describe, expect, it } from "vitest";
import { downloadCsv, equityToCsv, toCsv, tradesToCsv } from "./export";
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

  it("escapes fields containing comma / quote / newline (RFC4180)", () => {
    const out = toCsv([{ v: 'he said "hi", ok\nnext' }], [{ key: "v", label: "v" }]);
    // 内部 " -> ""，整字段用双引号包裹；换行 / 逗号保留在引号内
    expect(out).toBe('v\r\n"he said ""hi"", ok\nnext"');
  });

  it("renders null / undefined as empty string", () => {
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
  it("maps columns in order with ISO8601 UTC time", () => {
    const trades: Trade[] = [
      { ts: 1609459200, side: "long", price: 100, qty: 0.5, fee: 0.1, pnl: 0, positionAfter: 0.5, reason: "entry" },
      { ts: 1609462800, side: "close", price: 110, qty: 0.5, fee: 0.1, pnl: 5, positionAfter: 0, reason: "tp" },
    ];
    const lines = tradesToCsv(trades).split("\r\n");
    expect(lines[0]).toBe("时间(UTC),方向,价格,数量,手续费,盈亏,持仓后,原因");
    expect(lines).toHaveLength(3); // 表头 + 2 行
    expect(lines[1]).toBe("2021-01-01T00:00:00.000Z,long,100,0.5,0.1,0,0.5,entry");
    expect(lines[2]).toBe("2021-01-01T01:00:00.000Z,close,110,0.5,0.1,5,0,tp");
  });

  it("quotes a reason field containing a comma", () => {
    const trades: Trade[] = [
      { ts: 1609459200, side: "long", price: 1, qty: 1, fee: 0, pnl: 0, positionAfter: 1, reason: "rsi<30, dip" },
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
    expect(lines[0]).toBe("时间(UTC),净值,回撤");
    expect(lines).toHaveLength(3);
    expect(lines[2]).toBe("2021-01-01T01:00:00.000Z,10120.5,-0.01");
  });
});

describe("downloadCsv", () => {
  it("prepends a UTF-8 BOM and is jsdom-safe", () => {
    const captured: BlobPart[][] = [];
    const OrigBlob = globalThis.Blob;
    const OrigCreate = URL.createObjectURL;
    const OrigRevoke = URL.revokeObjectURL;
    const OrigClick = HTMLAnchorElement.prototype.click;
    // 测试替身：捕获传入 Blob 的分片（jsdom 的 createObjectURL 未实现，需打桩）；
    // 同时把 <a> 的 click 桩成 no-op，避免 jsdom 真的尝试 navigation 打印噪声。
    // @ts-expect-error 仅测试用最小替身
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
      downloadCsv("aragon-trades.csv", "a,b\r\n1,2");
      expect(captured).toHaveLength(1);
      expect(String(captured[0][0]).startsWith("﻿")).toBe(true);
    } finally {
      globalThis.Blob = OrigBlob;
      URL.createObjectURL = OrigCreate;
      URL.revokeObjectURL = OrigRevoke;
      HTMLAnchorElement.prototype.click = OrigClick;
    }
  });
});
