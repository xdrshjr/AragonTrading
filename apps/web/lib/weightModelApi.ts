// 权重模型 REST 客户端（经 /api/py/* 代理 → 后端 /api/weight-models*）。
// 复用 api.ts 的 ApiRequestError 与同款错误解包（与 factorApi 同范式）。
import { ApiRequestError } from "./api";
import type { FactorMember, TrainMethod } from "./factorTypes";
import type { WeightBacktestResult, WeightModel, WeightTrainRun } from "./weightModelTypes";

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

export interface WeightModelCreateBody {
  name: string;
  instId: string;
  bar: string;
  members: FactorMember[];
  method: TrainMethod;
  config?: Record<string, unknown>;
  horizon?: number;
  notes?: string;
  factorSetId?: number;
  enabled?: boolean;
}

export interface WeightModelUpdateBody {
  name?: string;
  instId?: string;
  bar?: string;
  members?: FactorMember[];
  method?: TrainMethod;
  config?: Record<string, unknown>;
  horizon?: number;
  notes?: string;
  enabled?: boolean;
}

export interface WeightTrainStartBody {
  instId: string;
  bar: string;
  startTs: number;
  endTs: number;
  members: FactorMember[];
  method: TrainMethod;
  config?: Record<string, unknown>;
  horizon?: number;
  modelId?: number;
  factorSetId?: number;
  name?: string;
  maxBars?: number;
}

export interface WeightTrainStartResult {
  jobId: string;
  runId: number;
  modelId: number;
}

// 一键回测入参（全部可选）：区间缺省回落训练区间；成本缺省回落后端 settings；maxBars 超限静默钳制。
export interface WeightBacktestBody {
  startTs?: number;
  endTs?: number;
  initialCapital?: number;
  fees?: number;
  slippage?: number;
  leverage?: number;
  maxBars?: number;
}

export const weightModelApi = {
  list: (enabled?: boolean) =>
    request<WeightModel[]>(`/weight-models${enabled != null ? `?enabled=${enabled}` : ""}`),
  get: (id: number) => request<WeightModel>(`/weight-models/${id}`),
  create: (body: WeightModelCreateBody) =>
    request<WeightModel>("/weight-models", { method: "POST", body: JSON.stringify(body) }),
  update: (id: number, body: WeightModelUpdateBody) =>
    request<WeightModel>(`/weight-models/${id}`, { method: "PUT", body: JSON.stringify(body) }),
  remove: (id: number) => request<void>(`/weight-models/${id}`, { method: "DELETE" }),

  runs: (id: number, opts?: { limit?: number; status?: string }) => {
    const params = new URLSearchParams();
    if (opts?.limit != null) params.set("limit", String(opts.limit));
    if (opts?.status) params.set("status", opts.status);
    const qs = params.toString();
    return request<WeightTrainRun[]>(`/weight-models/${id}/runs${qs ? `?${qs}` : ""}`);
  },
  run: (id: number, runId: number) =>
    request<WeightTrainRun>(`/weight-models/${id}/runs/${runId}`),
  removeRun: (id: number, runId: number) =>
    request<void>(`/weight-models/${id}/runs/${runId}`, { method: "DELETE" }),

  backtest: (modelId: number, runId: number, body?: WeightBacktestBody) =>
    request<WeightBacktestResult>(`/weight-models/${modelId}/runs/${runId}/backtest`, {
      method: "POST",
      body: JSON.stringify(body ?? {}),
    }),

  train: (body: WeightTrainStartBody) =>
    request<WeightTrainStartResult>("/weight-models/train", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  cancel: (jobId: string) =>
    request<{ jobId: string; status: string }>(`/weight-models/train/${jobId}`, {
      method: "DELETE",
    }),
};
