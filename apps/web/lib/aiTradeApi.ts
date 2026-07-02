// AI 自动交易 REST + WS 客户端（镜像 liveAutoApi.ts：本地 request + ReconnectingWS，经 Next 同源代理 /api/py/*）。
import { ApiRequestError } from "./api";
import { ReconnectingWS, type WsEvent } from "./ws";
import type {
  AiTradeStartReq,
  AiTradeStartResult,
  AiTradeStatusResponse,
  AiTradeStopResult,
} from "./aiTradeTypes";

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
      code = body?.error?.code ?? code;
      message = body?.error?.message ?? message;
    } catch {
      // 非 JSON 错误体：保留状态码文案
    }
    throw new ApiRequestError(code, message, res.status);
  }
  return res.status === 204 ? (undefined as T) : ((await res.json()) as T);
}

export const aiTradeApi = {
  start: (req: AiTradeStartReq) =>
    request<AiTradeStartResult>("/ai-trade/start", {
      method: "POST",
      body: JSON.stringify(req),
    }),
  stop: (sessionId: string) =>
    request<AiTradeStopResult>(`/ai-trade/${sessionId}/stop`, { method: "POST" }),
  status: () => request<AiTradeStatusResponse>("/ai-trade/status"),
};

/** 打开一条 AI 自动交易会话 WS（事件协议与 /ws/live-auto 同信封 {type,data}，另加 decision/summary）。 */
export function aiTradeWS(
  sessionId: string,
  onEvent: (e: WsEvent) => void,
  onState?: (open: boolean) => void,
): ReconnectingWS {
  return new ReconnectingWS(`/api/ws/ai-trade/${sessionId}`, onEvent, onState);
}
