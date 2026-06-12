// REST 客户端：封装 /api/py/*（经 Next rewrite 代理到 Python 后端）(spec §8.2 / §9.1)。
import type {
  Candle,
  EquityPoint,
  Health,
  Instrument,
  Metrics,
  Run,
  Strategy,
  Trade,
} from "./types";

const BASE = "/api/py";

export class ApiRequestError extends Error {
  code: string;
  status: number;
  constructor(code: string, message: string, status: number) {
    super(message);
    this.code = code;
    this.status = status;
  }
}

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
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export const api = {
  health: () => request<Health>("/health"),

  instruments: (instType = "SWAP") =>
    request<Instrument[]>(`/instruments?instType=${encodeURIComponent(instType)}`),

  candles: (instId: string, bar: string, limit = 300, before?: number) => {
    const params = new URLSearchParams({ instId, bar, limit: String(limit) });
    if (before) params.set("before", String(before));
    return request<Candle[]>(`/candles?${params.toString()}`);
  },

  strategies: () => request<Strategy[]>("/strategies"),
  strategy: (id: number) => request<Strategy>(`/strategies/${id}`),
  createStrategy: (body: { templateKey: string; name: string; params: Record<string, unknown> }) =>
    request<Strategy>("/strategies", { method: "POST", body: JSON.stringify(body) }),
  updateStrategy: (
    id: number,
    body: { name?: string; params?: Record<string, unknown>; enabled?: boolean },
  ) => request<Strategy>(`/strategies/${id}`, { method: "PUT", body: JSON.stringify(body) }),
  deleteStrategy: (id: number) => request<void>(`/strategies/${id}`, { method: "DELETE" }),

  backtest: (
    id: number,
    body: {
      instId: string;
      bar: string;
      limit?: number;
      initialCapital?: number;
      fees?: number;
      slippage?: number;
    },
  ) =>
    request<{ runId: number; metrics: Metrics }>(`/strategies/${id}/backtest`, {
      method: "POST",
      body: JSON.stringify(body),
    }),

  run: (runId: number) => request<Run>(`/runs/${runId}`),
  trades: (runId: number, limit = 500) => request<Trade[]>(`/runs/${runId}/trades?limit=${limit}`),
  equity: (runId: number) => request<EquityPoint[]>(`/runs/${runId}/equity`),

  paperStart: (body: { strategyId: number; instId: string; bar: string; initialCapital?: number }) =>
    request<{ sessionId: string; runId: number }>("/paper/start", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  paperStop: (sessionId: string) =>
    request<Run>(`/paper/${sessionId}/stop`, { method: "POST" }),
  paperSessions: () => request<unknown[]>("/paper/sessions"),
};
