// 交易经验库 REST 客户端（经 /api/py/* 代理 → 后端 /api/experience/*）。
// 逐字沿用 `autoTradeSettingsApi` 的 `httpTimeout` 接线与错误体解析范式。
//
// 三条路径已在 `lib/httpTimeout.ts` 登记为长跑档（E-11）：`/experience/rebuild`、`/export`、
// `/import`。不登记就落 `FAST_MS`（20 秒），而全库重建在大库上超 20 秒是常态 ⇒ 重建按钮
// **必然**假超时；`rebuild` 与 `import` 同时进写类前缀表，故一次超时的替换式导入会说
// 「结果未知」而不是「失败」——它确实可能已经写进去了。
import { ApiRequestError, toApiError } from "./api";
import { timeoutFor, withTimeout } from "./httpTimeout";
import type {
  ExperienceFilters,
  ExperienceGraphView,
  ExperienceHealth,
  ExperienceImportResult,
  ExperienceLessonPage,
  ExperienceNode,
  ExperienceNodeDetail,
  ExperienceOverview,
  ExperiencePreview,
  ExperiencePruneResult,
  ExperienceRebuildResult,
  ExperienceSettings,
  ExperienceSettingsPatch,
  ExperienceUtilityPage,
} from "./experienceTypes";

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

/** 把筛选条件拼成 query（空值一律不发——`?instId=` 会被后端当成一个真的筛选条件）。 */
function query(filters: ExperienceFilters & Record<string, unknown>): string {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(filters)) {
    if (value === undefined || value === null || value === "") continue;
    params.set(key, String(value));
  }
  const text = params.toString();
  return text ? `?${text}` : "";
}

export const experienceApi = {
  overview: () => request<ExperienceOverview>("/experience/overview"),

  graph: (filters: ExperienceFilters, limit = 120) =>
    request<ExperienceGraphView>(`/experience/graph${query({ ...filters, limit })}`),

  lessons: (filters: ExperienceFilters, offset = 0, limit = 50) =>
    request<ExperienceLessonPage>(`/experience/lessons${query({ ...filters, offset, limit })}`),

  detail: (nodeId: number, evidenceLimit = 20) =>
    request<ExperienceNodeDetail>(
      `/experience/nodes/${nodeId}${query({ evidenceLimit })}`),

  create: (body: { statement: string; detail: string; context: Record<string, string>;
                   polarity: "positive" | "negative" }) =>
    request<{ node: ExperienceNode }>("/experience/nodes", {
      method: "POST", body: JSON.stringify(body),
    }),

  patch: (nodeId: number, body: {
    statement?: string; detail?: string; pinned?: boolean; muted?: boolean;
    tier?: "candidate" | "normal" | "iron";
  }) =>
    request<{ node: ExperienceNode }>(`/experience/nodes/${nodeId}`, {
      method: "PATCH", body: JSON.stringify(body),
    }),

  remove: (nodeId: number) =>
    request<void>(`/experience/nodes/${nodeId}`, { method: "DELETE" }),

  /**
   * 手动重建（长跑档；CPU 段在后端走线程池）。
   *
   * `scope="stat"`（默认）= 智能：由增量物化的五条判据自选增量还是全量。
   * `scope="full"` = **强制**全量并 `epoch+1`——它是增量算错时的兜底出口。
   */
  rebuild: (scope: "stat" | "full" = "stat") =>
    request<ExperienceRebuildResult>("/experience/rebuild", {
      method: "POST", body: JSON.stringify({ scope }),
    }),

  /** 物化健康 + epoch 现状 + 已自动静音条数（**轮询专用**；`overview` 会扫整张节点表）。 */
  health: () => request<ExperienceHealth>("/experience/health"),

  /** 效用面板：「这条经验被喂进去之后，那些单后来怎么样」（共现，不是因果）。 */
  utility: (filters: { instId?: string; bar?: string; minInjected?: number; limit?: number }) =>
    request<ExperienceUtilityPage>(`/experience/utility${query(filters)}`),

  /**
   * 清理沉降经验。**必须先 `dryRun` 再确认**——这是一个删除操作，默认值站在「先看一眼」那侧。
   * 它落写路径超时档：超时要说「结果未知，请刷新核对」而不是「失败」。
   */
  prune: (dryRun: boolean) =>
    request<ExperiencePruneResult>("/experience/prune", {
      method: "POST", body: JSON.stringify({ scope: "stale", dryRun }),
    }),

  /** 检索预演——「Agent 下一轮会看到什么」的逐字答案。 */
  preview: (axes: Record<string, string | undefined>) =>
    request<ExperiencePreview>("/experience/preview", {
      method: "POST", body: JSON.stringify(axes),
    }),

  /**
   * 导出为 NDJSON 文本。
   *
   * 刻意**不在这里触发下载**：本函数只负责取到文本，「怎么交给用户」是组件的事（浏览器里是
   * `Blob` + `<a download>`，桌面壳里可能另有落点）。混在一起会让这个客户端无法被单测。
   */
  exportNdjson: async (filters: { instId?: string; since?: number; until?: number }) => {
    const path = `/experience/export${query(filters)}`;
    const timeoutMs = timeoutFor(path);
    let res: Response;
    try {
      res = await fetch(`${BASE}${path}`, withTimeout({ cache: "no-store" }, timeoutMs));
    } catch (err) {
      throw toApiError(err, timeoutMs, path);
    }
    if (!res.ok) throw new ApiRequestError(String(res.status), res.statusText, res.status);
    return res.text();
  },

  /** 导入（不可信输入；条数越界 ⇒ 422 `EXPERIENCE_IMPORT_TOO_LARGE`，一条都不会写进去）。 */
  importNodes: (body: { nodes: unknown[]; mode: "merge" | "replace" }) =>
    request<ExperienceImportResult>("/experience/import", {
      method: "POST",
      body: JSON.stringify({ version: 1, nodes: body.nodes, edges: [], events: [],
                             mode: body.mode }),
    }),

  settings: () => request<ExperienceSettings>("/settings/experience"),

  updateSettings: (patch: ExperienceSettingsPatch) =>
    request<ExperienceSettings>("/settings/experience", {
      method: "PUT", body: JSON.stringify(patch),
    }),
};
