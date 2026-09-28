import { ApiRequestError } from "./api";
import { withTimeout } from "./httpTimeout";
import type {
  BindingInput, IntentPage, TradeIntentView, TradingBinding,
  TradingCapabilities,
} from "./agentTradingTypes";

const PREFIX = "/api/py/agent";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${PREFIX}${path}`, withTimeout({
    ...init, cache: "no-store",
    headers: { "Content-Type": "application/json" },
  }, 20_000));
  const body = await response.json();
  if (!response.ok) {
    throw new ApiRequestError(
      body?.error?.code ?? "TRADING_REQUEST_FAILED",
      body?.error?.message ?? "交易查询失败，请查询当前状态。",
      response.status,
    );
  }
  return body as T;
}

function conversationPath(cid: string): string {
  return `/conversations/${encodeURIComponent(cid)}`;
}

function intentPath(cid: string, id: string): string {
  return `${conversationPath(cid)}/trade-intents/${encodeURIComponent(id)}`;
}

function pageQuery(cursor?: string): string {
  return `?limit=50${cursor ? `&cursor=${encodeURIComponent(cursor)}` : ""}`;
}

/** 每次调用至多一次请求；execute 超时由调用方 GET 恢复，禁止自动重发。 */
export const agentTradingApi = {
  capabilities: () => request<TradingCapabilities>("/trading/capabilities"),
  unsettled: (cursor?: string) => request<IntentPage>(
    `/trading/unsettled-intents${pageQuery(cursor)}`,
  ),
  binding: (cid: string) => request<TradingBinding | null>(
    `${conversationPath(cid)}/trading-binding`,
  ),
  bind: (cid: string, input: BindingInput) => request<TradingBinding>(
    `${conversationPath(cid)}/trading-binding`,
    { method: "PUT", body: JSON.stringify(input) },
  ),
  intents: (cid: string, cursor?: string) => request<IntentPage>(
    `${conversationPath(cid)}/trade-intents${pageQuery(cursor)}`,
  ),
  intent: (cid: string, id: string) => request<TradeIntentView>(
    intentPath(cid, id),
  ),
  execute: (cid: string, id: string, input: {
    version: number; previewHash: string;
  }) => request<TradeIntentView>(`${intentPath(cid, id)}/execute`, {
    method: "POST", body: JSON.stringify(input),
  }),
  dismiss: (cid: string, id: string, version: number) =>
    request<TradeIntentView>(`${intentPath(cid, id)}/dismiss`, {
      method: "POST", body: JSON.stringify({ version }),
    }),
};
