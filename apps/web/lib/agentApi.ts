// Agent REST 客户端（经 /api/py/* 代理 → 后端 /api/agent/*）。绝不携带 / 返回密钥（R3）。
import { ApiRequestError } from "./api";
import type { AgentMessage } from "./agentTypes";

const BASE = "/api/py";

export interface AgentConfig {
  configured: boolean;
  provider: string;
  model: string;
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
};
