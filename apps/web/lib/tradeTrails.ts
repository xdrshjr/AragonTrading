import type {
  Trade,
  TradeDirection,
  TradeMarkerEvent,
  TradeTrailSegment,
} from "./types";

interface IndexedTrade {
  trade: Trade;
  index: number;
}

interface PairedTrail {
  segment: TradeTrailSegment;
  entryIndex: number;
  exitIndex: number;
}

const DASHED_LINE_STYLE = 2;

export function sortTradesForTrail(trades: Trade[]): Trade[] {
  return indexTrades(trades).map((item) => item.trade);
}

export function buildTradeTrailSegments(trades: Trade[]): TradeTrailSegment[] {
  return buildPairedTrails(trades).map((item) => item.segment);
}

/**
 * 因果序的只读导出（ai-trade-cost-truth-and-net-pnl §3.5 F-2）。
 *
 * 存在的唯一理由：往返的**腿费归集**必须与配对钉死**同一个**排序键，否则费用会串窗（P2-4）。
 * 由本模块导出而不是让调用方各自重排，是为了不产生第二份「因果序」的定义。
 */
export function causalOrderedTrades(trades: Trade[]): Trade[] {
  return causalOrderTrades(trades).map((item) => item.trade);
}

export function buildTradeMarkerEvents(trades: Trade[]): TradeMarkerEvent[] {
  const pairByIndex = new Map<number, PairedTrail>();
  for (const pair of buildPairedTrails(trades)) {
    pairByIndex.set(pair.entryIndex, pair);
    pairByIndex.set(pair.exitIndex, pair);
  }

  let hasOpen = false;
  return indexTrades(trades).map(({ trade, index }) => {
    const pair = pairByIndex.get(index);
    if (trade.side === "long" || trade.side === "short") {
      hasOpen = !pair;
      return {
        id: markerId(trade, index),
        trade,
        kind: trade.side === "long" ? "entry-long" : "entry-short",
        direction: trade.side,
        pairedSegmentId: pair?.segment.id,
      };
    }

    const event: TradeMarkerEvent = {
      id: markerId(trade, index),
      trade,
      kind: pair || hasOpen ? "close" : "orphan-close",
      pairedSegmentId: pair?.segment.id,
    };
    if (pair) hasOpen = false;
    return event;
  });
}

export function tradeToneForEvent(
  event: TradeMarkerEvent,
): "long" | "short" | "neutral" {
  if (event.kind === "entry-long") return "long";
  if (event.kind === "entry-short") return "short";
  if (event.trade.pnl > 0) return "long";
  if (event.trade.pnl < 0) return "short";
  return "neutral";
}

function buildPairedTrails(trades: Trade[]): PairedTrail[] {
  const pairs: PairedTrail[] = [];
  let open: IndexedTrade | null = null;

  for (const item of causalOrderTrades(trades)) {
    const { trade } = item;
    if (trade.side === "long" || trade.side === "short") {
      open = item;
      continue;
    }
    if (!open) continue;
    pairs.push({
      segment: segmentFor(open, item),
      entryIndex: open.index,
      exitIndex: item.index,
    });
    open = null;
  }

  return pairs;
}

function segmentFor(entry: IndexedTrade, exit: IndexedTrade): TradeTrailSegment {
  const direction = entry.trade.side as TradeDirection;
  const pnl = exit.trade.pnl;
  const isWin = pnl > 0;
  return {
    id: segmentId(entry, exit),
    direction,
    entry: entry.trade,
    exit: exit.trade,
    pnl,
    isWin,
    canRenderLine: entry.trade.ts < exit.trade.ts,
    lineColor: direction === "long" ? "var(--long)" : "var(--short)",
    lineStyle: isWin ? undefined : DASHED_LINE_STYLE,
  };
}

// 渲染顺序：按 ts 升序（lightweight-charts 的 setMarkers 要求 markers 按 time 升序，dev 构建有断言）。
function indexTrades(trades: Trade[]): IndexedTrade[] {
  return trades
    .map((trade, index) => ({ trade, index }))
    .sort((a, b) => {
      if (a.trade.ts !== b.trade.ts) return a.trade.ts - b.trade.ts;
      return tradeOrder(a) - tradeOrder(b);
    });
}

// 配对顺序：按**因果序**，绝不用 ts。AI 自动交易的 trade.ts 由两套时钟写入（K 线轮 = bar 开盘时刻 /
// 定时唤醒轮 = 墙钟），按 ts 排序会破坏真实的开→平交替，导致完整交易整笔丢弃或张冠李戴
// （诊断 ai-trade-stats-missing-round-trips）。整数组二选一判定：全员带 id（历史 DB 路径）→ 按 id
// 升序 = 落库序；否则（实时 WS 快照 / 追加）数组原序即到达序 = 因果序。不做逐元素回退，避免把 DB
// 主键空间与数组下标空间混进同一个比较器。配对与渲染天然解耦——PairedTrail 记的是原数组下标。
function causalOrderTrades(trades: Trade[]): IndexedTrade[] {
  const items = trades.map((trade, index) => ({ trade, index }));
  if (!items.every((item) => item.trade.id != null)) return items;
  return items.sort((a, b) => (a.trade.id as number) - (b.trade.id as number));
}

function tradeOrder(item: IndexedTrade): number {
  return item.trade.id ?? item.index;
}

function segmentId(entry: IndexedTrade, exit: IndexedTrade): string {
  const runId = entry.trade.runId ?? exit.trade.runId;
  const strategyId = strategyKey(entry.trade) ?? strategyKey(exit.trade);
  if (runId != null && entry.trade.id != null && exit.trade.id != null) {
    return [strategyId, runId, entry.trade.id, exit.trade.id].filter(Boolean).join("-");
  }
  return [
    strategyId,
    entry.trade.ts,
    entry.index,
    exit.trade.ts,
    exit.index,
  ].filter(Boolean).join("-");
}

function markerId(trade: Trade, index: number): string {
  const strategyId = strategyKey(trade);
  if (trade.runId != null && trade.id != null) {
    return [strategyId, trade.runId, trade.id].filter(Boolean).join("-");
  }
  return [strategyId, trade.ts, index, trade.side].filter(Boolean).join("-");
}

function strategyKey(trade: Trade): string | undefined {
  const strategyId = (trade as Trade & { strategyId?: number }).strategyId;
  return strategyId == null ? undefined : `s${strategyId}`;
}
