// 多模型训练批次 REST 客户端（经 /api/py/* 代理 → 后端 /api/weight-models/batches*）。
// 复用 api.ts 的 ApiRequestError 与同款错误解包（镜像 weightModelApi）。
import { ApiRequestError, toApiError } from "./api";
import { timeoutFor, withTimeout } from "./httpTimeout";
import type { FactorMember, TrainMethod } from "./factorTypes";
import type {
  WeightBatchDetail,
  WeightBatchStartResult,
  WeightBatchSummary,
} from "./weightBatchTypes";

const BASE = "/api/py";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  // D3：超时决策集中在 lib/httpTimeout.ts，接线留在原地——浏览器 fetch 无默认超时，
  // 「后端活着但不回」时按钮会静默冻结最长 5 分钟（standalone）或永远（dev）。
  const timeoutMs = timeoutFor(path);
  let res: Response;
  try {
    res = await fetch(`${BASE}${path}`, withTimeout({
      ...init,
      headers: { "Content-Type": "application/json", ...(init?.headers || {}) },
      cache: "no-store",
    }, timeoutMs));
  } catch (err) {
    throw toApiError(err, timeoutMs, path);   // 非超时错误原样上抛，绝不吞
  }
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

export interface WeightBatchCandidateBody {
  method: TrainMethod;
  config?: Record<string, unknown>;
  variantLabel?: string;
}

export interface WeightBatchStartBody {
  instId: string;
  bar: string;
  startTs: number;
  endTs: number;
  members: FactorMember[];
  config?: Record<string, unknown>;
  horizon?: number;
  candidates: WeightBatchCandidateBody[];
  factorSetId?: number;
  name?: string;
  origin?: "builder" | "model";
  maxBars?: number;
}

export const weightBatchApi = {
  start: (body: WeightBatchStartBody) =>
    request<WeightBatchStartResult>("/weight-models/batches", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  list: () => request<WeightBatchSummary[]>("/weight-models/batches"),
  get: (id: number) => request<WeightBatchDetail>(`/weight-models/batches/${id}`),
  select: (id: number, runId: number) =>
    request<WeightBatchDetail>(`/weight-models/batches/${id}/select`, {
      method: "POST",
      body: JSON.stringify({ runId }),
    }),
  cancel: (id: number) =>
    request<{ batchId: number; status: string }>(`/weight-models/batches/${id}/cancel`, {
      method: "POST",
    }),
  cancelCandidate: (id: number, runId: number) =>
    request<{ batchId: number; runId: number; status: string }>(
      `/weight-models/batches/${id}/candidates/${runId}/cancel`,
      { method: "POST" },
    ),
  remove: (id: number) => request<void>(`/weight-models/batches/${id}`, { method: "DELETE" }),
};
