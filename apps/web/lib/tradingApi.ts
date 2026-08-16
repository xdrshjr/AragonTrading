// 实盘交易 REST 客户端（经 /api/py/* 代理 → 后端 /api/trading/*）。
// 复用 accountApi 的 request 风格与错误体解析；绝不携带 / 返回明文凭证（C7）。
import { ApiRequestError, toApiError } from "./api";
import { timeoutFor, withTimeout } from "./httpTimeout";
import type {
  CloseAllResult,
  ClosePositionReq,
  ClosePositionResult,
  LiveFillsResponse,
  LiveOpenOrdersResponse,
  LivePositionsResponse,
  PlaceOrderReq,
  PlaceOrderResult,
  SetLeverageReq,
  SetLeverageResult,
  TradingConfig,
} from "./tradingTypes";

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
  // 未结委托（普通挂单 + 策略委托；轮询源，5s；前端不传 instId，客户端过滤）。
  openOrders: (accountId?: number) =>
    request<LiveOpenOrdersResponse>(`/trading/open-orders${qs({ accountId })}`),
  // 设杠杆（受 LIVE_TRADING_ENABLED 守门）。
  setLeverage: (req: SetLeverageReq) =>
    request<SetLeverageResult>("/trading/leverage", { method: "POST", body: JSON.stringify(req) }),
  // 实盘下单（受 LIVE_TRADING_ENABLED 守门）。
  placeOrder: (req: PlaceOrderReq) =>
    request<PlaceOrderResult>("/trading/order", { method: "POST", body: JSON.stringify(req) }),
  // 单仓平仓（limit 挂单 / market 整仓；受 LIVE_TRADING_ENABLED 守门）。
  closePosition: (req: ClosePositionReq) =>
    request<ClosePositionResult>("/trading/close", { method: "POST", body: JSON.stringify(req) }),
  // 一键市价全平（受 LIVE_TRADING_ENABLED 守门；逐仓诚实汇总）。
  closeAll: (accountId?: number) =>
    request<CloseAllResult>("/trading/close-all", {
      method: "POST",
      body: JSON.stringify({ accountId }),
    }),
};
