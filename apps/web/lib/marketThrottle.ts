// 行情高频推流的前端自适应节流器（诊断 market-depth-refresh-too-frequent，方案 A）。
// OKX 的 books5/tickers（≈10Hz）+ 逐笔 trades 经 ws 原样透传，原实现每条消息直接一次 setState，
// 叠加成 20~40+Hz 的整页重渲染。这里把这股高频流缓冲到 ref 级（不触发渲染），按「常规 1000ms /
// 剧烈变化 500ms」的自适应节奏一次性刷入 React state，把刷新稳定在 1~2Hz。
// 范式沿用 chartSync.ts：闭包持有可变缓冲 + 注入 now/timer 以便确定性单测。
import type { MarketTicker, MarketTrade, OrderBookSnapshot } from "./types";

// 自适应间隔：常规 1Hz，剧烈行情提速到 2Hz（本轮需求）。
export const BASELINE_INTERVAL_MS = 1000;
export const HIGH_CHANGE_INTERVAL_MS = 500;
// 「剧烈变化」阈值：自上次刷入以来最新价相对变动达到 5bp（0.05%）即视为剧烈、提速刷新。
// 具名常量便于调参。
export const HIGH_CHANGE_THRESHOLD = 5e-4;

// 一次刷入 React state 的合并帧：只携带本窗口出现过的最新数据，未出现的字段为空，
// 由消费方决定是否落库，避免无谓的 state churn。
export interface MarketFrame {
  book: OrderBookSnapshot | null;
  ticker: MarketTicker | null;
  trades: MarketTrade[];
  lastPrice: number | null;
  marketTs: number | null;
}

type TimerHandle = unknown;

export interface MarketThrottleConfig {
  // 定时触发时把合并帧交给消费方刷入 React state（具体 setState 由消费方负责）。
  flush: (frame: MarketFrame) => void;
  now?: () => number;
  setTimer?: (cb: () => void, ms: number) => TimerHandle;
  clearTimer?: (handle: TimerHandle) => void;
  baselineMs?: number;
  highChangeMs?: number;
  highChangeThreshold?: number;
}

export interface MarketThrottle {
  pushTrade(trade: MarketTrade): void;
  pushTicker(ticker: MarketTicker): void;
  pushBook(book: OrderBookSnapshot): void;
  // candle 收盘/未完成价经调用方门控后并入同一缓冲，使 flush 成为 lastPrice 的唯一写入口，
  // 杜绝「candle 即时写 + 节流写」两个入口互相打架。
  pushPrice(price: number, ts: number): void;
  reset(): void;
  dispose(): void;
}

// 首样本守卫：无基线（首帧）或基线为 0 时不做相对变动除法，一律按常规处置。
export function isHighChange(
  current: number | null,
  baseline: number | null,
  threshold: number,
): boolean {
  if (current == null || baseline == null || baseline === 0) return false;
  return Math.abs(current - baseline) / Math.abs(baseline) >= threshold;
}

export function createMarketThrottle(config: MarketThrottleConfig): MarketThrottle {
  const now = config.now ?? (() => Date.now());
  const setTimer = config.setTimer ?? ((cb, ms) => setTimeout(cb, ms));
  const clearTimer =
    config.clearTimer ?? ((handle) => clearTimeout(handle as ReturnType<typeof setTimeout>));
  const baselineMs = config.baselineMs ?? BASELINE_INTERVAL_MS;
  const highChangeMs = config.highChangeMs ?? HIGH_CHANGE_INTERVAL_MS;
  const threshold = config.highChangeThreshold ?? HIGH_CHANGE_THRESHOLD;

  // 挂起缓冲（ref 级，不触发渲染）
  let book: OrderBookSnapshot | null = null;
  let ticker: MarketTicker | null = null;
  let trades: MarketTrade[] = [];
  let lastPrice: number | null = null;
  let priceTs = -1; // 当前 lastPrice 候选 ts，用于「新者胜出」合并多个价格来源
  let marketTs: number | null = null; // book/ticker/trade 最大 ts，驱动面板「updated」时间
  let hasData = false;

  // 节奏基准
  let lastFlushedPrice: number | null = null; // 上次刷入价，高变化判定基线
  let lastFlushTs: number | null = null; // 上次刷入墙钟时刻，作节奏锚点
  let timer: TimerHandle | null = null;
  let scheduledFireTs = Infinity; // 当前定时器目标触发时刻
  let disposed = false;

  const setPriceCandidate = (price: number, ts: number) => {
    if (ts >= priceTs) {
      lastPrice = price;
      priceTs = ts;
    }
  };

  const flush = () => {
    if (timer !== null) {
      clearTimer(timer);
      timer = null;
    }
    scheduledFireTs = Infinity;
    if (!hasData) return;
    const frame: MarketFrame = { book, ticker, trades, lastPrice, marketTs };
    if (lastPrice != null) lastFlushedPrice = lastPrice; // 推进高变化基线
    lastFlushTs = now();
    book = null;
    ticker = null;
    trades = [];
    lastPrice = null;
    priceTs = -1;
    marketTs = null;
    hasData = false;
    config.flush(frame);
  };

  const onTimer = () => {
    timer = null;
    scheduledFireTs = Infinity;
    if (disposed) return;
    flush();
  };

  const schedule = () => {
    if (disposed || !hasData) return;
    // 首帧立即刷入（leading edge）：避免连上后最长 baselineMs 仍停在「Waiting for OKX…」。
    if (lastFlushTs == null) {
      flush();
      return;
    }
    const interval = isHighChange(lastPrice, lastFlushedPrice, threshold)
      ? highChangeMs
      : baselineMs;
    const fireTs = lastFlushTs + interval;
    // 仅在「尚无定时器」或「新目标更早」（窗口中途升级为剧烈变化）时安排/提前，绝不后延，防饿死。
    if (timer !== null && fireTs >= scheduledFireTs) return;
    if (timer !== null) clearTimer(timer);
    scheduledFireTs = fireTs;
    timer = setTimer(onTimer, Math.max(0, fireTs - now()));
  };

  const pushTrade = (trade: MarketTrade) => {
    trades.push(trade);
    setPriceCandidate(trade.price, trade.ts);
    marketTs = Math.max(marketTs ?? 0, trade.ts);
    hasData = true;
    schedule();
  };

  const pushTicker = (next: MarketTicker) => {
    ticker = next;
    setPriceCandidate(next.last, next.ts);
    marketTs = Math.max(marketTs ?? 0, next.ts);
    hasData = true;
    schedule();
  };

  const pushBook = (next: OrderBookSnapshot) => {
    book = next; // 盘口不贡献 lastPrice，仅推进面板 updated 时间
    marketTs = Math.max(marketTs ?? 0, next.ts);
    hasData = true;
    schedule();
  };

  const pushPrice = (price: number, ts: number) => {
    setPriceCandidate(price, ts);
    hasData = true;
    schedule();
  };

  const reset = () => {
    if (timer !== null) clearTimer(timer);
    timer = null;
    scheduledFireTs = Infinity;
    book = null;
    ticker = null;
    trades = [];
    lastPrice = null;
    priceTs = -1;
    marketTs = null;
    hasData = false;
    lastFlushedPrice = null; // 切换合约时连高变化基线一并清空，防上一合约缓存泄漏
    lastFlushTs = null;
  };

  const dispose = () => {
    disposed = true;
    if (timer !== null) clearTimer(timer);
    timer = null;
    scheduledFireTs = Infinity;
  };

  return { pushTrade, pushTicker, pushBook, pushPrice, reset, dispose };
}
