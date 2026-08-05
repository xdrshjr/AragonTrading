// 挖掘层 REST 客户端（经 /api/py/* 代理 → 后端 /api/discovery/*）。
// 复用 api.ts 的 ApiRequestError 与同款错误解包（与 factorApi.ts 同范式）。
import { ApiRequestError } from "./api";
import type {
  DiscoveryCandidateDetail,
  DiscoveryCandidateRow,
  DiscoveryDefaults,
  DiscoveryRunDetail,
  DiscoveryRunSummary,
  DiscoveryStartBody,
  DiscoveryVocabulary,
} from "./discoveryTypes";
import { ReconnectingWS, type WsEvent } from "./ws";

const BASE = "/api/py";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers || {}) },
    cache: "no-store",
  });
  if (!res.ok) {
    let code = String(res.status);
    let message = res.statusText;
    try {
      const body = await res.json();
      if (body?.error) {
        code = body.error.code ?? code;
        message = body.error.message ?? message;
      }
    } catch {
      /* ignore parse error */
    }
    throw new ApiRequestError(code, message, res.status);
  }
  return res.status === 204 ? (undefined as T) : ((await res.json()) as T);
}

/** 查询串拼装：跳过空值，保证 `?a=1&b=2` 稳定顺序（便于缓存与测试）。 */
export function buildQuery(params: Record<string, string | number | undefined | null>): string {
  const parts = Object.entries(params)
    .filter(([, value]) => value !== undefined && value !== null && value !== "")
    .map(([key, value]) => `${encodeURIComponent(key)}=${encodeURIComponent(String(value))}`);
  return parts.length ? `?${parts.join("&")}` : "";
}

export interface CandidateQuery {
  verdict?: string;
  mechanism?: string;
  reason?: string;
  sort?: string;
  limit?: number;
  offset?: number;
}

export const discoveryApi = {
  defaults: () => request<DiscoveryDefaults>("/discovery/config/defaults"),

  vocabulary: () => request<DiscoveryVocabulary>("/discovery/vocabulary"),

  start: (body: DiscoveryStartBody) =>
    request<{ runId: number; status: string; config: Record<string, unknown>; startedTs: number }>(
      "/discovery/runs",
      { method: "POST", body: JSON.stringify(body) },
    ),

  listRuns: (params: { status?: string; limit?: number; offset?: number } = {}) =>
    request<{ items: DiscoveryRunSummary[]; total: number }>(
      `/discovery/runs${buildQuery(params)}`,
    ),

  getRun: (runId: number) => request<DiscoveryRunDetail>(`/discovery/runs/${runId}`),

  pause: (runId: number) =>
    request<{ runId: number; status: string }>(`/discovery/runs/${runId}/pause`, {
      method: "POST",
    }),

  resume: (runId: number) =>
    request<{ runId: number; status: string }>(`/discovery/runs/${runId}/resume`, {
      method: "POST",
    }),

  stop: (runId: number) =>
    request<{ runId: number; status: string }>(`/discovery/runs/${runId}/stop`, {
      method: "POST",
    }),

  remove: (runId: number) =>
    request<void>(`/discovery/runs/${runId}`, { method: "DELETE" }),

  listCandidates: (runId: number, query: CandidateQuery = {}) =>
    request<{ items: DiscoveryCandidateRow[]; total: number }>(
      `/discovery/runs/${runId}/candidates${buildQuery(query as Record<string, string | number>)}`,
    ),

  getCandidate: (candidateId: number) =>
    request<DiscoveryCandidateDetail>(`/discovery/candidates/${candidateId}`),

  promote: (candidateId: number) =>
    request<{ promoted: { kind: string; entityId: number; entityKind: string; reused: boolean } }>(
      `/discovery/candidates/${candidateId}/promote`,
      { method: "POST" },
    ),

  backtest: (candidateId: number, startTs: number, endTs: number) =>
    request<{
      candidateId: number;
      instId: string;
      bar: string;
      metrics: Record<string, unknown>;
      trades: number;
    }>(`/discovery/candidates/${candidateId}/backtest`, {
      method: "POST",
      body: JSON.stringify({ startTs, endTs }),
    }),
};

/** 一 run 一连接的挖掘实时流（镜像 aiTradeWS / weightTrainWs）。 */
export function discoveryWS(
  runId: number,
  onEvent: (event: WsEvent) => void,
  onState?: (open: boolean) => void,
): ReconnectingWS {
  return new ReconnectingWS(`/api/ws/discovery/${runId}`, onEvent, onState);
}
