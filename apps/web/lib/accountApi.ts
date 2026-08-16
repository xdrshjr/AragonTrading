// 实盘账户 REST 客户端（经 /api/py/* 代理 → 后端 /api/accounts/*）。
// 绝不携带 / 返回明文 secret / passphrase（R-Secret）；secret/passphrase 留空 = 保留原值（编辑）。
import { ApiRequestError, toApiError } from "./api";
import { timeoutFor, withTimeout } from "./httpTimeout";
import type {
  OkxAccount,
  OkxAccountDetail,
  OkxAccountDraft,
  OkxAccountTestResult,
  OkxAccountsListResponse,
} from "./accountTypes";

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

// 编辑请求体：apiKey/secret/passphrase 留空字段不下发，让后端「保留原值」语义生效（spec §8）。
function editBody(draft: OkxAccountDraft): Record<string, string> {
  const body: Record<string, string> = {
    label: draft.label,
    ipWhitelist: draft.ipWhitelist,
    permissions: draft.permissions,
  };
  if (draft.apiKey.trim()) body.apiKey = draft.apiKey;
  if (draft.secretKey.trim()) body.secretKey = draft.secretKey;
  if (draft.passphrase.trim()) body.passphrase = draft.passphrase;
  return body;
}

export const accountApi = {
  accounts: {
    list: () => request<OkxAccountsListResponse>("/accounts"),
    create: (draft: OkxAccountDraft) =>
      request<OkxAccount>("/accounts", { method: "POST", body: JSON.stringify(draft) }),
    update: (id: number, draft: OkxAccountDraft) =>
      request<OkxAccount>(`/accounts/${id}`, { method: "PUT", body: JSON.stringify(editBody(draft)) }),
    remove: (id: number) => request<void>(`/accounts/${id}`, { method: "DELETE" }),
    setDefault: (id: number) =>
      request<OkxAccountsListResponse>(`/accounts/${id}/default`, { method: "POST" }),
    // 测存量账户连通性（用库内 secret/passphrase）。
    test: (id: number) =>
      request<OkxAccountTestResult>(`/accounts/${id}/test`, { method: "POST" }),
    // 测草稿连通性（首存前；须带显式 apiKey/secretKey/passphrase）。
    testDraft: (draft: OkxAccountDraft) =>
      request<OkxAccountTestResult>("/accounts/test", {
        method: "POST",
        body: JSON.stringify({
          apiKey: draft.apiKey,
          secretKey: draft.secretKey,
          passphrase: draft.passphrase,
          ipWhitelist: draft.ipWhitelist,
        }),
      }),
    // 查详情：信息 + 资产 + 持仓（真实 OKX，鉴权失败显式错误，绝不假数据）。
    detail: (id: number) => request<OkxAccountDetail>(`/accounts/${id}/detail`),
  },
};
