// AI 回测作业流（ai-auto-trading-backtest §2.6）：挂于 AiBacktestModal。
// 挂载/换 jobId 时 GET /ai-trade/backtest/{id} 重发现当前态（重连即回放），再开一条 /ws/ai-backtest/{id}，
// 事件按 type 路由进本地瞬态（phase/progress/decision/summary/trade/result/done/cancelled/error）。断线重连
// 由 ReconnectingWS 自动续连并重放 snapshot。返回单一视图供面板渲染（运行态 vs 完成态）。
import { useEffect, useReducer } from "react";

import { aiBacktestApi, aiBacktestWS } from "./aiBacktestApi";
import type {
  AiBacktestJobState,
  AiBacktestPhase,
  AiBacktestProgress,
  AiBacktestResult,
  AiBacktestSnapshot,
  AiBacktestStatus,
  AiBacktestSummary,
} from "./aiBacktestTypes";
import type { AiTradeDecision } from "./aiTradeTypes";
import type { Trade } from "./types";
import type { WsEvent } from "./ws";

const DECISIONS_MAX = 500; // 运行态本地决策日志上限（完成态从 result 全量渲染）
const TRADES_MAX = 2000;

export interface AiBacktestJobView {
  jobId: string;
  status: AiBacktestStatus | null;
  phase: AiBacktestPhase;
  progress: AiBacktestProgress | null;
  decisions: AiTradeDecision[];
  summaries: AiBacktestSummary[];
  trades: Trade[];
  equity: { ts: number; equity: number }[];
  result: AiBacktestResult | null;
  state: AiBacktestJobState;
  connected: boolean;
  error: { code: string; message: string } | null;
}

type Action =
  | { t: "reset"; jobId: string }
  | { t: "connected"; open: boolean }
  | { t: "seed"; status: AiBacktestStatus }
  | { t: "snapshot"; snap: AiBacktestSnapshot }
  | { t: "phase"; phase: AiBacktestPhase }
  | { t: "progress"; progress: AiBacktestProgress }
  | { t: "decision"; decision: AiTradeDecision }
  | { t: "summary"; summary: AiBacktestSummary }
  | { t: "trade"; trade: Trade }
  | { t: "result"; result: AiBacktestResult }
  | { t: "terminal"; state: AiBacktestJobState }
  | { t: "error"; code: string; message: string };

function initial(jobId: string): AiBacktestJobView {
  return {
    jobId,
    status: null,
    phase: "loading",
    progress: null,
    decisions: [],
    summaries: [],
    trades: [],
    equity: [],
    result: null,
    state: "running",
    connected: false,
    error: null,
  };
}

function reducer(state: AiBacktestJobView, action: Action): AiBacktestJobView {
  switch (action.t) {
    case "reset":
      return initial(action.jobId);
    case "connected":
      return { ...state, connected: action.open };
    case "seed":
      return {
        ...state,
        status: action.status,
        phase: action.status.phase,
        state: action.status.state,
        result: action.status.result ?? state.result,
      };
    case "snapshot":
      return {
        ...state,
        status: action.snap.job,
        phase: action.snap.job.phase,
        state: action.snap.job.state,
        decisions: action.snap.decisions ?? [],
        summaries: action.snap.summaries ?? [],
        equity: action.snap.equity ?? [],
        result: action.snap.job.result ?? state.result,
      };
    case "phase":
      return { ...state, phase: action.phase };
    case "progress":
      return {
        ...state,
        progress: action.progress,
        equity:
          action.progress.equity != null
            ? [...state.equity, { ts: action.progress.ts, equity: action.progress.equity }].slice(-2000)
            : state.equity,
      };
    case "decision":
      return { ...state, decisions: [...state.decisions, action.decision].slice(-DECISIONS_MAX) };
    case "summary":
      return { ...state, summaries: [...state.summaries, action.summary] };
    case "trade":
      return { ...state, trades: [...state.trades, action.trade].slice(-TRADES_MAX) };
    case "result":
      return { ...state, result: action.result };
    case "terminal":
      return { ...state, state: action.state };
    case "error":
      return { ...state, error: { code: action.code, message: action.message }, state: "failed" };
    default:
      return state;
  }
}

function route(event: WsEvent, dispatch: React.Dispatch<Action>) {
  switch (event.type) {
    case "snapshot":
      dispatch({ t: "snapshot", snap: event.data as AiBacktestSnapshot });
      return;
    case "phase":
      dispatch({ t: "phase", phase: (event.data as { phase: AiBacktestPhase }).phase });
      return;
    case "progress":
      dispatch({ t: "progress", progress: event.data as AiBacktestProgress });
      return;
    case "decision":
      dispatch({ t: "decision", decision: event.data as AiTradeDecision });
      return;
    case "summary":
      dispatch({ t: "summary", summary: event.data as AiBacktestSummary });
      return;
    case "trade":
      dispatch({ t: "trade", trade: event.data as Trade });
      return;
    case "result":
      dispatch({ t: "result", result: event.data as AiBacktestResult });
      return;
    case "done":
      dispatch({ t: "terminal", state: "completed" });
      return;
    case "cancelled":
      dispatch({ t: "terminal", state: "cancelled" });
      return;
    case "error": {
      const data = event.data as { code?: string; message?: string };
      dispatch({ t: "error", code: data.code ?? "AI_BACKTEST_FAILED", message: data.message ?? "回测失败" });
      return;
    }
    // ping：心跳，忽略
  }
}

export function useAiBacktestJob(jobId: string | null): AiBacktestJobView | null {
  const [view, dispatch] = useReducer(reducer, jobId ? initial(jobId) : initial(""));

  useEffect(() => {
    if (!jobId) return;
    dispatch({ t: "reset", jobId });
    let cancelled = false;
    // 重发现：GET 当前态（重连即回放 result；快作业先于 WS 结束也能立刻显示）。
    aiBacktestApi
      .status(jobId)
      .then((res) => {
        if (!cancelled) dispatch({ t: "seed", status: res.job });
      })
      .catch(() => {});
    const ws = aiBacktestWS(
      jobId,
      (event) => {
        if (!cancelled) route(event, dispatch);
      },
      (open) => {
        if (!cancelled) dispatch({ t: "connected", open });
      },
    );
    ws.connect();
    return () => {
      cancelled = true;
      ws.close();
    };
  }, [jobId]);

  return jobId ? view : null;
}
