// AI 自动交易「分组」运行期流（ai-trade-multi-symbol-and-timeframe §4.2）：挂载于 page.tsx。
// 1) 挂载 / 切到实盘时 GET /ai-trade/status 重发现孤儿分组（renderer 刷新后后端会话仍在跑）；
// 2) 对分组每个成员各开一条 /ws/ai-trade/{sessionId}，事件按 instId 路由进 store.aiTradeGroup.members
//    （snapshot/status/trade/position/decision/summary/agent_trace 各归成员槽）。镜像 useLiveAutoGroupStream。
import { useEffect } from "react";
import { aiTradeApi, aiTradeWS } from "./aiTradeApi";
import type {
  AiTradeDecision,
  AiTradeGroupRow,
  AiTradeGroupState,
  AiTradeRoundTrace,
  AiTradeSessionRow,
  AiTradeSnapshot,
  AiTradeStatusEvent,
  AiTradeTraceEvent,
} from "./aiTradeTypes";
import { useAppStore } from "./store";
import type { PaperPositionEvent, Trade } from "./types";
import type { WsEvent } from "./ws";

// 成员标识分隔符（instId 仅含字母数字与「-」、sessionId 为 hex，两者均不含「@@」，可安全拼接 / 拆分）。
const SEP = "@@";

export function groupStateFromStatus(row: AiTradeGroupRow): AiTradeGroupState {
  const members: AiTradeGroupState["members"] = {};
  for (const m of row.members) {
    members[m.instId] = {
      instId: m.instId,
      sessionId: m.sessionId,
      allocationPct: m.allocationPct,
      session: m,
      trades: [],
      position: null,
      decisions: m.lastDecision ? [m.lastDecision] : [],
      traces: [],
      summary: m.contextSummary ?? null,
    };
  }
  return {
    groupId: row.groupId,
    bar: row.bar,
    strategyIds: row.strategyIds,
    strategyNames: row.strategyNames,
    totalBudgetUsd: row.totalBudgetUsd,
    leverage: row.leverage,
    contextRefreshRounds: row.contextRefreshRounds,
    wakeIntervalMinutes: row.wakeIntervalMinutes,   // wake-interval：供面板显示「唤醒周期 N 分钟」
    state: "running",
    startedTs: row.createdTs,
    members,
  };
}

