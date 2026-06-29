// REST 客户端：封装 /api/py/*（经 Next rewrite 代理到 Python 后端）(spec §8.2 / §9.1)。
import type {
  BacktestResult,
  Candle,
  ComboMemberSpec,
  ComboPolicy,
  ComboPreviewResult,
  CompareResult,
  DataSource,
  EquityPoint,
  Health,
  Instrument,
  MultiSampleResult,
  PaperBatchStartResult,
  PaperBatchStopResult,
  PaperSessionRow,
  PaperStopAllResult,
  Run,
  Strategy,
  StrategyKind,
  Trade,
  WalkForwardResult,
} from "./types";

const BASE = "/api/py";

export class ApiRequestError extends Error {
  code: string;
  status: number;
  // 分组前置门禁失败（LIVE_AUTO_GROUP_PREFLIGHT）携带的逐标的明细（可选，向后兼容）。
  members?: { instId: string; code: string; message: string }[];
  constructor(
    code: string,
    message: string,
    status: number,
    members?: { instId: string; code: string; message: string }[],
  ) {
    super(message);
    this.code = code;
    this.status = status;
    this.members = members;
  }
}

function formatValidationDetail(detail: unknown): string | null {
  if (!Array.isArray(detail)) return null;
  const messages = detail
    .map((item) => {
      if (!item || typeof item !== "object") return null;
      const record = item as Record<string, unknown>;
      const loc = Array.isArray(record.loc)
        ? record.loc.filter((part) => part !== "body").map(String).join(".")
        : "";
      const msg = typeof record.msg === "string" ? record.msg : "Invalid value";
      return loc ? `${loc}: ${msg}` : msg;
    })
    .filter((message): message is string => Boolean(message));
  return messages.length ? messages.join("; ") : null;
}

// 内部变体：同 request 的错误处理，但同时回传原始 Response 以便读取响应头（F2）。
async function rawRequest<T>(
  path: string,
  init?: RequestInit,
): Promise<{ data: T; res: Response }> {
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
      } else {
        const validationMessage = formatValidationDetail(body?.detail);
        if (validationMessage) {
          code = "VALIDATION_ERROR";
          message = validationMessage;
        }
      }
    } catch {
      /* ignore parse error */
    }
    throw new ApiRequestError(code, message, res.status);
  }
  const data = res.status === 204 ? (undefined as T) : ((await res.json()) as T);
  return { data, res };
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  return (await rawRequest<T>(path, init)).data;
}

