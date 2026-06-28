// 因子层 REST 客户端（经 /api/py/* 代理 → 后端 /api/factors*、/api/factor-sets*）。
// 复用 api.ts 的 ApiRequestError 与同款错误解包；BASE 已是 /api/py（评审 C-P2-3）。
import { ApiRequestError } from "./api";
import type {
  Factor,
  FactorComputeResult,
  FactorDirection,
  FactorEvalResult,
  FactorLibraryItem,
  FactorMember,
  FactorMineResult,
  FactorScoreResult,
  FactorSet,
  FactorSource,
  TrainerInfo,
  TrainMetrics,
  TrainMethod,
  TrainResult,
} from "./factorTypes";

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

export interface FactorRange {
  instId: string;
  bar: string;
  startTs: number;
  endTs: number;
}

export const factorApi = {
  library: () => request<FactorLibraryItem[]>("/factors/library"),
  trainers: () => request<TrainerInfo[]>("/factors/trainers"),

  list: (filter?: {
    enabled?: boolean;
    category?: string;
    q?: string;
    source?: FactorSource;
    sort?: "rankic" | "name" | "created";
  }) => {
    const params = new URLSearchParams();
    if (filter?.enabled != null) params.set("enabled", String(filter.enabled));
    if (filter?.category) params.set("category", filter.category);
    if (filter?.q) params.set("q", filter.q);
    if (filter?.source) params.set("source", filter.source);
    if (filter?.sort) params.set("sort", filter.sort);
    const qs = params.toString();
    return request<Factor[]>(`/factors${qs ? `?${qs}` : ""}`);
  },
  create: (body: {
    factorKey: string;
    name: string;
    params?: Record<string, unknown>;
    direction?: FactorDirection;
    source?: Exclude<FactorSource, "builtin">;
  }) => request<Factor>("/factors", { method: "POST", body: JSON.stringify(body) }),
  update: (
    id: number,
    body: {
      name?: string;
      params?: Record<string, unknown>;
      direction?: FactorDirection;
      enabled?: boolean;
      notes?: string;
    },
  ) => request<Factor>(`/factors/${id}`, { method: "PUT", body: JSON.stringify(body) }),
  remove: (id: number) => request<void>(`/factors/${id}`, { method: "DELETE" }),

  compute: (body: FactorRange & { factorKey: string; params?: Record<string, unknown> }) =>
    request<FactorComputeResult>("/factors/compute", { method: "POST", body: JSON.stringify(body) }),
  evaluate: (
    body: FactorRange & {
      factorKey?: string;
      factorId?: number;
      params?: Record<string, unknown>;
      direction?: FactorDirection;
      horizons?: number[];
      quantiles?: number;
    },
  ) => request<FactorEvalResult>("/factors/evaluate", { method: "POST", body: JSON.stringify(body) }),
  mine: (
    body: FactorRange & { categories?: string[]; horizons?: number[]; topN?: number; persist?: boolean },
  ) => request<FactorMineResult>("/factors/mine", { method: "POST", body: JSON.stringify(body) }),

  sets: {
    list: (enabled?: boolean) =>
      request<FactorSet[]>(`/factor-sets${enabled != null ? `?enabled=${enabled}` : ""}`),
    get: (id: number) => request<FactorSet>(`/factor-sets/${id}`),
    create: (body: {
      name: string;
      members: FactorMember[];
      method: TrainMethod;
      modelJson?: Record<string, unknown>;
      metricsJson?: TrainMetrics | null;
      config?: Record<string, unknown>;
      enabled?: boolean;
    }) => request<FactorSet>("/factor-sets", { method: "POST", body: JSON.stringify(body) }),
    update: (
      id: number,
      body: {
        name?: string;
        members?: FactorMember[];
        method?: TrainMethod;
        modelJson?: Record<string, unknown>;
        metricsJson?: Record<string, unknown> | null;
        config?: Record<string, unknown>;
        enabled?: boolean;
      },
    ) => request<FactorSet>(`/factor-sets/${id}`, { method: "PUT", body: JSON.stringify(body) }),
    remove: (id: number) => request<void>(`/factor-sets/${id}`, { method: "DELETE" }),
    score: (id: number, body: FactorRange) =>
      request<FactorScoreResult>(`/factor-sets/${id}/score`, {
        method: "POST",
        body: JSON.stringify(body),
      }),
  },

  train: (
    body: FactorRange & {
      members: FactorMember[];
      method: TrainMethod;
      config?: Record<string, unknown>;
      horizon?: number;
    },
  ) => request<TrainResult>("/factor-sets/train", { method: "POST", body: JSON.stringify(body) }),

  scorePreview: (
    body: FactorRange & { members: FactorMember[]; thresholds?: Record<string, unknown> },
  ) =>
    request<FactorScoreResult>("/factor-sets/score-preview", {
      method: "POST",
      body: JSON.stringify(body),
    }),
};
