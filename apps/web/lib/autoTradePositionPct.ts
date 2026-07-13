import type { PaperPositionEvent } from "./types";
import type { LivePosition } from "./tradingTypes";

interface RuntimeMember {
  position: PaperPositionEvent | null;
  status?: { state?: string } | null;
  session?: { state?: string } | null;
}

export interface AutoTradePositionSources {
  aiMembers?: Record<string, RuntimeMember>;
  liveMembers?: Record<string, RuntimeMember>;
  single?: { instId: string; position: PaperPositionEvent | null };
}

/**
 * Return an automatic position snapshot only when it still exactly represents
 * the account-level OKX row. Manual or externally changed positions stay
 * unattributed instead of receiving a guessed percentage.
 */
export function autoTradePositionPct(
  live: LivePosition,
  sources: AutoTradePositionSources,
): number | null {
  const runtime = runtimePosition(live.instId, sources);
  if (!runtime || runtime.side === "flat") return null;

  const liveSize = Math.abs(live.pos ?? Number.NaN);
  const runtimeSize = runtime.size;
  const positionPct = runtime.positionPct;
  if (!Number.isFinite(liveSize) || liveSize <= 0) return null;
  if (!Number.isFinite(runtimeSize) || runtimeSize <= 0) return null;
  if (positionPct == null || !Number.isFinite(positionPct)) return null;
  if (positionPct < 0 || positionPct > 1) return null;
  if (liveDirection(live) !== runtime.side) return null;

  const tolerance = Math.max(1e-9, liveSize * 1e-6);
  return Math.abs(liveSize - runtimeSize) <= tolerance ? positionPct : null;
}

function runtimePosition(
  instId: string,
  sources: AutoTradePositionSources,
): PaperPositionEvent | null {
  return activeMemberPosition(sources.aiMembers?.[instId])
    ?? activeMemberPosition(sources.liveMembers?.[instId])
    ?? (sources.single?.instId === instId ? sources.single.position : null);
}

function activeMemberPosition(member: RuntimeMember | undefined): PaperPositionEvent | null {
  const state = member?.status?.state ?? member?.session?.state;
  return state === "stopped" ? null : (member?.position ?? null);
}

function liveDirection(live: LivePosition): "long" | "short" | null {
  if (live.posSide === "long" || live.posSide === "short") return live.posSide;
  if (live.posSide !== "net" || live.pos == null || !Number.isFinite(live.pos)) return null;
  if ((live.pos ?? 0) > 0) return "long";
  if ((live.pos ?? 0) < 0) return "short";
  return null;
}
