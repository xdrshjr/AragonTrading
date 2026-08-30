// 标的市场情报层的 REST 客户端（ai-trade-market-intel §4.1）。
// 镜像 `aiTradeApi.ts` 的 `request` 包装与错误体解析（`{error:{code,message}}`）：超时决策集中在
// `lib/httpTimeout.ts`，本特性的端点**全部**走 FAST 档——采集是「排队即返回」的 202，probe 的
// 服务端总耗时被硬封顶在 12 秒（评审 P1-6），故 `httpTimeout.ts` 刻意不改。
import { ApiRequestError, toApiError } from "./api";
import { timeoutFor, withTimeout } from "./httpTimeout";
import type {
  IntelCitationRow,
  IntelFeedbackInput,
  IntelFeedbackRow,
  IntelImpactReport,
  IntelItem,
  IntelItemInput,
  IntelItemPatch,
  IntelProbeResult,
  IntelProviderHealth,
  IntelReadResponse,
  IntelRound,
  IntelSettings,
  IntelSettingsPatch,
  IntelSourceRow,
  IntelTier,
  AiTradeIntelSummary,
} from "./intelTypes";

const BASE = "/api/py";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const timeoutMs = timeoutFor(path);
  let res: Response;
  try {
    // 取数与超时包装**必须写在同一行**：`contrastTokens.source.test.ts` 的第 ④ 组是**逐行**扫描
    // 的源码门禁（判据是调用点而不是文件），拆成多行会让它判红。此处的注释也刻意不写出那两个
    // 函数名的字面调用形态——那会让同一条正则把注释也数成一个调用点。
    res = await fetch(`${BASE}${path}`, withTimeout({
      ...init,
      headers: { "Content-Type": "application/json", ...(init?.headers || {}) },
      cache: "no-store",
    }, timeoutMs));
  } catch (err) {
    throw toApiError(err, timeoutMs, path); // 非超时错误原样上抛，绝不吞
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
  return res.status === 204 ? (undefined as T) : ((await res.json()) as T);
}

const enc = encodeURIComponent;

