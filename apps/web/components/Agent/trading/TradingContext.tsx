"use client";

import { createContext, useContext, useEffect, useMemo, useRef,
  useState, type ReactNode } from "react";
import { agentTradingApi } from "@/lib/agentTradingApi";
import type { TradeIntentView, TradingBinding,
  TradingCapabilities } from "@/lib/agentTradingTypes";
import { useAppStore } from "@/lib/store";

interface TradingState {
  conversationId: string;
  capabilities: TradingCapabilities | null;
  binding: TradingBinding | null;
  intents: Record<string, TradeIntentView>;
  bindingLoaded: boolean;
  error: string | null;
}
interface TradingContextValue extends TradingState {
  pending: boolean;
  refresh: () => Promise<void>;
  selectAccount: (id: number | null) => Promise<void>;
  accept: (intent: TradeIntentView) => void;
  acquire: (id: string) => boolean;
  release: (id: string) => void;
}
const Context = createContext<TradingContextValue | null>(null);

/** 只合并当前会话中不倒退的版本；历史快照不能覆写 REST 真值。 */
export function mergeIntent(
  state: Record<string, TradeIntentView>, intent: TradeIntentView, cid: string,
): Record<string, TradeIntentView> {
  if (intent.conversationId !== cid) return state;
  if (state[intent.id] && state[intent.id].version > intent.version) return state;
  return { ...state, [intent.id]: intent };
}

/** 所有卡片共享绑定、版本缓存和写请求占用，同一次双击仅发一次请求。 */
export function TradingProvider({ children }: { children: ReactNode }) {
  const cid = useAppStore((s) => s.agentConversationId);
  const revision = useAppStore((s) => s.agentTradingBindingRevision);
  const pending = useAppStore((s) => s.agentTradingBindingPending);
  const [state, setState] = useState<TradingState>({ conversationId: cid,
    capabilities: null, binding: null, intents: {}, bindingLoaded: false,
    error: null });
  const current = useRef(cid);
  current.current = cid;
  const writes = useRef(new Set<string>());
  const generation = useRef(0);

  async function refresh() {
    const scope = ++generation.current;
    setState((previous) => ({ ...previous, bindingLoaded: false }));
    const results = await Promise.allSettled([
      agentTradingApi.capabilities(), agentTradingApi.binding(cid),
    ]);
    if (current.current !== cid || generation.current !== scope) return;
    const [caps, binding] = results;
    let restored = binding.status === "fulfilled" ? binding.value : null;
    let error = results.some((item) => item.status === "rejected")
      ? "账户信息读取失败，请刷新；未结交易仍可查询。" : null;
    const accountMode = useAppStore.getState().accountMode;
    if (restored && restored.accountMode !== accountMode) {
      try {
        // 返回旧会话不能复用其旧实盘资格；CAS 同时废弃旧模式预览。
        restored = await agentTradingApi.bind(cid, {
          accountId: restored.accountId, accountMode,
          expectedVersion: restored.version,
        });
      } catch (failure) {
        error = failure instanceof Error ? failure.message : "账户模式同步失败。";
      }
    }
    if (current.current !== cid || generation.current !== scope) return;
    setState((previous) => ({ ...previous, conversationId: cid,
      capabilities: caps.status === "fulfilled" ? caps.value : null,
      binding: restored,
      bindingLoaded: binding.status === "fulfilled" && !error
        && (!restored || restored.accountMode
          === useAppStore.getState().accountMode),
      error,
    }));
  }

  useEffect(() => {
    setState({ conversationId: cid, capabilities: null, binding: null,
      intents: {}, bindingLoaded: false, error: null });
    if (cid) void refresh();
    return () => { generation.current += 1; };
    // 每次切换会话清空执行资格，模式 CAS 后重新拉取绑定。
  }, [cid, revision]); // eslint-disable-line react-hooks/exhaustive-deps

  async function selectAccount(accountId: number | null) {
    const store = useAppStore.getState();
    if (store.agentConversationId !== cid) return;
    if (store.agentTradingBindingPending || !state.bindingLoaded) return;
    useAppStore.setState({ agentTradingBindingPending: true,
      agentTradingBindingError: null });
    try {
      const binding = await agentTradingApi.bind(cid, {
        accountId, accountMode: store.accountMode,
        expectedVersion: state.binding?.version ?? 0,
      });
      if (current.current !== cid) return;
      setState((previous) => ({ ...previous, binding,
        error: "账户已更新，旧预览已失效；已提交订单继续核对。" }));
    } catch (error) {
      if (current.current !== cid) return;
      setState((previous) => ({ ...previous,
        error: error instanceof Error ? error.message : "账户更新失败。" }));
    } finally {
      useAppStore.setState({ agentTradingBindingPending: false });
    }
  }

  const value = useMemo<TradingContextValue>(() => ({
    ...state, pending, refresh, selectAccount,
    accept: (intent) => {
      if (current.current !== cid) return;
      setState((previous) => ({ ...previous,
        intents: mergeIntent(previous.intents, intent, cid) }));
    },
    acquire: (id) => {
      const key = `${cid}/${id}`;
      if (writes.current.has(key)) return false;
      writes.current.add(key);
      return true;
    },
    release: (id) => { writes.current.delete(`${cid}/${id}`); },
  }), [state, pending, cid]); // eslint-disable-line react-hooks/exhaustive-deps
  return <Context.Provider value={value}>{children}</Context.Provider>;
}

/** 无 provider 的旧卡片保留只读能力，不推断执行资格。 */
export function useTradingContext() { return useContext(Context); }