export function useAiTradeGroupStream() {
  const accountMode = useAppStore((s) => s.accountMode);
  const aiTradeGroup = useAppStore((s) => s.aiTradeGroup);
  const setAiTradeGroup = useAppStore((s) => s.setAiTradeGroup);
  const patchAiTradeMemberSession = useAppStore((s) => s.patchAiTradeMemberSession);
  const patchAiTradeMemberStatus = useAppStore((s) => s.patchAiTradeMemberStatus);
  const setAiTradeMemberTrades = useAppStore((s) => s.setAiTradeMemberTrades);
  const appendAiTradeMemberTrade = useAppStore((s) => s.appendAiTradeMemberTrade);
  const setAiTradeMemberPosition = useAppStore((s) => s.setAiTradeMemberPosition);
  const pushAiTradeMemberDecision = useAppStore((s) => s.pushAiTradeMemberDecision);
  const setAiTradeMemberDecisions = useAppStore((s) => s.setAiTradeMemberDecisions);
  const setAiTradeMemberTraces = useAppStore((s) => s.setAiTradeMemberTraces);
  const upsertAiTradeMemberTracePhase = useAppStore((s) => s.upsertAiTradeMemberTracePhase);
  const setAiTradeMemberSummary = useAppStore((s) => s.setAiTradeMemberSummary);

  // 重发现：实盘模式下拉活跃分组，重建瞬态运行期态（已有同组本地态则不覆盖成交流，仅在缺失时重建）。
  useEffect(() => {
    if (accountMode !== "live") return;
    let cancelled = false;
    aiTradeApi
      .status()
      .then((res) => {
        if (cancelled || !res.group) return;
        const current = useAppStore.getState().aiTradeGroup;
        if (current?.groupId === res.group.groupId) return;
        setAiTradeGroup(groupStateFromStatus(res.group));
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [accountMode, setAiTradeGroup]);

  // 成员集合稳定键：成员增删 / 重启即重建全部 WS（值变才触发，避免每次渲染重连）。
  const memberKey = aiTradeGroup
    ? Object.values(aiTradeGroup.members)
        .map((m) => `${m.instId}${SEP}${m.sessionId}`)
        .sort()
        .join(",")
    : "";

  useEffect(() => {
    if (!memberKey) return;
    const pairs = memberKey.split(",").map((entry) => {
      const [instId, sessionId] = entry.split(SEP);
      return { instId, sessionId };
    });
    const clients = pairs.map(({ instId, sessionId }) => {
      const ws = aiTradeWS(sessionId, (event: WsEvent) =>
        routeMemberEvent(instId, event, {
          patchAiTradeMemberSession,
          patchAiTradeMemberStatus,
          setAiTradeMemberTrades,
          appendAiTradeMemberTrade,
          setAiTradeMemberPosition,
          pushAiTradeMemberDecision,
          setAiTradeMemberDecisions,
          setAiTradeMemberTraces,
          upsertAiTradeMemberTracePhase,
          setAiTradeMemberSummary,
        }),
      );
      ws.connect();
      return ws;
    });
    return () => clients.forEach((c) => c.close());
  }, [
    memberKey,
    patchAiTradeMemberSession,
    patchAiTradeMemberStatus,
    setAiTradeMemberTrades,
    appendAiTradeMemberTrade,
    setAiTradeMemberPosition,
    pushAiTradeMemberDecision,
    setAiTradeMemberDecisions,
    setAiTradeMemberTraces,
    upsertAiTradeMemberTracePhase,
    setAiTradeMemberSummary,
  ]);
}

interface MemberSetters {
  patchAiTradeMemberSession: (instId: string, patch: Partial<AiTradeSessionRow>) => void;
  patchAiTradeMemberStatus: (instId: string, status: AiTradeStatusEvent) => void;
  setAiTradeMemberTrades: (instId: string, trades: Trade[]) => void;
  appendAiTradeMemberTrade: (instId: string, trade: Trade) => void;
  setAiTradeMemberPosition: (instId: string, pos: PaperPositionEvent | null) => void;
  pushAiTradeMemberDecision: (instId: string, decision: AiTradeDecision) => void;
  setAiTradeMemberDecisions: (instId: string, decisions: AiTradeDecision[]) => void;
  setAiTradeMemberTraces: (instId: string, traces: AiTradeRoundTrace[]) => void;
  upsertAiTradeMemberTracePhase: (instId: string, evt: AiTradeTraceEvent) => void;
  setAiTradeMemberSummary: (instId: string, summary: string | null) => void;
}

// 单成员 WS 事件 → store.aiTradeGroup.members[instId]（事件协议与单会话同构，按 instId 路由）。
function routeMemberEvent(instId: string, event: WsEvent, setters: MemberSetters) {
  switch (event.type) {
    case "snapshot": {
      const snap = event.data as AiTradeSnapshot;
      setters.setAiTradeMemberTrades(instId, snap.trades ?? []);
      setters.setAiTradeMemberPosition(instId, (snap.position as PaperPositionEvent) ?? null);
      setters.setAiTradeMemberDecisions(instId, snap.decisions ?? []);
      setters.setAiTradeMemberTraces(instId, snap.agentTraces ?? []);
      setters.setAiTradeMemberSummary(instId, snap.contextSummary ?? null);
      if (snap.status) setters.patchAiTradeMemberStatus(instId, snap.status);
      return;
    }
    case "agent_trace":
      setters.upsertAiTradeMemberTracePhase(instId, event.data as AiTradeTraceEvent);
      return;
    case "trade":
      setters.appendAiTradeMemberTrade(instId, event.data as Trade);
      return;
    case "position":
      setters.setAiTradeMemberPosition(instId, event.data as PaperPositionEvent);
      return;
    case "status":
      setters.patchAiTradeMemberStatus(instId, event.data as AiTradeStatusEvent);
      return;
    case "decision": {
      const decision = event.data as AiTradeDecision;
      setters.pushAiTradeMemberDecision(instId, decision);
      setters.patchAiTradeMemberSession(instId, { lastDecision: decision });
      return;
    }
    case "summary": {
      const summary = (event.data as { summary: string }).summary;
      setters.setAiTradeMemberSummary(instId, summary);
      setters.patchAiTradeMemberSession(instId, { contextSummary: summary });
      return;
    }
    // equity / candle / error：净值由面板派生，K 线由市场 WS 兜底，error 由 status.message 呈现。
  }
}
