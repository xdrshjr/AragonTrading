// AI 自动交易统计历史 REST 客户端（ai-trade-stats-dashboard §3.8 / §5.2）。
// P1-2：仓库无 apiGet/apiFetch 导出（lib/api.ts 的 request/rawRequest 为模块私有，仅导出 api 与
// ApiRequestError）。故镜像 aiTradeApi.ts 的自持范式：本地私有 get<T>（BASE="/api/py"、cache:"no-store"），
// 复用 ./api 的 ApiRequestError；不引用任何不存在的 helper。历史为一次性只读拉取，无 WS。
import { ApiRequestError, toApiError } from "./api";
import { timeoutFor, withTimeout } from "./httpTimeout";
import type { AiTradeHistoryDetailOut, AiTradeHistoryListOut } from "./aiTradeHistoryTypes";
import type { AiTradeProfitabilityResponse } from "./aiTradeProfitability";
import type { AiTradePresetComparisonResponse } from "./aiTradeTypes";

const BASE = "/api/py";

async function get<T>(path: string): Promise<T> {
  // D3：超时决策集中在 lib/httpTimeout.ts，接线留在原地。
  const timeoutMs = timeoutFor(path);
  let res: Response;
  try {
    res = await fetch(`${BASE}${path}`, withTimeout({ cache: "no-store" }, timeoutMs));
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
  // 历史纪律预设对照（tradability-and-open-funnel 轴 E）：用**真实资金的历史**回答「哪一档更赚」。
  // 后端按 `aiStats.disciplinePreset` 分组聚合；`conclusive=false` 的组必须显式写「样本不足」。
  presetComparison: () =>
    get<AiTradePresetComparisonResponse>("/ai-trade/history/preset-comparison"),
  // 跨会话盈利体检（realized-edge-calibration 轴 B/C）：**池化**口径的历史统计 + 校准三问。
  // 全部过滤参数可选，缺省 = 全部历史；后端保证空历史也返回结构完整的回执（不 404、不 500）。
  profitability: (params: { instId?: string; bar?: string; sinceTs?: number } = {}) => {
    const qs = new URLSearchParams(
      Object.entries(params).filter(([, v]) => v != null).map(([k, v]) => [k, String(v)]),
    ).toString();
    return get<AiTradeProfitabilityResponse>(`/ai-trade/history/profitability${qs ? `?${qs}` : ""}`);
  },
};
