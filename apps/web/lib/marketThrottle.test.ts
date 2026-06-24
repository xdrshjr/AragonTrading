import { describe, expect, it } from "vitest";
import {
  HIGH_CHANGE_THRESHOLD,
  createMarketThrottle,
  isHighChange,
  type MarketFrame,
} from "./marketThrottle";
import type { MarketTicker, MarketTrade, OrderBookSnapshot } from "./types";

interface FakeTask {
  fireAt: number;
  cb: () => void;
  cancelled: boolean;
}

// 确定性假时钟：注入 now/setTimer/clearTimer，advance(ms) 推进并触发到期任务（沿用 chartSync 可测范式）。
function makeClock() {
  let current = 0;
  const tasks: FakeTask[] = [];
  return {
    now: () => current,
    setTimer: (cb: () => void, ms: number): FakeTask => {
      const task: FakeTask = { fireAt: current + ms, cb, cancelled: false };
      tasks.push(task);
      return task;
    },
    clearTimer: (handle: unknown) => {
      (handle as FakeTask).cancelled = true;
    },
    advance: (ms: number) => {
      current += ms;
      let ran = true;
      while (ran) {
        ran = false;
        for (const task of tasks) {
          if (!task.cancelled && task.fireAt <= current) {
            task.cancelled = true;
            ran = true;
            task.cb();
          }
        }
      }
    },
  };
}

const ticker = (last: number, ts: number): MarketTicker => ({
  ts,
  last,
  open24h: null,
  vol24h: null,
  volCcy24h: null,
});
const trade = (price: number, ts: number, tradeId: string): MarketTrade => ({
  ts,
  price,
  qty: 1,
  side: "buy",
  tradeId,
});
const book = (ts: number): OrderBookSnapshot => ({
  ts,
  asks: [{ price: 100.1, qty: 1 }],
  bids: [{ price: 99.9, qty: 1 }],
});

function harness() {
  const clock = makeClock();
  const frames: MarketFrame[] = [];
  const throttle = createMarketThrottle({
    flush: (frame) => frames.push(frame),
    now: clock.now,
    setTimer: clock.setTimer,
    clearTimer: clock.clearTimer,
  });
  return { clock, frames, throttle };
}

describe("isHighChange", () => {
  it("treats the first sample (no baseline) as not high change", () => {
    expect(isHighChange(100, null, HIGH_CHANGE_THRESHOLD)).toBe(false);
    expect(isHighChange(null, 100, HIGH_CHANGE_THRESHOLD)).toBe(false);
    expect(isHighChange(100, 0, HIGH_CHANGE_THRESHOLD)).toBe(false);
  });

  it("flags a relative move at or above the threshold", () => {
    expect(isHighChange(100.06, 100, HIGH_CHANGE_THRESHOLD)).toBe(true); // 6bp
    expect(isHighChange(100.02, 100, HIGH_CHANGE_THRESHOLD)).toBe(false); // 2bp
  });
});

describe("createMarketThrottle", () => {
  it("flushes the first frame immediately (leading edge) so the panel never blanks for a full window", () => {
    const { frames, throttle } = harness();
    throttle.pushBook(book(5));
    expect(frames).toHaveLength(1);
    expect(frames[0].book?.ts).toBe(5);
    expect(frames[0].marketTs).toBe(5);
  });

  it("coalesces a calm window into one baseline (1000ms) flush", () => {
    const { clock, frames, throttle } = harness();
    throttle.pushTicker(ticker(100, 1)); // leading flush, baseline price = 100
    throttle.pushTrade(trade(100.01, 10, "a"));
    throttle.pushTrade(trade(100.02, 20, "b"));
    expect(frames).toHaveLength(1);
    clock.advance(999);
    expect(frames).toHaveLength(1);
    clock.advance(1);
    expect(frames).toHaveLength(2);
    expect(frames[1].trades.map((t) => t.tradeId)).toEqual(["a", "b"]);
    expect(frames[1].lastPrice).toBe(100.02);
  });

  it("speeds up to the 500ms window when price moves sharply mid-window", () => {
    const { clock, frames, throttle } = harness();
    throttle.pushTicker(ticker(100, 1)); // leading flush, baseline = 100
    throttle.pushTrade(trade(100.01, 10, "a")); // calm → schedules baseline 1000ms
    throttle.pushTrade(trade(101, 20, "b")); // +1% → upgrades window to 500ms
    clock.advance(499);
    expect(frames).toHaveLength(1);
    clock.advance(1);
    expect(frames).toHaveLength(2);
  });

  it("keeps the newest-timestamp price while still carrying the latest ticker object", () => {
    const { clock, frames, throttle } = harness();
    throttle.pushTicker(ticker(100, 1)); // leading flush
    throttle.pushTrade(trade(101, 50, "a")); // price candidate ts=50
    throttle.pushTicker(ticker(99, 30)); // older ts → must not override price
    clock.advance(1000);
    expect(frames[1].lastPrice).toBe(101);
    expect(frames[1].ticker?.last).toBe(99);
  });

  it("routes candle price through the buffer without advancing the market timestamp", () => {
    const { clock, frames, throttle } = harness();
    throttle.pushBook(book(5)); // leading flush
    throttle.pushPrice(100.5, 30); // candle close, no book/ticker/trade
    clock.advance(1000);
    expect(frames[1].lastPrice).toBe(100.5);
    expect(frames[1].marketTs).toBeNull();
    expect(frames[1].book).toBeNull();
    expect(frames[1].trades).toHaveLength(0);
  });

  it("dispose() cancels the pending flush", () => {
    const { clock, frames, throttle } = harness();
    throttle.pushTicker(ticker(100, 1)); // leading flush
    throttle.pushTrade(trade(100.01, 10, "a")); // schedules a pending flush
    throttle.dispose();
    clock.advance(5000);
    expect(frames).toHaveLength(1);
  });

  it("reset() clears the baseline so the next push leads again", () => {
    const { frames, throttle } = harness();
    throttle.pushTicker(ticker(100, 1)); // leading flush
    throttle.reset();
    throttle.pushTrade(trade(200, 10, "a")); // baseline gone → flushes immediately
    expect(frames).toHaveLength(2);
    expect(frames[1].lastPrice).toBe(200);
  });
});
