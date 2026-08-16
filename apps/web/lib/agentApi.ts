// Agent REST 客户端（经 /api/py/* 代理 → 后端 /api/agent/*）。绝不携带 / 返回密钥（R3）。
import { ApiRequestError, toApiError } from "./api";
import { timeoutFor, withTimeout } from "./httpTimeout";
import type {
  AgentMessage,
  AgentProvider,
  AgentTool,
  AgentToolsResponse,
  ProviderDraft,
  ProviderTestResult,
  ProvidersListResponse,
} from "./agentTypes";

const BASE = "/api/py";

export interface AgentConfig {
  configured: boolean;
  provider: string;
  model: string;
}

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

export const agentApi = {
  // 是否已配置密钥 + 当前模型（WS ready 之外的 REST 兜底，可用于 SSR/刷新）。
  config: () => request<AgentConfig>("/agent/config"),
  messages: (conversationId: string, limit = 200) =>
    request<AgentMessage[]>(
      `/agent/conversations/${encodeURIComponent(conversationId)}/messages?limit=${limit}`,
    ),
  // 清空会话（"新对话"）。
  clear: (conversationId: string) =>
    request<void>(`/agent/conversations/${encodeURIComponent(conversationId)}`, {
      method: "DELETE",
    }),

  // AI 模型服务商管理（设置中心）。请求体仅 camelCase，apiKey 留空 = 保留原 key（R-Sec）。
  providers: {
    list: () => request<ProvidersListResponse>("/agent/providers"),
    create: (draft: ProviderDraft) =>
      request<AgentProvider>("/agent/providers", {
        method: "POST",
        body: JSON.stringify(draft),
      }),
    update: (id: number, draft: Partial<ProviderDraft>) =>
      request<AgentProvider>(`/agent/providers/${id}`, {
        method: "PUT",
        body: JSON.stringify(draft),
      }),
    remove: (id: number) =>
      request<void>(`/agent/providers/${id}`, { method: "DELETE" }),
    setDefault: (id: number) =>
      request<ProvidersListResponse>(`/agent/providers/${id}/default`, { method: "POST" }),
    // 测存量服务连通性（用库内 key）。
    test: (id: number) =>
      request<ProviderTestResult>(`/agent/providers/${id}/test`, { method: "POST" }),
    // 测草稿连通性（首存前；须带显式 apiKey）。
    testDraft: (draft: ProviderDraft) =>
      request<ProviderTestResult>("/agent/providers/test", {
        method: "POST",
        body: JSON.stringify(draft),
      }),
  },

  // Agent 工具管理（设置中心）：查看 + 逐项 / 批量开关。工具清单只来自后端注册表（C6）。
  tools: {
    list: () => request<AgentToolsResponse>("/agent/tools"),
    setEnabled: (name: string, enabled: boolean) =>
      request<AgentTool>(`/agent/tools/${encodeURIComponent(name)}`, {
        method: "PUT",
        body: JSON.stringify({ enabled }),
      }),
    setAll: (enabled: boolean) =>
      request<AgentToolsResponse>("/agent/tools/bulk", {
        method: "POST",
        body: JSON.stringify({ enabled }),
      }),
  },
};
