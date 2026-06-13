import { describe, expect, it } from "vitest";
import {
  bollinger,
  DEFAULT_INDICATORS,
  ema,
  getIndicator,
  INDICATORS,
  kdj,
  macd,
  rsi,
  sma,
} from "./indicators";
import type { Candle } from "./types";

// 构造测试用 K 线：仅给定 close（o/h/l 由 close 派生），ts 从 1 起严格递增。
function candlesFromCloses(closes: number[], vols?: number[]): Candle[] {
  return closes.map((c, i) => ({
    ts: i + 1,
    o: i > 0 ? closes[i - 1] : c,
    h: c + 1,
    l: c - 1,
    c,
    vol: vols ? vols[i] : 100,
  }));
}

describe("sma", () => {
  it("returns undefined during warmup then window mean", () => {
    expect(sma([1, 2, 3, 4, 5], 3)).toEqual([undefined, undefined, 2, 3, 4]);
  });
  it("handles period larger than input", () => {
    expect(sma([1, 2], 5)).toEqual([undefined, undefined]);
  });
});

describe("ema", () => {
  it("seeds with SMA of first period and recurses", () => {
    const out = ema([1, 2, 3, 4, 5], 3);
    // 种子 = SMA(1,2,3)=2 在 index 2
    expect(out[0]).toBeUndefined();
    expect(out[1]).toBeUndefined();
    expect(out[2]).toBeCloseTo(2, 10);
    // k=2/4=0.5 → ema[3]=4*0.5+2*0.5=3
    expect(out[3]).toBeCloseTo(3, 10);
    // ema[4]=5*0.5+3*0.5=4
    expect(out[4]).toBeCloseTo(4, 10);
  });
});

describe("bollinger", () => {
  it("computes mid/upper/lower with population std", () => {
    const closes = [2, 4, 6, 8, 10];
    const { mid, upper, lower } = bollinger(closes, 5, 2);
    // 仅最后一位有效：mid=6，方差=((−4)^2+(−2)^2+0+2^2+4^2)/5=8 → std=√8
    expect(mid[4]).toBeCloseTo(6, 10);
    const std = Math.sqrt(8);
    expect(upper[4]).toBeCloseTo(6 + 2 * std, 10);
    expect(lower[4]).toBeCloseTo(6 - 2 * std, 10);
    expect(mid[3]).toBeUndefined();
  });
});

describe("macd", () => {
  it("DIF valid from slow-1, HIST = (DIF-DEA)*2 sign consistent", () => {
    const closes = Array.from({ length: 60 }, (_, i) => 100 + i); // 单调上涨
    const { dif, dea, hist } = macd(closes, 12, 26, 9);
    // dif 在 index < 25 必为 undefined，>=25 起有效
    expect(dif[24]).toBeUndefined();
    expect(dif[25]).toBeDefined();
    // 找一个 dif/dea/hist 同时有效的点验证关系
    const i = closes.length - 1;
    expect(hist[i]).toBeCloseTo(((dif[i] as number) - (dea[i] as number)) * 2, 10);
    // 单调上涨 → DIF 应为正
    expect(dif[i] as number).toBeGreaterThan(0);
  });
});

describe("rsi", () => {
  it("approaches 100 on monotonic rise", () => {
    const closes = Array.from({ length: 30 }, (_, i) => 100 + i);
    const out = rsi(closes, 14);
    expect(out[13]).toBeUndefined();
    expect(out[14]).toBeCloseTo(100, 10); // 全程上涨 avgLoss=0 → 100
  });
  it("approaches 0 on monotonic fall", () => {
    const closes = Array.from({ length: 30 }, (_, i) => 100 - i);
    const out = rsi(closes, 14);
    expect(out[14]).toBeCloseTo(0, 6); // 全程下跌 avgGain=0 → 0
  });
  it("first valid index equals period", () => {
    const out = rsi([1, 2, 1, 2, 1, 2], 3);
    expect(out[2]).toBeUndefined();
    expect(out[3]).toBeDefined();
  });
});

describe("kdj", () => {
  it("does not produce NaN on flat range (zero denom) and seeds K/D at 50", () => {
    const candles = candlesFromCloses([5, 5, 5, 5, 5, 5, 5, 5, 5, 5]);
    // 强制横盘：把 h/l 也设为相等以触发分母为 0
    const flat: Candle[] = candles.map((c) => ({ ...c, h: 5, l: 5, c: 5, o: 5 }));
    const { k, d, j } = kdj(flat, 9, 3, 3);
    expect(k.every((v) => v === undefined || Number.isFinite(v))).toBe(true);
    expect(d.every((v) => v === undefined || Number.isFinite(v))).toBe(true);
    expect(j.every((v) => v === undefined || Number.isFinite(v))).toBe(true);
    // RSV 横盘取 50 → K=2/3*50+1/3*50=50
    expect(k[8]).toBeCloseTo(50, 10);
    expect(d[8]).toBeCloseTo(50, 10);
  });
});

describe("indicator registry", () => {
  it("DEFAULT_INDICATORS matches user requirement (MA + BOLL + MACD on)", () => {
    expect(DEFAULT_INDICATORS).toEqual({
      ma: true,
      ema: false,
      boll: true,
      macd: true,
      rsi: false,
      kdj: false,
      vol: false,
    });
  });

  it("getIndicator finds by key and unknown returns undefined", () => {
    expect(getIndicator("macd")?.label).toBe("MACD");
    expect(getIndicator("nope")).toBeUndefined();
  });

  it("compute returns empty data on empty candles without throwing", () => {
    const tc = { long: "#0f0", short: "#f00", muted: "#888", accent: "#c75", text: "#000" };
    for (const def of INDICATORS) {
      const plots = def.compute([], tc);
      expect(Array.isArray(plots)).toBe(true);
      for (const p of plots) expect(p.data.length).toBe(0);
    }
  });

  it("compute output time strictly ascending and aligned to candle ts", () => {
    const tc = { long: "#0f0", short: "#f00", muted: "#888", accent: "#c75", text: "#000" };
    const candles = candlesFromCloses(
      Array.from({ length: 80 }, (_, i) => 100 + Math.sin(i / 5) * 10),
    );
    const tsSet = new Set(candles.map((c) => c.ts));
    for (const def of INDICATORS) {
      for (const plot of def.compute(candles, tc)) {
        const times = plot.data.map((d) => d.time as number);
        for (let i = 1; i < times.length; i++) expect(times[i]).toBeGreaterThan(times[i - 1]);
        for (const t of times) expect(tsSet.has(t)).toBe(true);
      }
    }
  });

  it("VOL colors bars by candle direction using theme colors", () => {
    const tc = { long: "#0f0", short: "#f00", muted: "#888", accent: "#c75", text: "#000" };
    const candles: Candle[] = [
      { ts: 1, o: 10, h: 12, l: 9, c: 11, vol: 5 }, // 涨 → long
      { ts: 2, o: 11, h: 11, l: 8, c: 9, vol: 7 }, // 跌 → short
    ];
    const plots = getIndicator("vol")!.compute(candles, tc);
    const hist = plots[0];
    expect(hist.kind).toBe("histogram");
    expect(hist.data[0]).toMatchObject({ value: 5, color: "#0f0" });
    expect(hist.data[1]).toMatchObject({ value: 7, color: "#f00" });
  });
});
