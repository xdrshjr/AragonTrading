// 市场 WS 帧路由（`/api/ws/market` 的唯一消费判据）。
//
// 这里存在的理由是一次真实事故：`okx-link-resilience` 给这条流加了一支链路状态帧
// `{"type":"link"}`（ws_market.py 的 `on_stream`，每次建流必发），其正确性前提写在后端注释里
// ——「前端遇到未知 type 本就忽略」。而当时的消费端是 else 兜底：凡未匹配的帧一律当 K 线。
// link 帧遂被存成「正在形成的 K 线」，以 `ts === undefined` 进入图表数据；lightweight-charts
// 判定 time 的判据是「既不是 number 也不是 string 就当 BusinessDay」，于是去读 `undefined.year`
// 抛 TypeError，而该异常发生在 effect 里、其上没有任何 error boundary，整棵 React 树被卸载——
// 打包桌面端表现为开屏白屏，而外壳日志全绿。
// 详见 docs/diagnoses/desktop-white-screen-market-ws-link-frame/analysis.md
//
// 因此本模块的两条铁律：
//   ① 路由是**白名单**：只有显式 case 的 type 会被消费，其余忽略，让后端的「加法事件类型」
//      真正是加法。default 分支的 `never` 赋值使「联合类型新增一支却没在此路由」成为编译错误。
//   ② 进图表的 K 线必须先过 `isUsableCandle`：判据来自图表库真正的要求（时间必须是有限数），
//      而不是来自「后端应该会给对」的信任。

import type { Candle, MarketTicker, MarketTrade, MarketWsEvent, OrderBookSnapshot } from "./types";

/**
 * 路由的落点。全部由调用方注入，本模块不碰 React、不碰节流器实现，故可直接单测。
 */
export interface MarketFrameSinks {
  /** 传 null 表示「本帧证明行情在流，清掉故障横幅」。 */
  setError: (message: string | null) => void;
  advanceMarketTs: (ts: number) => void;
  pushTrade: (trade: MarketTrade) => void;
  pushTicker: (ticker: MarketTicker) => void;
  pushBook: (book: OrderBookSnapshot) => void;
  pushPrice: (price: number, ts: number) => void;
  /** 读最新的行情时钟（ref 语义：必须取当下值，不能取渲染时快照）。 */
  lastMarketTs: () => number | null;
  /** 是否把公共 K 线应用到图表（回测区间 / 模拟盘自带 K 线时为 false）。 */
  applyPublicCandle: boolean;
  onConfirmedCandle: (candle: Candle) => void;
  onFormingCandle: (candle: Candle | null) => void;
}

/**
 * 这份 payload 能否安全地当作 K 线送进图表。
 *
 * `ts` 必须是有限数——这正是白屏事故里缺失的那一条判据；`c` 参与最新价门控，同样要求有限数。
 * 三个 K 线入口（市场流 / 模拟盘流 / 自动交易流）共用它，避免只在出事的那一个入口设防。
 */
export function isUsableCandle(data: unknown): data is Candle {
  if (data === null || typeof data !== "object") return false;
  const candle = data as Partial<Candle>;
  return Number.isFinite(candle.ts) && Number.isFinite(candle.c);
}

/**
 * 把一帧市场 WS 事件路由到对应落点。未建模的 type 一律忽略（含不清故障横幅——
 * 一帧链路状态到达并不代表行情在流）。
 */
export function routeMarketFrame(event: MarketWsEvent, sinks: MarketFrameSinks): void {
  switch (event.type) {
    case "error":
      sinks.setError(event.data.message); // 故障提示保持即时，不进节流
      return;
    case "link":
      // 链路状态帧目前只在后端留痕、供诊断；顶栏链路芯片走 REST /api/okx/connectivity。
      return;
    case "trade":
      sinks.setError(null);
      sinks.advanceMarketTs(event.data.ts);
      sinks.pushTrade(event.data);
      return;
    case "ticker":
      sinks.setError(null);
      sinks.advanceMarketTs(event.data.ts);
      sinks.pushTicker(event.data);
      return;
    case "book":
      sinks.setError(null);
      sinks.advanceMarketTs(event.data.ts);
      sinks.pushBook(event.data);
      return;
    case "candle":
      sinks.setError(null);
      routeCandleFrame(event.data, sinks);
      return;
    default: {
      // 编译期穷尽性：给 MarketWsEvent 加一支却忘了在此路由，下面这行赋值会报错。
      // 运行期：wire 上出现尚未建模的 type，静默忽略（铁律 ①）。
      const unrouted: never = event;
      void unrouted;
      return;
    }
  }
}

/**
 * K 线帧：confirm/forming 维持即时（右侧 K 线需即时落定）；其价格经时钟门控并入节流缓冲，
 * 使 flush 成为 lastPrice 的唯一写入口，避免与节流流互相打架（顶部最新价同步降到 1~2Hz）。
 */
function routeCandleFrame(data: Candle, sinks: MarketFrameSinks): void {
  if (!isUsableCandle(data)) return; // 形状不合格的 K 线只能丢弃——送进图表就是白屏
  if ((sinks.lastMarketTs() ?? 0) <= data.ts) sinks.pushPrice(data.c, data.ts);
  if (!sinks.applyPublicCandle) return;
  if (data.confirm === true) {
    sinks.onConfirmedCandle(data);
    sinks.onFormingCandle(null);
  } else {
    sinks.onFormingCandle(data);
  }
}
