import { describe, expect, it } from "vitest";
import { mergeEquityByTs } from "./liveSeries";
import type { EquityPoint } from "./types";

const pt = (ts: number, equity: number, transient = false): EquityPoint => ({
  ts,
  equity,
  drawdown: 0,
  transient,
});

describe("mergeEquityByTs", () => {
  it("appends a new ts and keeps ascending order", () => {
    const out = mergeEquityByTs([pt(1, 100), pt(3, 110)], pt(2, 105));
    expect(out.map((p) => p.ts)).toEqual([1, 2, 3]);
  });

  it("upserts same ts so the series stays strictly increasing (P1-3)", () => {
    // transient(ts=3) 先到，随后确认点(ts=3) 覆盖之 → ts 唯一、保留最后写入值
    let series: EquityPoint[] = [pt(1, 100), pt(2, 105)];
    series = mergeEquityByTs(series, pt(3, 108, true)); // 瞬时盯市
    series = mergeEquityByTs(series, pt(3, 107, false)); // 同根确认结算
    const tsList = series.map((p) => p.ts);
    expect(tsList).toEqual([1, 2, 3]);
    expect(new Set(tsList).size).toBe(tsList.length); // 无重复 ts
    const last = series[series.length - 1];
    expect(last.equity).toBe(107); // 确认值覆盖瞬时值
    expect(last.transient).toBe(false);
  });

  it("handles unordered inserts by sorting", () => {
    let series: EquityPoint[] = [];
    for (const ts of [5, 2, 9, 1]) series = mergeEquityByTs(series, pt(ts, ts * 10));
    expect(series.map((p) => p.ts)).toEqual([1, 2, 5, 9]);
  });
});
