// AI 自动交易统计历史 REST 客户端（ai-trade-stats-dashboard §3.8 / §5.2）。
// P1-2：仓库无 apiGet/apiFetch 导出（lib/api.ts 的 request/rawRequest 为模块私有，仅导出 api 与
// ApiRequestError）。故镜像 aiTradeApi.ts 的自持范式：本地私有 get<T>（BASE="/api/py"、cache:"no-store"），
// 复用 ./api 的 ApiRequestError；不引用任何不存在的 helper。历史为一次性只读拉取，无 WS。
import { ApiRequestError } from "./api";
import type { AiTradeHistoryDetailOut, AiTradeHistoryListOut } from "./aiTradeHistoryTypes";

const BASE = "/api/py";

async function get<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`, { cache: "no-store" });
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
      // 非 JSON 错误体：保留状态码文案
    }
    throw new ApiRequestError(code, message, res.status);
  }
  return (await res.json()) as T;
}

export const aiTradeHistoryApi = {
  // 历史会话列表（分页；后端按 startedTs DESC、排除当前运行组）。
  list: (limit = 20, offset = 0) =>
    get<AiTradeHistoryListOut>(`/ai-trade/history?limit=${limit}&offset=${offset}`),
  // 单会话详情（会话头 + 逐成员成交 / metrics + 后端聚合 summary）。
  detail: (groupId: string) =>
    get<AiTradeHistoryDetailOut>(`/ai-trade/history/${encodeURIComponent(groupId)}`),
};
