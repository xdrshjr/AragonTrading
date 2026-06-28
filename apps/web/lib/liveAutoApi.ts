// 实时自动交易 REST 客户端（经 /api/py/* 代理 → 后端 /api/live-auto/*）。
// 复用 tradingApi 的 request 风格与错误体解析；事件协议同 paper（复用 ws.ts reconnecting 客户端）。
import { ApiRequestError } from "./api";
import { ReconnectingWS, type WsEvent } from "./ws";
import type {
  LiveAutoStartReq,
  LiveAutoStartResult,
  LiveAutoStatusResponse,
  LiveAutoStopResult,
} from "./liveAutoTypes";

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

export const liveAutoApi = {
  // 启动自动交易（受 LIVE_AUTO_TRADING_ENABLED 与 LIVE_TRADING_ENABLED 双闸守门）。
  start: (req: LiveAutoStartReq) =>
    request<LiveAutoStartResult>("/live-auto/start", {
      method: "POST",
      body: JSON.stringify(req),
    }),
  // 停止自动交易（平掉自管持仓 + 落 Run 终态）。
  stop: (sessionId: string) =>
    request<LiveAutoStopResult>(`/live-auto/${sessionId}/stop`, { method: "POST" }),
  // 当前活跃会话 + 是否启用（只读，前端重载后据此重连 WS）。
  status: () => request<LiveAutoStatusResponse>("/live-auto/status"),
};

// 自动交易会话 WS（事件协议与 /ws/paper 同构）。
export function liveAutoWS(
  sessionId: string,
  onEvent: (e: WsEvent) => void,
  onState?: (open: boolean) => void,
): ReconnectingWS {
  return new ReconnectingWS(`/api/ws/live-auto/${sessionId}`, onEvent, onState);
}
