// 自选自动交易分组 REST 客户端（watchlist-auto-trade-group §4，经 /api/py/* 代理 → 后端 /api/live-auto/group/*）。
// 错误体解析复用 liveAutoApi 风格；运行期事件流复用既有 per-session liveAutoWS（每成员各开一条）。
import { ApiRequestError, toApiError } from "./api";
import { timeoutFor, withTimeout } from "./httpTimeout";
import { liveAutoWS } from "./liveAutoApi";
import type {
  LiveAutoGroupMemberStopResult,
  LiveAutoGroupStartReq,
  LiveAutoGroupStartResult,
  LiveAutoGroupStatusResponse,
  LiveAutoGroupStopResult,
} from "./liveAutoTypes";

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
    let members: { instId: string; code: string; message: string }[] | undefined;
    try {
      const body = await res.json();
      if (body?.error) {
        code = body.error.code ?? code;
        message = body.error.message ?? message;
        members = body.error.members;
      }
    } catch {
      /* ignore parse error */
    }
    throw new ApiRequestError(code, message, res.status, members);
  }
  return res.status === 204 ? (undefined as T) : ((await res.json()) as T);
}

export const liveAutoGroupApi = {
  // 启动多标的自动交易分组（受双闸守门；两段式门禁 + 整组回滚）。
  start: (req: LiveAutoGroupStartReq) =>
    request<LiveAutoGroupStartResult>("/live-auto/group/start", {
      method: "POST",
      body: JSON.stringify(req),
    }),
  // 停整组（停所有成员、各自平仓）。
  stop: (groupId: string) =>
    request<LiveAutoGroupStopResult>(`/live-auto/group/${groupId}/stop`, { method: "POST" }),
  // 仅停某成员（其余续跑）。
  stopMember: (groupId: string, instId: string) =>
    request<LiveAutoGroupMemberStopResult>(
      `/live-auto/group/${groupId}/member/${encodeURIComponent(instId)}/stop`,
      { method: "POST" },
    ),
  // 当前活跃分组 + 是否启用（只读，重载后据此重连各成员 WS）。
  status: () => request<LiveAutoGroupStatusResponse>("/live-auto/group/status"),
};

export { liveAutoWS };
