// 交易日志的 REST 客户端（ai-trade-audit-journal §3.3）。
//
// 镜像 `aiTradeHistoryApi.ts` 的自持范式：本地私有 `get<T>` / `post<T>`（BASE="/api/py"、
// `cache:"no-store"`），复用 `./api` 的 `ApiRequestError`；不引用任何不存在的 helper
// （`lib/api.ts` 的 `request`/`rawRequest` 是模块私有的）。
//
// **导出本身不走这里**：`GET /export` 由 `<a download>` 的浏览器导航触发（见
// `journalExport.ts`），刻意不用 `fetch` + `Blob`——几百 MB 的 NDJSON 走 Blob 会把渲染进程的
// 内存顶起来。它的时间保护在服务端（墙钟预算）。
import { ApiRequestError, toApiError } from "./api";
import { timeoutFor, withTimeout } from "./httpTimeout";
import type {
  DecisionDetail,
  DecisionsPage,
  ExportFormat,
  ExportPreview,
  JournalEventsPage,
  JournalFillsPage,
  JournalFilters,
  JournalSessionsPage,
  JournalStats,
  PromptRebuild,
  PruneResult,
  SftOptions,
} from "./journalTypes";

const BASE = "/api/py";
const ROOT = "/ai-trade/journal";

async function unwrap<T>(res: Response): Promise<T> {
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

async function get<T>(path: string): Promise<T> {
  const timeoutMs = timeoutFor(path);
  let res: Response;
  try {
    res = await fetch(`${BASE}${path}`, withTimeout({ cache: "no-store" }, timeoutMs));
  } catch (err) {
    throw toApiError(err, timeoutMs, path);
  }
  return unwrap<T>(res);
}

async function post<T>(path: string, body: unknown): Promise<T> {
  const timeoutMs = timeoutFor(path);
  // 出站调用与 withTimeout **必须写在同一行**：`contrastTokens.source.test.ts` 的门禁 ④
  // 按行扫「每一个出站调用都接了超时」，跨行写法会被判成漏接。那道门禁守的是一件真事——
  // 一个忘了接超时的按钮会静默冻结最长 5 分钟。
  // （本注释刻意不写出那个函数名的带括号形态：门禁数的是**源码原文**里的出现次数，
  //   一句注释会让它多数出一个并不存在的调用点。）
  const init: RequestInit = {
    method: "POST",
    cache: "no-store",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  };
  let res: Response;
  try {
    res = await fetch(`${BASE}${path}`, withTimeout(init, timeoutMs));
  } catch (err) {
    throw toApiError(err, timeoutMs, path);
  }
  return unwrap<T>(res);
}

/**
 * 把过滤条件拼成 querystring。
 *
 * `null` / `undefined` / 空串 / `false` 一律**不进串**：一个 `executedOnly=false` 既没有语义
 * 也会让浏览器缓存键无谓地分叉。
 */
export function toQuery(params: Record<string, unknown>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === null || value === undefined || value === "" || value === false) continue;
    search.set(key, String(value));
  }
  const text = search.toString();
  return text ? `?${text}` : "";
}

export const journalApi = {
  sessions: (limit = 30, cursorTs?: number) =>
    get<JournalSessionsPage>(`${ROOT}/sessions${toQuery({ limit, cursorTs })}`),

  decisions: (filters: JournalFilters, opts: { beforeId?: number; limit?: number } = {}) =>
    get<DecisionsPage>(`${ROOT}/decisions${toQuery({ ...filters, ...opts })}`),

  decision: (id: number) => get<DecisionDetail>(`${ROOT}/decisions/${id}`),

  /** 按需重建提示词。`shaMatch=false` **不是错误**（200），但界面必须显著告知。 */
  prompt: (id: number) => get<PromptRebuild>(`${ROOT}/decisions/${id}/prompt`),

  events: (
    filters: { groupId?: string; instId?: string; level?: string; category?: string },
    opts: { beforeId?: number; limit?: number } = {},
  ) => get<JournalEventsPage>(`${ROOT}/events${toQuery({ ...filters, ...opts })}`),

  fills: (filters: JournalFilters, opts: { beforeId?: number; limit?: number } = {}) =>
    get<JournalFillsPage>(`${ROOT}/fills${toQuery({ ...filters, ...opts })}`),

  stats: () => get<JournalStats>(`${ROOT}/stats`),

  /** 导出预检。它是本模块唯一走**长跑超时档**的路径（见 `httpTimeout.ts` 的前缀表）。 */
  exportPreview: (format: ExportFormat, scope: JournalFilters, sft: Partial<SftOptions>) =>
    post<ExportPreview>(`${ROOT}/export/preview`, { format, scope, sft }),

  /** 剪枝。写路径：超时要说「结果未知，请刷新核对」而不是「失败」。 */
  prune: (dryRun: boolean) => post<PruneResult>(`${ROOT}/prune`, { dryRun }),
};
