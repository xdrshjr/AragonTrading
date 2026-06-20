import { colorForIndex } from "./compare";
import { deriveOpenPosition } from "./position";
import type {
  Overlay,
  PaperBatchStartResult,
  PaperPositionEvent,
  PaperSessionRow,
  PaperStrategySession,
  StrategyEquityPoint,
  StrategyTrade,
} from "./types";

export const FALLBACK_MAX_PAPER_SESSIONS = 3;

export function colorForPaperStrategy(index: number): string {
  return colorForIndex(index);
}

export function resolveMaxPaperSessions(maxPaperSessions?: number): number {
  return maxPaperSessions ?? FALLBACK_MAX_PAPER_SESSIONS;
}

export function sessionsFromBatch(
  result: PaperBatchStartResult,
): Record<number, PaperStrategySession> {
  const entries = result.sessions.map((session, index) => [
    session.strategyId,
    {
      strategyId: session.strategyId,
      strategyName: session.name,
      sessionId: session.sessionId,
      runId: session.runId,
      color: colorForPaperStrategy(index),
      state: session.state,
    },
  ]);
  return Object.fromEntries(entries);
}

// 挂载即对账：把后端 GET /api/paper/sessions 拉回的真实会话映射成前端运行期句柄。
// 只保留 state == "running"（list_sessions 会带回已 stopped 的残留行，rehydrate 它们会复活死会话并误锁选择）；
// 后端行无 strategyName(单条会话)/color，名称按 strategyId 从 namesById 解析、颜色按序号配色（与 sessionsFromBatch 同口径）。
// groupId：所有 running 行同属一个非空 group 时回填以支持批量停止；否则置 null，走单条停止兜底。
export function sessionsFromReconcile(
  rows: PaperSessionRow[],
  namesById: Record<number, string>,
): { sessions: Record<number, PaperStrategySession>; groupId: string | null } {
  const running = rows.filter((row) => row.state === "running");
  const entries = running.map((row, index) => [
    row.strategyId,
    {
      strategyId: row.strategyId,
      strategyName: namesById[row.strategyId] ?? row.strategyName ?? `Strategy ${row.strategyId}`,
      sessionId: row.sessionId,
      runId: row.runId,
      color: colorForPaperStrategy(index),
      state: "running" as const,
    },
  ]);
  const sessions = Object.fromEntries(entries) as Record<number, PaperStrategySession>;
  const groupIds = new Set(running.map((row) => row.groupId));
  const groupId = groupIds.size === 1 && !groupIds.has(null) ? running[0].groupId : null;
  return { sessions, groupId };
}

export function mergePaperTrade(
  prev: Record<number, StrategyTrade[]>,
  trade: StrategyTrade,
): Record<number, StrategyTrade[]> {
  const current = prev[trade.strategyId] ?? [];
  return { ...prev, [trade.strategyId]: [...current, trade] };
}

export function mergePaperEquity(
  prev: Record<number, StrategyEquityPoint[]>,
  point: StrategyEquityPoint,
): Record<number, StrategyEquityPoint[]> {
  const byTs = new Map<number, StrategyEquityPoint>();
  for (const item of prev[point.strategyId] ?? []) byTs.set(item.ts, item);
  byTs.set(point.ts, point);
  const next = Array.from(byTs.values()).sort((a, b) => a.ts - b.ts);
  return { ...prev, [point.strategyId]: next };
}

export function selectFirstAvailablePaperStrategy(
  activeId: number | null,
  sessions: Record<number, PaperStrategySession>,
): number | null {
  const ids = Object.keys(sessions).map(Number);
  if (activeId != null && ids.includes(activeId)) return activeId;
  return ids[0] ?? null;
}

export function visiblePaperTrades(
  tradesByStrategyId: Record<number, StrategyTrade[]>,
  visibleIds: number[],
): StrategyTrade[] {
  return visibleIds.flatMap((id) => tradesByStrategyId[id] ?? []);
}

export function paperSessionsToOverlays(
  sessions: Record<number, PaperStrategySession>,
  equityByStrategyId: Record<number, StrategyEquityPoint[]>,
  visibleIds: number[],
): Overlay[] {
  const overlays: Overlay[] = [];
  for (const id of visibleIds) {
    const session = sessions[id];
    if (!session) continue;
    overlays.push({
      id,
      name: session.strategyName,
      color: session.color,
      equity: equityByStrategyId[id] ?? [],
    });
  }
  return overlays;
}

// 按策略「当前持仓 + 资金」一行的规范化形态（Fix B）。持仓字段统一来源：后端权威
// PaperPositionEvent 或前端 deriveOpenPosition，二者归一到同一结构供列表渲染。
export interface PaperRowPosition {
  side: "long" | "short";
  size: number;
  avgEntry: number;
  markPrice: number | null;
  unrealizedPnl: number | null;
  unrealizedPct: number | null;
}

export interface PaperPositionRow {
  strategyId: number;
  strategyName: string;
  color: string;
  position: PaperRowPosition | null; // null = 当前无持仓（真平仓 / 权威报平）
  equity: number | null; // 该策略当前净值/资金（paperEquityByStrategyId 末点）
}

const POSITION_EPS = 1e-9;

function positionFromEvent(event: PaperPositionEvent): PaperRowPosition | null {
  if (event.side === "flat" || Math.abs(event.size) <= POSITION_EPS) return null;
  const size = Math.abs(event.size);
  const denom = event.avgEntry * size;
  return {
    side: event.side,
    size,
    avgEntry: event.avgEntry,
    markPrice: event.markPrice,
    unrealizedPnl: event.unrealizedPnl,
    unrealizedPct: denom > 0 ? event.unrealizedPnl / denom : null,
  };
}

// Fix A 的数据源规则：每行持仓优先取后端权威 paperPositionsByStrategyId[id]
// （自带各策略自身 markPrice，跨合约天然正确，含权威报平 null）；仅当该策略尚无任何
// position/snapshot 事件（值为 undefined，即首个事件前的窗口）才回退 deriveOpenPosition。
function resolveRowPosition(
  authoritative: PaperPositionEvent | null | undefined,
  trades: StrategyTrade[],
  markPrice: number | null,
): PaperRowPosition | null {
  if (authoritative !== undefined) {
    return authoritative ? positionFromEvent(authoritative) : null;
  }
  const derived = deriveOpenPosition(trades, markPrice);
  if (!derived) return null;
  return {
    side: derived.side,
    size: derived.size,
    avgEntry: derived.avgEntry,
    markPrice: derived.markPrice,
    unrealizedPnl: derived.unrealizedPnl,
    unrealizedPct: derived.unrealizedPct,
  };
}

// 把运行中各策略汇总成「按策略持仓 + 资金」行列表（Fix B 主入口）。
// markPrice 仅用于首事件前回退路径（deriveOpenPosition）；权威路径自带各策略 markPrice。
export function buildPaperPositionRows(
  strategies: PaperStrategySession[],
  positionsByStrategyId: Record<number, PaperPositionEvent | null>,
  tradesByStrategyId: Record<number, StrategyTrade[]>,
  equityByStrategyId: Record<number, StrategyEquityPoint[]>,
  markPrice: number | null,
): PaperPositionRow[] {
  return strategies.map((session) => {
    const id = session.strategyId;
    const equity = equityByStrategyId[id];
    return {
      strategyId: id,
      strategyName: session.strategyName,
      color: session.color,
      position: resolveRowPosition(
        positionsByStrategyId[id],
        tradesByStrategyId[id] ?? [],
        markPrice,
      ),
      equity: equity?.length ? equity[equity.length - 1].equity : null,
    };
  });
}