export const intelApi = {
  /** 读情报库（summary + 三层条目）。**不受总闸约束**：看历史情报不动任何资金。 */
  read: (
    instId: string,
    params: { tier?: IntelTier; category?: string; status?: string; q?: string; limit?: number } = {},
  ) => {
    const qs = new URLSearchParams();
    if (params.tier) qs.set("tier", params.tier);
    if (params.category) qs.set("category", params.category);
    if (params.status) qs.set("status", params.status);
    if (params.q) qs.set("q", params.q);
    if (params.limit) qs.set("limit", String(params.limit));
    const suffix = qs.toString() ? `?${qs}` : "";
    return request<IntelReadResponse>(`/intel/${enc(instId)}${suffix}`);
  },
  /** 采集 / 整理轮的审计时间线。 */
  rounds: (instId: string, kind?: "collect" | "curate" | "verify", limit = 20) =>
    request<{ rounds: IntelRound[] }>(
      `/intel/${enc(instId)}/rounds?limit=${limit}${kind ? `&kind=${kind}` : ""}`,
    ),
  /** 手动触发一轮采集（202 排队即返回）。 */
  collect: (instId: string) =>
    request<{ queued: boolean }>(`/intel/${enc(instId)}/collect`, { method: "POST" }),
  /** 手动触发一轮整理。 */
  curate: (instId: string) =>
    request<{ queued: boolean }>(`/intel/${enc(instId)}/curate`, { method: "POST" }),
  /** 手工新增一条情报（**不做乐观更新**：服务端会算 dedupKey 并可能判重）。 */
  createItem: (instId: string, body: IntelItemInput) =>
    request<{ item: IntelItem }>(`/intel/${enc(instId)}/items`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  /** 编辑 / 置顶 / 静音。 */
  patchItem: (itemId: number, patch: IntelItemPatch) =>
    request<{ item: IntelItem }>(`/intel/items/${itemId}`, {
      method: "PATCH",
      body: JSON.stringify(patch),
    }),
  /** 移层（服务端在同一事务内重算 `expiresTs`）。 */
  moveItem: (itemId: number, tier: IntelTier) =>
    request<{ item: IntelItem }>(`/intel/items/${itemId}/move`, {
      method: "POST",
      body: JSON.stringify({ tier }),
    }),
  /** 物理删除（唯一的硬删路径）。 */
  deleteItem: (itemId: number) =>
    request<void>(`/intel/items/${itemId}`, { method: "DELETE" }),
  /** 标记已读：未读徽标的真值落服务端，换台机器也一致。 */
  markSeen: (instId: string, ts?: number) =>
    request<{ summary: AiTradeIntelSummary }>(`/intel/${enc(instId)}/seen`, {
      method: "POST",
      body: JSON.stringify({ ts: ts ?? null }),
    }),
  /** 读检索源设置（**绝不含明文密钥**）。 */
  settings: () => request<IntelSettings>("/settings/intel"),
  /** 写检索源设置（只写显式给出的字段）。 */
  saveSettings: (patch: IntelSettingsPatch) =>
    request<IntelSettings>("/settings/intel", {
      method: "PUT",
      body: JSON.stringify(patch),
    }),
  // ---------------- closed-loop（§4.1 #13-#21） ----------------
  /** 手动触发一轮核查（202 排队即返回）。 */
  verify: (instId: string) =>
    request<{ queued: boolean }>(`/intel/${enc(instId)}/verify`, { method: "POST" }),
  /** 只核查这一条：**显式绕过全部候选过滤**（人类显式指令，不是自动池的一员）。 */
  verifyItem: (itemId: number) =>
    request<{ queued: boolean }>(`/intel/items/${itemId}/verify`, { method: "POST" }),
  /** 黑白名单清单（`instId` 缺省 = 全局 + 每个标的）。 */
  feedback: (instId?: string) =>
    request<{ rows: IntelFeedbackRow[] }>(
      `/intel/feedback${instId ? `?instId=${enc(instId)}` : ""}`,
    ),
  /** 记一条来源取舍；`block` 会同事务扫除存量并回执归档条数。 */
  writeFeedback: (body: IntelFeedbackInput) =>
    request<{ ok: boolean; archived: number }>("/intel/feedback", {
      method: "POST",
      body: JSON.stringify({ scope: "domain", instId: "", ...body }),
    }),
  /** 给确认框算受影响条数（**纯读**，绝不写库）。 */
  previewFeedback: (body: IntelFeedbackInput) =>
    request<{ affected: number }>("/intel/feedback/preview", {
      method: "POST",
      body: JSON.stringify({ scope: "domain", instId: "", ...body }),
    }),
  /** 撤销一条来源取舍。**不复活**任何已归档条目。 */
  clearFeedback: (key: string, instId = "") =>
    request<void>(
      `/intel/feedback?scope=domain&key=${enc(key)}&instId=${enc(instId)}`,
      { method: "DELETE" },
    ),
  /** 检索源健康读数（最近 N 轮的成败聚合 + 熔断剩余秒）。 */
  providerHealth: (limit = 20) =>
    request<IntelProviderHealth>(`/intel/settings/health?limit=${limit}`),
  /** 当前库存的域名聚合（信源页签上半）。 */
  sources: (instId: string) =>
    request<{ rows: IntelSourceRow[] }>(`/intel/${enc(instId)}/sources`),
  /** 影响力页签的全部读数。 */
  impact: (instId: string, limit = 10) =>
    request<IntelImpactReport>(`/intel/${enc(instId)}/impact?limit=${limit}`),
  /** 引用明细（游标分页，按 id 降序）。 */
  citations: (
    instId: string,
    params: { itemId?: number; limit?: number; before?: number } = {},
  ) => {
    const qs = new URLSearchParams();
    if (params.itemId) qs.set("itemId", String(params.itemId));
    qs.set("limit", String(params.limit ?? 50));
    if (params.before) qs.set("before", String(params.before));
    return request<{ rows: IntelCitationRow[]; hasMore: boolean }>(
      `/intel/${enc(instId)}/citations?${qs}`,
    );
  },
  /** 测试连接（服务端总耗时硬封顶 12 秒）。 */
  probe: (body: { provider?: string; apiKey?: string } = {}) =>
    request<IntelProbeResult>("/settings/intel/probe", {
      method: "POST",
      body: JSON.stringify(body),
    }),
};