export const api = {
  health: () => request<Health>("/health"),

  instruments: (instType = "SWAP") =>
    request<Instrument[]>(`/instruments?instType=${encodeURIComponent(instType)}`),

  candles: (instId: string, bar: string, limit = 300, before?: number, after?: number) => {
    const params = new URLSearchParams({ instId, bar, limit: String(limit) });
    if (before) params.set("before", String(before));
    if (after) params.set("after", String(after));
    return request<Candle[]>(`/candles?${params.toString()}`);
  },

  // 主历史拉取：同 candles，但读响应头 X-Aragon-Data-Source 暴露数据来源（F2）。
  // 经 /api/py/* 同源代理（Next rewrite 透传上游头），故无需 CORS expose 即可读到。
  candlesWithMeta: async (
    instId: string,
    bar: string,
    limit = 300,
    before?: number,
    after?: number,
  ): Promise<{ candles: Candle[]; source: DataSource }> => {
    const params = new URLSearchParams({ instId, bar, limit: String(limit) });
    if (before) params.set("before", String(before));
    if (after) params.set("after", String(after));
    const { data, res } = await rawRequest<Candle[]>(`/candles?${params.toString()}`);
    const source: DataSource = res.headers.get("X-Aragon-Data-Source") === "cache" ? "cache" : "live";
    return { candles: data, source };
  },

  strategies: () => request<Strategy[]>("/strategies"),
  strategy: (id: number) => request<Strategy>(`/strategies/${id}`),
  createStrategy: (body: { templateKey: string; name: string; params: Record<string, unknown> }) =>
    request<Strategy>("/strategies", { method: "POST", body: JSON.stringify(body) }),
  updateStrategy: (
    id: number,
    body: { name?: string; params?: Record<string, unknown>; enabled?: boolean },
  ) => request<Strategy>(`/strategies/${id}`, { method: "PUT", body: JSON.stringify(body) }),
  deleteStrategy: (id: number) => request<void>(`/strategies/${id}`, { method: "DELETE" }),

  backtest: (
    id: number,
    body: {
      instId: string;
      bar: string;
      limit?: number;
      startTs: number;
      endTs: number;
      initialCapital?: number;
      fees?: number;
      slippage?: number;
      leverage?: number;
    },
  ) =>
    request<BacktestResult>(`/strategies/${id}/backtest`, {
      method: "POST",
      body: JSON.stringify(body),
    }),

  // 高级回测：滑动窗口 / Walk-forward（不影响 backtest）。返回窗口分布 + 汇总（spec §5.3）。
  walkForward: (
    id: number,
    body: {
      instId: string;
      bar: string;
      startTs: number;
      endTs: number;
      windowBars: number;
      stepBars: number;
      resetCapital: boolean;
      initialCapital?: number;
      fees?: number;
      slippage?: number;
      leverage?: number;
      maxBars?: number;
    },
  ) =>
    request<WalkForwardResult>(`/strategies/${id}/walk-forward`, {
      method: "POST",
      body: JSON.stringify(body),
    }),

  // 历史复看：从 parent run 的 config_json 还原 {runId, summary, windows}（GET /runs/{id}/walk-forward）。
  walkForwardResult: (runId: number) =>
    request<WalkForwardResult>(`/runs/${runId}/walk-forward`),

  // 多策略对比（F3）：一次回测多个策略，返回含归一化前净值的榜单
  compare: (body: {
    strategyIds: number[];
    instId: string;
    bar: string;
    limit?: number;
    startTs: number;
    endTs: number;
    initialCapital?: number;
    fees?: number;
    slippage?: number;
    leverage?: number;
  }) =>
    request<CompareResult>("/strategies/compare", {
      method: "POST",
      body: JSON.stringify(body),
    }),

  // 多样本回测：同一策略对多个标的逐一回测（顺序执行，per-symbol 错误隔离），按标的聚合返回。
  multiBacktest: (
    id: number,
    body: {
      instIds: string[];
      bar: string;
      limit?: number;
      startTs: number;
      endTs: number;
      initialCapital?: number;
      fees?: number;
      slippage?: number;
      leverage?: number;
    },
  ) =>
    request<MultiSampleResult>(`/strategies/${id}/multi-backtest`, {
      method: "POST",
      body: JSON.stringify(body),
    }),

  // 策略组合即时回测（regime rotation）：落一条 backtest run 并回轮动时间线/分体制贡献。
  comboPreview: (body: {
    members: ComboMemberSpec[];
    policy?: Partial<ComboPolicy>;
    instId: string;
    bar: string;
    limit?: number;
    startTs: number;
    endTs: number;
    initialCapital?: number;
    fees?: number;
    slippage?: number;
    leverage?: number;
  }) =>
    request<ComboPreviewResult>("/strategies/combo/preview", {
      method: "POST",
      body: JSON.stringify(body),
    }),

  run: (runId: number) => request<Run>(`/runs/${runId}`),
  trades: (runId: number, limit = 500) => request<Trade[]>(`/runs/${runId}/trades?limit=${limit}`),
  equity: (runId: number) => request<EquityPoint[]>(`/runs/${runId}/equity`),

  // 运行历史列表（F4）
  runsList: (filter?: {
    strategyId?: number;
    instId?: string;
    mode?: string;
    limit?: number;
    offset?: number;
  }) => {
    const params = new URLSearchParams();
    if (filter?.strategyId != null) params.set("strategyId", String(filter.strategyId));
    if (filter?.instId) params.set("instId", filter.instId);
    if (filter?.mode) params.set("mode", filter.mode);
    if (filter?.limit != null) params.set("limit", String(filter.limit));
    if (filter?.offset != null) params.set("offset", String(filter.offset));
    const qs = params.toString();
    return request<Run[]>(`/runs${qs ? `?${qs}` : ""}`);
  },

  paperStart: (body: {
    strategyId: number;
    instId: string;
    bar: string;
    initialCapital?: number;
    source?: "live" | "replay";
    speed?: number;
    replayBars?: number;
    priming?: boolean; // live 启动时预热回放建仓（warm-start，默认后端为 true）
    primingBars?: number; // 预热回看根数（默认后端 300）
  }) =>
    request<{ sessionId: string; runId: number; strategyKind?: StrategyKind }>("/paper/start", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  paperBatchStart: (body: {
    strategyIds: number[];
    instId: string;
    bar: string;
    initialCapital?: number;
    source?: "live" | "replay";
    speed?: number;
    replayBars?: number;
    priming?: boolean;
    primingBars?: number;
  }) =>
    request<PaperBatchStartResult>("/paper/batch/start", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  paperStop: (sessionId: string) =>
    request<Run>(`/paper/${sessionId}/stop`, { method: "POST" }),
  paperBatchStop: (groupId: string) =>
    request<PaperBatchStopResult>(`/paper/batch/${groupId}/stop`, { method: "POST" }),
  // 停止后端注册表中全部 running 会话，回收被孤儿会话占满的配额（停止全部并重试）。
  paperStopAll: () => request<PaperStopAllResult>("/paper/stop-all", { method: "POST" }),
  paperSessions: () => request<PaperSessionRow[]>("/paper/sessions"),
};
