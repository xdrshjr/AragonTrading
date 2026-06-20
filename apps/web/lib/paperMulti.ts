import { colorForIndex } from "./compare";
import type {
  Overlay,
  PaperBatchStartResult,
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
