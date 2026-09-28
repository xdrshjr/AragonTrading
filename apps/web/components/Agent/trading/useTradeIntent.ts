"use client";

import { useEffect, useRef, useState } from "react";
import { agentTradingApi } from "@/lib/agentTradingApi";
import { ApiRequestError } from "@/lib/api";
import { isUnsettled, type TradeIntentView } from "@/lib/agentTradingTypes";
import { useAppStore } from "@/lib/store";
import { useTradingContext } from "./TradingContext";

const attempted = new Set<string>();
function hasAttempted(key: string) {
  try { return attempted.has(key) || sessionStorage.getItem(key) === "1"; }
  catch { return attempted.has(key); }
}
function rememberAttempt(key: string) {
  attempted.add(key);
  try { sessionStorage.setItem(key, "1"); }
  catch { /* 禁止存储时仍由内存记录和服务端至多一次约束保护。 */ }
}

/** 每张卡先 GET 再开放操作；写失败永久降为查询，轮询永不调用 execute。 */
export function useTradeIntent(snapshot: TradeIntentView, readOnly = false) {
  const context = useTradingContext();
  const attemptKey = `agent-trade/${snapshot.conversationId}/${snapshot.id}`;
  const cid = useAppStore((s) => s.agentConversationId);
  const accountMode = useAppStore((s) => s.accountMode);
  const revision = useAppStore((s) => s.agentTradingBindingRevision);
  const [local, setLocal] = useState<TradeIntentView>(snapshot);
  const [verified, setVerified] = useState(false);
  const [busy, setBusy] = useState(false);
  const [uncertain, setUncertain] = useState(() => hasAttempted(attemptKey));
  const [error, setError] = useState<string | null>(null);
  const [errorCode, setErrorCode] = useState<string | null>(null);
  const elementRef = useRef<HTMLElement>(null);
  const writing = useRef(false);
  const submissionError = useRef<string | null>(null);
  const epoch = useRef(0);
  const currentCid = useRef(cid);
  currentCid.current = cid;
  const intent = context?.intents[snapshot.id] ?? local;
  const latest = useRef(intent);
  latest.current = intent;
  const acceptRef = useRef(context?.accept);
  acceptRef.current = context?.accept;

  async function refresh() {
    const scope = epoch.current;
    try {
      const fresh = await agentTradingApi.intent(
        snapshot.conversationId, snapshot.id,
      );
      if (scope !== epoch.current || currentCid.current !== cid) return;
      if (useAppStore.getState().agentConversationId !== cid) return;
      if (fresh.conversationId !== snapshot.conversationId) return;
      setLocal((previous) => fresh.version >= previous.version
        ? fresh : previous);
      acceptRef.current?.(fresh);
      setVerified(true);
      setError(submissionError.current);
    } catch {
      if (scope === epoch.current && currentCid.current === cid) {
        setError("状态查询失败，保留上次结果；请查询状态，不要重复下单。");
      }
    }
  }

  useEffect(() => {
    epoch.current += 1;
    setLocal(snapshot);
    setVerified(false);
    setUncertain(hasAttempted(attemptKey));
    let visible = true;
    let polling = false;
    const query = async () => {
      if (polling || !visible || document.visibilityState === "hidden") return;
      polling = true;
      await refresh();
      polling = false;
    };
    const visibility = () => { if (!document.hidden) void query(); };
    document.addEventListener("visibilitychange", visibility);
    const observer = typeof IntersectionObserver === "undefined" ? null
      : new IntersectionObserver(([entry]) => {
        visible = entry.isIntersecting;
        if (visible) void query();
      });
    if (elementRef.current) observer?.observe(elementRef.current);
    void query();
    const timer = setInterval(() => {
      if (latest.current.status === "prepared"
        || isUnsettled(latest.current.status)) void query();
    }, 2000);
    return () => {
      epoch.current += 1;
      observer?.disconnect();
      clearInterval(timer);
      document.removeEventListener("visibilitychange", visibility);
    };
  }, [snapshot.id, snapshot.conversationId, cid, revision]); // eslint-disable-line react-hooks/exhaustive-deps

  const binding = context?.binding;
  const canExecute = accountMode === "live"
    && !readOnly && verified && !busy && !uncertain
    && !context?.pending && context?.capabilities?.executionEnabled === true
    && context.bindingLoaded && intent.canExecute && intent.status === "prepared"
    && binding?.version === intent.bindingVersion
    && binding.accountMode === "live" && binding.accountId === intent.account.id
    && binding.environment === intent.account.environment
    && intent.conversationId === cid;

  async function act(action: "execute" | "dismiss") {
    if (useAppStore.getState().agentConversationId !== cid) return;
    if (useAppStore.getState().agentTradingBindingPending) return;
    if (action === "execute"
      && useAppStore.getState().accountMode !== "live") return;
    if (writing.current || !verified || readOnly) return;
    if (action === "execute" && hasAttempted(attemptKey)) return;
    if (action === "execute" && !canExecute) return;
    if (action === "dismiss" && (intent.status !== "prepared"
      || intent.conversationId !== cid || context?.pending || uncertain)) return;
    if (context && !context.acquire(intent.id)) return;
    writing.current = true;
    submissionError.current = null;
    setBusy(true);
    const scope = epoch.current;
    if (action === "execute") {
      rememberAttempt(attemptKey);
      setUncertain(true);
    }
    try {
      const fresh = action === "execute"
        ? await agentTradingApi.execute(intent.conversationId, intent.id, {
          version: intent.version, previewHash: intent.previewHash,
        })
        : await agentTradingApi.dismiss(
          intent.conversationId, intent.id, intent.version,
        );
      if (scope !== epoch.current || currentCid.current !== cid) return;
      setLocal((previous) => fresh.version >= previous.version ? fresh : previous);
      acceptRef.current?.(fresh);
      setError(null);
    } catch (failure) {
      if (scope !== epoch.current || currentCid.current !== cid) return;
      await refresh();
      if (scope !== epoch.current || currentCid.current !== cid) return;
      setErrorCode(failure instanceof ApiRequestError ? failure.code : null);
      const message = failure instanceof ApiRequestError
        ? `${failure.message} 请查询当前状态；需要重新预览时发送新消息。`
        : action === "execute"
        ? "提交结果待核实，请查询状态。不要重复下单。"
        : "放弃失败，请查询最新状态。";
      submissionError.current = message;
      setError(message);
    } finally {
      writing.current = false;
      context?.release(intent.id);
      // 重连会提高查询轮次，但不应遗留已结束请求的占用状态。
      setBusy(false);
    }
  }
  return { intent, verified, busy, uncertain, error, errorCode, canExecute,
    refresh, act, elementRef };
}
