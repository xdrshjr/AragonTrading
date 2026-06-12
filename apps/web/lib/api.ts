// REST 客户端：封装 /api/py/*（经 Next rewrite 代理到 Python 后端）(spec §8.2 / §9.1)。
import type {
  Candle,
  CompareResult,
  DataSource,
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

// 内部变体：同 request 的错误处理，但同时回传原始 Response 以便读取响应头（F2）。
async function rawRequest<T>(
  path: string,
  init?: RequestInit,
): Promise<{ data: T; res: Response }> {
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
  const data = res.status === 204 ? (undefined as T) : ((await res.json()) as T);
  return { data, res };
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  return (await rawRequest<T>(path, init)).data;
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

  // 主历史拉取：同 candles，但读响应头 X-Aragon-Data-Source 暴露数据来源（F2）。
  // 经 /api/py/* 同源代理（Next rewrite 透传上游头），故无需 CORS expose 即可读到。
  candlesWithMeta: async (
    instId: string,
    bar: string,
    limit = 300,
    before?: number,
  ): Promise<{ candles: Candle[]; source: DataSource }> => {
    const params = new URLSearchParams({ instId, bar, limit: String(limit) });
    if (before) params.set("before", String(before));
    const { data, res } = await rawRequest<Candle[]>(`/candles?${params.toString()}`);
    const source: DataSource = res.headers.get("X-Aragon-Data-Source") === "cache" ? "cache" : "live";
    return { candles: data, source };
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

  // 多策略对比（F3）：一次回测多个策略，返回含归一化前净值的榜单
  compare: (body: {
    strategyIds: number[];
    instId: string;
    bar: string;
    limit?: number;
    initialCapital?: number;
    fees?: number;
    slippage?: number;
    leverage?: number;
  }) =>
    request<CompareResult>("/strategies/compare", {
      method: "POST",
      body: JSON.stringify(body),
    }),

  run: (runId: number) => request<Run>(`/runs/${runId}`),
  trades: (runId: number, limit = 500) => request<Trade[]>(`/runs/${runId}/trades?limit=${limit}`),
  equity: (runId: number) => request<EquityPoint[]>(`/runs/${runId}/equity`),

  // 运行历史列表（F4）
  runsList: (filter?: {
    strategyId?: number;
    instId?: string;
    mode?: string;
    limit?: number;
    offset?: number;
  }) => {
    const params = new URLSearchParams();
    if (filter?.strategyId != null) params.set("strategyId", String(filter.strategyId));
    if (filter?.instId) params.set("instId", filter.instId);
    if (filter?.mode) params.set("mode", filter.mode);
    if (filter?.limit != null) params.set("limit", String(filter.limit));
    if (filter?.offset != null) params.set("offset", String(filter.offset));
    const qs = params.toString();
    return request<Run[]>(`/runs${qs ? `?${qs}` : ""}`);
  },

  paperStart: (body: {
    strategyId: number;
    instId: string;
    bar: string;
    initialCapital?: number;
    source?: "live" | "replay";
    speed?: number;
    replayBars?: number;
  }) =>
    request<{ sessionId: string; runId: number }>("/paper/start", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  paperStop: (sessionId: string) =>
    request<Run>(`/paper/${sessionId}/stop`, { method: "POST" }),
  paperSessions: () => request<unknown[]>("/paper/sessions"),
};
