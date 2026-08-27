// AI 自动交易「分组」REST + WS 客户端（ai-trade-multi-symbol-and-timeframe §6，镜像 liveAutoGroupApi）。
// 经 Next 同源代理 /api/py/*；错误体解析额外提取 per-symbol preflight 明细（members），供整组失败逐标的提示。
import { ApiRequestError, toApiError } from "./api";
import { timeoutFor, withTimeout } from "./httpTimeout";
import { ReconnectingWS, type WsEvent } from "./ws";
import type {
  AiTradeBackstopRepairResult,
  AiTradeEndResult,
  AiTradeGroupMemberStopResult,
  AiTradeGroupRow,
  AiTradeGroupStartReq,
  AiTradeGroupStartResult,
  AiTradeGroupStatusResponse,
  AiTradeGroupStopResult,
  AiTradeMemberExitResult,
  AiTradePauseResult,
  AiTradeRelinkResult,
  AiTradeResumePreview,
} from "./aiTradeTypes";

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
  // 人工授权的**单次**市价离场（exit-priority §6.2）：只平本会话自管持仓，**不停止会话**。
  // 这是本轮人机交互的兜底——无论模型怎么想、无论哪一层怎么判，用户永远有一个不经模型的离场
  // 按钮。运维总闸关 ⇒ 409 `AI_TRADE_MANUAL_EXIT_DISABLED`。
  exitMember: (groupId: string, instId: string) =>
    request<AiTradeMemberExitResult>(
      `/ai-trade/${groupId}/member/${encodeURIComponent(instId)}/exit`,
      { method: "POST" },
    ),
  // 人工授权的**单次**强制重连 + REST 活性补取（feed-self-healing §4.2）：换一条新的 K 线 WS，
  // 再立刻取一次最新已收线根。**不停会话、不平仓**。服务端有 12 秒硬死线，故落 FAST_MS 是安全的
  // ——12 秒内必然回一个诚实的回执，绝不让用户在唯一的兜底按钮上读到「本次操作结果未知」。
  // 运维总闸关 ⇒ 409 `AI_TRADE_FEED_SELFHEAL_DISABLED`。
  relinkMember: (groupId: string, instId: string) =>
    request<AiTradeRelinkResult>(
      `/ai-trade/${groupId}/member/${encodeURIComponent(instId)}/relink`,
      { method: "POST" },
    ),
  // 当前活跃分组 + 是否启用（只读，重载后据此重连各成员 WS）。
  status: () => request<AiTradeGroupStatusResponse>("/ai-trade/status"),
  // ---- 暂停 / 继续 / 结束（ai-trade-pause-resume §五）----
  // 暂停：停一切新决策与开仓，**保留持仓**（`holdPosition: false` = 平仓后暂停）。
  pauseGroup: (groupId: string, body: { holdPosition?: boolean } = {}) =>
    request<AiTradePauseResult>(`/ai-trade/${groupId}/pause`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  // 续跑复核（只读，会向交易所核实持仓 / 挂单）：离线期间发生了什么、契约有没有漂移。
  resumePreview: (groupId: string) =>
    request<AiTradeResumePreview>(`/ai-trade/${groupId}/resume-preview`),
  // 继续：`verdict === "needs_confirm"` 时必须携 `acknowledge: true`（后端 400 兜底）。
  resumeGroup: (groupId: string, body: { acknowledge: boolean }) =>
    request<AiTradeGroupRow>(`/ai-trade/${groupId}/resume`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  // 结束并归档：**带仓时 `flatten` 必填**，选「保留持仓」还要 `acknowledgeOrphanPosition`。
  endGroup: (groupId: string,
             body: { flatten?: boolean | null; acknowledgeOrphanPosition?: boolean } = {}) =>
    request<AiTradeEndResult>(`/ai-trade/${groupId}/end`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
};

/** 打开一条 AI 自动交易会话 WS（事件协议 {type,data}，含 decision/summary/agent_trace；每成员一条）。 */
export function aiTradeWS(
  sessionId: string,
  onEvent: (e: WsEvent) => void,
  onState?: (open: boolean) => void,
): ReconnectingWS {
  return new ReconnectingWS(`/api/ws/ai-trade/${sessionId}`, onEvent, onState);
}
