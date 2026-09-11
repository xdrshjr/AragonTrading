// 后训练轨迹采集的 REST 客户端（ai-trade-trajectory-capture §4.1）。
//
// 经 Next 同源代理 `/api/py/*`；自持 `request<T>` 镜像 `aiTradeApi.ts` 的写法——超时决策集中
// 在 `lib/httpTimeout.ts`。两个端点都落默认 `FAST_MS`：服务端自带 `probe_timeout_s` 死线，
// 客户端不必另开一档（`httpTimeout.ts` 因此不改）。
//
// `validate` 虽是 POST，副作用却只限于在最近的已存在祖先目录里建删一个探针文件、**不创建
// 目标目录**，故它是读语义：不登记进写路径表，超时文案说「请检查后端是否响应」而不是
// 「结果未知」。
import { ApiRequestError, toApiError } from "./api";
import { timeoutFor, withTimeout } from "./httpTimeout";
import type {
  AiTradeTrajectoryConfig,
  AiTradeTrajectoryDirVerdict,
} from "./trajectoryTypes";

const BASE = "/api/py";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const timeoutMs = timeoutFor(path);
  let res: Response;
  try {
    res = await fetch(`${BASE}${path}`, withTimeout({
      ...init,
      headers: { "Content-Type": "application/json", ...(init?.headers || {}) },
      cache: "no-store",
    }, timeoutMs));
  } catch (err) {
    throw toApiError(err, timeoutMs, path); // 非超时错误原样上抛，绝不吞
  }
  if (!res.ok) throw await toRequestError(res);
  return (await res.json()) as T;
}

/** 非 2xx ⇒ `ApiRequestError`（错误体形如 `{error:{code,message}}`）。 */
async function toRequestError(res: Response): Promise<ApiRequestError> {
  let code = String(res.status);
  let message = res.statusText;
  try {
    const body = await res.json();
    if (body?.error) {
      code = body.error.code ?? code;
      message = body.error.message ?? message;
    }
  } catch {
    // 非 JSON 错误体（老后端没有这条路由时代理回的 404 页）：保留状态码文案。
  }
  return new ApiRequestError(code, message, res.status);
}

export const trajectoryApi = {
  /**
   * 采集配置：运维总闸、默认根目录、可用空间下限等。
   *
   * 老后端没有这条路由 ⇒ 404 ⇒ 卡片禁用复选框并说明「本次不会采集」——老后端的
   * `AiTradeGroupStartIn` 会静默忽略未知键，勾上了却不采是本仓明令禁止的形态。
   */
  config: () =>
    request<AiTradeTrajectoryConfig>("/ai-trade/trajectory/config"),
  /**
   * 校验一个目录（`null` ⇒ 校验服务端默认根目录）。`ok=false` 也是 200。
   *
   * @param directory 用户已提交的目录（已 trim；空串已归一为 `null`）。
   */
  validate: (directory: string | null) =>
    request<AiTradeTrajectoryDirVerdict>("/ai-trade/trajectory/validate", {
      method: "POST",
      body: JSON.stringify({ directory }),
    }),
};
