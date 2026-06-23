// 实盘交易 REST 客户端（经 /api/py/* 代理 → 后端 /api/trading/*）。
// 复用 accountApi 的 request 风格与错误体解析；绝不携带 / 返回明文凭证（C7）。
import { ApiRequestError } from "./api";
import type {
  LiveFillsResponse,
  LivePositionsResponse,
  PlaceOrderReq,
  PlaceOrderResult,
  SetLeverageReq,
  SetLeverageResult,
  TradingConfig,
} from "./tradingTypes";

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

// 拼装 query string，跳过 undefined / null（保持 URL 干净，让后端默认值生效）。
function qs(params: Record<string, string | number | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== "") search.set(key, String(value));
  }
  const text = search.toString();
  return text ? `?${text}` : "";
}

export const tradingApi = {
  // 下单面板上下文（posMode / 可用 USDT / 元数据 / 最新价 / 可下单性）。
  config: (instId: string, accountId?: number) =>
    request<TradingConfig>(`/trading/config${qs({ instId, accountId })}`),
  // 真实持仓（轮询源，5s）。
  positions: (accountId?: number) =>
    request<LivePositionsResponse>(`/trading/positions${qs({ accountId })}`),
  // 真实成交明细（轮询源，8s）。
  fills: (instId?: string, accountId?: number, limit = 100) =>
    request<LiveFillsResponse>(`/trading/fills${qs({ instId, accountId, limit })}`),
  // 设杠杆（受 LIVE_TRADING_ENABLED 守门）。
  setLeverage: (req: SetLeverageReq) =>
    request<SetLeverageResult>("/trading/leverage", { method: "POST", body: JSON.stringify(req) }),
  // 实盘下单（受 LIVE_TRADING_ENABLED 守门）。
  placeOrder: (req: PlaceOrderReq) =>
    request<PlaceOrderResult>("/trading/order", { method: "POST", body: JSON.stringify(req) }),
};
