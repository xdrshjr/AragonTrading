// AI 自动交易运行期流（ai-auto-trading §2.7）：挂载于 page.tsx。
// 1) 挂载 / 切到实盘时 GET /ai-trade/status 重发现孤儿会话（renderer 刷新后后端会话仍在跑）；
// 2) 对活跃会话开一条 /ws/ai-trade/{sessionId}，事件路由进 store（含 decision/summary）。
import { useEffect } from "react";
import { aiTradeApi, aiTradeWS } from "./aiTradeApi";
import type {
  AiTradeDecision,
  AiTradeSessionRow,
  AiTradeSnapshot,
  AiTradeStatusEvent,
} from "./aiTradeTypes";
import { useAppStore } from "./store";
import type { PaperPositionEvent, Trade } from "./types";
import type { WsEvent } from "./ws";

export function useAiTradeStream() {
  const accountMode = useAppStore((s) => s.accountMode);
  const aiTradeSessionId = useAppStore((s) => s.aiTradeSessionId);
  const setAiTradeSessionId = useAppStore((s) => s.setAiTradeSessionId);
  const setAiTradeSession = useAppStore((s) => s.setAiTradeSession);
  const patchAiTradeSession = useAppStore((s) => s.patchAiTradeSession);
  const setAiTradeTrades = useAppStore((s) => s.setAiTradeTrades);
  const appendAiTradeTrade = useAppStore((s) => s.appendAiTradeTrade);
  const setAiTradePosition = useAppStore((s) => s.setAiTradePosition);
  const pushAiTradeDecision = useAppStore((s) => s.pushAiTradeDecision);
  const setAiTradeDecisions = useAppStore((s) => s.setAiTradeDecisions);
  const setAiTradeSummary = useAppStore((s) => s.setAiTradeSummary);

  // 重发现：实盘模式下拉活跃 AI 会话，重建瞬态运行期态（已有同会话本地态则不覆盖成交流）。
  useEffect(() => {
    if (accountMode !== "live") return;
    let cancelled = false;
    aiTradeApi
      .status()
      .then((res) => {
        if (cancelled || !res.session) return;
        if (useAppStore.getState().aiTradeSessionId === res.session.sessionId) return;
        setAiTradeSessionId(res.session.sessionId);
        setAiTradeSession(res.session);
        setAiTradeSummary(res.session.contextSummary ?? null);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [accountMode, setAiTradeSessionId, setAiTradeSession, setAiTradeSummary]);

  // 会话 WS：事件路由进 store（snapshot/trade/position/status/decision/summary）。
  useEffect(() => {
    if (!aiTradeSessionId) return;
    const ws = aiTradeWS(aiTradeSessionId, (event: WsEvent) =>
      routeEvent(event, {
        patchAiTradeSession,
        setAiTradeTrades,
        appendAiTradeTrade,
        setAiTradePosition,
        pushAiTradeDecision,
        setAiTradeDecisions,
        setAiTradeSummary,
      }),
    );
    ws.connect();
    return () => ws.close();
  }, [
    aiTradeSessionId,
    patchAiTradeSession,
    setAiTradeTrades,
    appendAiTradeTrade,
    setAiTradePosition,
    pushAiTradeDecision,
    setAiTradeDecisions,
    setAiTradeSummary,
  ]);
}

interface AiTradeSetters {
  patchAiTradeSession: (patch: Partial<AiTradeSessionRow>) => void;
  setAiTradeTrades: (trades: Trade[]) => void;
  appendAiTradeTrade: (trade: Trade) => void;
  setAiTradePosition: (pos: PaperPositionEvent | null) => void;
  pushAiTradeDecision: (decision: AiTradeDecision) => void;
  setAiTradeDecisions: (decisions: AiTradeDecision[]) => void;
  setAiTradeSummary: (summary: string | null) => void;
}

function routeEvent(event: WsEvent, setters: AiTradeSetters) {
  switch (event.type) {
    case "snapshot": {
      const snap = event.data as AiTradeSnapshot;
      setters.setAiTradeTrades(snap.trades ?? []);
      setters.setAiTradePosition((snap.position as PaperPositionEvent) ?? null);
      setters.setAiTradeDecisions(snap.decisions ?? []);
      setters.setAiTradeSummary(snap.contextSummary ?? null);
      if (snap.status) setters.patchAiTradeSession(snap.status as Partial<AiTradeSessionRow>);
      return;
    }
    case "trade":
      setters.appendAiTradeTrade(event.data as Trade);
      return;
    case "position":
      setters.setAiTradePosition(event.data as PaperPositionEvent);
      return;
    case "status":
      setters.patchAiTradeSession(event.data as AiTradeStatusEvent as Partial<AiTradeSessionRow>);
      return;
    case "decision": {
      const decision = event.data as AiTradeDecision;
      setters.pushAiTradeDecision(decision);
      setters.patchAiTradeSession({ lastDecision: decision });
      return;
    }
    case "summary": {
      const summary = (event.data as { summary: string }).summary;
      setters.setAiTradeSummary(summary);
      setters.patchAiTradeSession({ contextSummary: summary });
      return;
    }
    // equity / candle / error：净值由面板派生，K 线由市场 WS 兜底，error 由 status.message 呈现。
  }
}
