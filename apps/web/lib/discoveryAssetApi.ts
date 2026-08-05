// 成果资产台账 REST 客户端（经 /api/py/* 代理 → 后端 /api/discovery/assets/*）。
// 复用 discoveryApi.ts 的 buildQuery 与同款错误解包范式，不另造一套。

import { ApiRequestError } from "./api";
import { buildQuery } from "./discoveryApi";
import type {
  DiscoveryAssetCheckRow,
  DiscoveryAssetDetailRow,
  DiscoveryAssetQuery,
  DiscoveryAssetRow,
  DiscoveryAssetSummary,
} from "./discoveryAssetTypes";

const BASE = "/api/py";

/**
 * 「立即体检」是一个**同步**请求：取数 + 单资产墙钟超时，最长可能挂近一分钟。
 * 因此它单独用一个更长的超时，且**绝不重试**——重试只会撞守护的 409。
 */
export const CHECK_TIMEOUT_MS = 65_000;

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

export const discoveryAssetApi = {
  list: (query: DiscoveryAssetQuery = {}) =>
    request<{ items: DiscoveryAssetRow[]; total: number }>(
      `/discovery/assets${buildQuery(query as Record<string, string | number>)}`,
    ),

  summary: () => request<DiscoveryAssetSummary>("/discovery/assets/summary"),

  get: (assetId: number) =>
    request<DiscoveryAssetDetailRow>(`/discovery/assets/${assetId}`),

  checks: (assetId: number, params: { limit?: number; offset?: number } = {}) =>
    request<{ items: DiscoveryAssetCheckRow[]; total: number }>(
      `/discovery/assets/${assetId}/checks${buildQuery(params)}`,
    ),

  /** 同步重评一次；超时更长且不重试（见 CHECK_TIMEOUT_MS）。 */
  checkNow: (assetId: number) =>
    request<DiscoveryAssetCheckRow>(`/discovery/assets/${assetId}/check`, {
      method: "POST",
      signal: AbortSignal.timeout(CHECK_TIMEOUT_MS),
    }),

  archive: (assetId: number, archived: boolean) =>
    request<{ id: number; archived: boolean; archivedTs: number }>(
      `/discovery/assets/${assetId}/${archived ? "archive" : "unarchive"}`,
      { method: "POST" },
    ),

  patch: (assetId: number, note: string) =>
    request<DiscoveryAssetRow>(`/discovery/assets/${assetId}`, {
      method: "PATCH",
      body: JSON.stringify({ note }),
    }),

  promote: (assetId: number) =>
    request<{
      promoted: {
        kind: string;
        entityId: number;
        entityKind: string;
        reused: boolean;
        companionStrategyId?: number;
      };
    }>(`/discovery/assets/${assetId}/promote`, { method: "POST" }),

  /** 导出链接（浏览器直接下载，附件名由后端的 Content-Disposition 决定）。 */
  exportUrl: (query: DiscoveryAssetQuery & { ids?: string } = {}) =>
    `${BASE}/discovery/assets/export${buildQuery(query as Record<string, string | number>)}`,
};
