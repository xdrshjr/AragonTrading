import { colorForIndex } from "./compare";
import type {
  Overlay,
  PaperBatchStartResult,
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
