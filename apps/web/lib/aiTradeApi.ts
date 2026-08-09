// AI 自动交易「分组」REST + WS 客户端（ai-trade-multi-symbol-and-timeframe §6，镜像 liveAutoGroupApi）。
// 经 Next 同源代理 /api/py/*；错误体解析额外提取 per-symbol preflight 明细（members），供整组失败逐标的提示。
import { ApiRequestError } from "./api";
import { ReconnectingWS, type WsEvent } from "./ws";
import type {
  AiTradeBackstopRepairResult,
  AiTradeGroupMemberStopResult,
  AiTradeGroupStartReq,
  AiTradeGroupStartResult,
  AiTradeGroupStatusResponse,
  AiTradeGroupStopResult,
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
    let members: { instId: string; code: string; message: string }[] | undefined;
    try {
      const body = await res.json();
      if (body?.error) {
        code = body.error.code ?? code;
        message = body.error.message ?? message;
        members = body.error.members;
      }
    } catch {
      // 非 JSON 错误体：保留状态码文案
    }
    throw new ApiRequestError(code, message, res.status, members);
  }
  return res.status === 204 ? (undefined as T) : ((await res.json()) as T);
}

export const aiTradeApi = {
  // 启动 AI 自动交易分组（受双闸守门；两段式门禁 + 整组回滚；预算按成员 pct 切分）。
  start: (req: AiTradeGroupStartReq) =>
    request<AiTradeGroupStartResult>("/ai-trade/start", {
      method: "POST",
      body: JSON.stringify(req),
    }),
  // 停整组（停所有成员、各自平掉自管持仓）。
  stopGroup: (groupId: string) =>
    request<AiTradeGroupStopResult>(`/ai-trade/${groupId}/stop`, { method: "POST" }),
  // 仅停某成员（其余续跑）。
  stopMember: (groupId: string, instId: string) =>
    request<AiTradeGroupMemberStopResult>(
      `/ai-trade/${groupId}/member/${encodeURIComponent(instId)}/stop`,
      { method: "POST" },
    ),
  // 人工授权的**单次**交易所侧兜底补挂（backstop-repair §4.1）。与自动路径共用同一个判定与同一条
  // 写路径，只绕过 `mode`、绝不绕过三道锁；被拦下即 409（`blockedReason` 与卡片、prompt 同值域）。
  repairBackstop: (groupId: string, instId: string) =>
    request<AiTradeBackstopRepairResult>(
      `/ai-trade/${groupId}/member/${encodeURIComponent(instId)}/backstop/repair`,
      { method: "POST" },
    ),
  // 当前活跃分组 + 是否启用（只读，重载后据此重连各成员 WS）。
  status: () => request<AiTradeGroupStatusResponse>("/ai-trade/status"),
};

/** 打开一条 AI 自动交易会话 WS（事件协议 {type,data}，含 decision/summary/agent_trace；每成员一条）。 */
export function aiTradeWS(
  sessionId: string,
  onEvent: (e: WsEvent) => void,
  onState?: (open: boolean) => void,
): ReconnectingWS {
  return new ReconnectingWS(`/api/ws/ai-trade/${sessionId}`, onEvent, onState);
}
