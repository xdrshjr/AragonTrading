// 因子组合探索训练 REST 客户端（经 /api/py/* 代理 → 后端 /api/weight-models/explorations）。
// 独立文件（factor-combo-exploration C-5），镜像 weightBatchApi.ts / weightBatch.ts 的 API ↔ body-builder 拆分口径；
// 读 / 择优 / 取消 / 删除 / WS 全部复用 weightBatchApi（探索批次就是批次），故本文件只承接「启动」一个端点。
import { ApiRequestError, toApiError } from "./api";
import { timeoutFor, withTimeout } from "./httpTimeout";
import type { WeightExploreStartBody, WeightExploreStartResult } from "./weightExplore";

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

export const weightExploreApi = {
  start: (body: WeightExploreStartBody) =>
    request<WeightExploreStartResult>("/weight-models/explorations", {
      method: "POST",
      body: JSON.stringify(body),
    }),
};
