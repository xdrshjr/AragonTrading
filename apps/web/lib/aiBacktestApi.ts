// AI 回测 REST + WS 客户端（镜像 aiTradeApi.ts / weightTrainWs.ts：本地 request + ReconnectingWS，
// 经 Next 同源代理 /api/py/*，WS 直连 /api/ws/ai-backtest/{jobId}）。
import { ApiRequestError } from "./api";
import type { AiBacktestStartReq, AiBacktestStartResult, AiBacktestStatus } from "./aiBacktestTypes";
import { ReconnectingWS, type WsEvent } from "./ws";

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

export const aiBacktestApi = {
  start: (req: AiBacktestStartReq) =>
    request<AiBacktestStartResult>("/ai-trade/backtest/start", {
      method: "POST",
      body: JSON.stringify(req),
    }),
  cancel: (jobId: string) =>
    request<{ jobId: string; state: string }>(`/ai-trade/backtest/${jobId}/cancel`, {
      method: "POST",
    }),
  status: (jobId: string) =>
    request<{ job: AiBacktestStatus }>(`/ai-trade/backtest/${jobId}`),
};

/** 打开一条 AI 回测作业 WS（事件协议 {type,data}，含 phase/progress/decision/summary/trade/result/done）。 */
export function aiBacktestWS(
  jobId: string,
  onEvent: (e: WsEvent) => void,
  onState?: (open: boolean) => void,
): ReconnectingWS {
  return new ReconnectingWS(`/api/ws/ai-backtest/${jobId}`, onEvent, onState);
}
