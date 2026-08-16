// workspace 偏好 REST 客户端（watchlist-auto-trade-persistence §3，经 /api/py/* 代理 → 后端
// /api/workspace/watchlist）。错误体解析复用 autoTradeSettingsApi 风格的 request 助手。
import { ApiRequestError, toApiError } from "./api";
import { timeoutFor, withTimeout } from "./httpTimeout";
import type { WorkspaceWatchlistIn, WorkspaceWatchlistOut } from "./workspaceTypes";

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

export const workspaceApi = {
  // 读后端持久化的 workspace 偏好（present:false 表示尚无记录 → 前端 seed，§2.2）。
  get: () => request<WorkspaceWatchlistOut>("/workspace/watchlist"),
  // 整文档 upsert workspace 偏好（写穿为 fire-and-forget，响应不回写 store，P1-B）。
  // keepalive 供关窗 flush 在文档卸载期不被取消（§2.2 / P2-1）。
  update: (prefs: WorkspaceWatchlistIn, opts?: { keepalive?: boolean }) =>
    request<WorkspaceWatchlistOut>("/workspace/watchlist", {
      method: "PUT",
      body: JSON.stringify(prefs),
      keepalive: opts?.keepalive,
    }),
};
