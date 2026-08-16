// 自动交易设置 REST 客户端（auto-trade-budget-and-allocation-settings §4.1，经 /api/py/* 代理 →
// 后端 /api/settings/auto-trade）。错误体解析复用既有 liveAutoGroupApi 风格。
import { ApiRequestError, toApiError } from "./api";
import { timeoutFor, withTimeout } from "./httpTimeout";
import type { AutoTradeSettings } from "./liveAutoTypes";

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

export const autoTradeSettingsApi = {
  // 读总预算上限百分比 + 经分组运营天花板夹后的有效上限（预览口径，RN-2）。
  get: () => request<AutoTradeSettings>("/settings/auto-trade"),
  // 写总预算上限百分比（0<pct≤100，越界服务端 422）。
  update: (maxBudgetPct: number) =>
    request<AutoTradeSettings>("/settings/auto-trade", {
      method: "PUT",
      body: JSON.stringify({ maxBudgetPct }),
    }),
};
