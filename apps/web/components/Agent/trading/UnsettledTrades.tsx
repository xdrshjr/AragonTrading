"use client";

import { useEffect, useRef, useState } from "react";
import { agentTradingApi } from "@/lib/agentTradingApi";
import { environmentLabel, INTENT_LABELS,
  type TradeIntentView } from "@/lib/agentTradingTypes";
import { useAppStore } from "@/lib/store";
import TradeIntentCard from "./TradeIntentCard";

/** 全局只读恢复入口独立于新交易能力和当前会话，清空聊天后仍可达。 */
export default function UnsettledTrades() {
  const [items, setItems] = useState<TradeIntentView[]>([]);
  const [open, setOpen] = useState(false);
  const [selected, setSelected] = useState<TradeIntentView | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);
  const active = useRef(true);
  const request = useRef(0);

  async function refresh() {
    const version = ++request.current;
    try {
      const rows: TradeIntentView[] = [];
      let cursor: string | undefined;
      do {
        const page = await agentTradingApi.unsettled(cursor);
        if (!active.current || version !== request.current) return;
        rows.push(...page.items);
        cursor = page.nextCursor ?? undefined;
      } while (cursor);
      setItems(rows);
      setLoaded(true);
      setError(null);
    } catch {
      if (active.current && version === request.current) {
        setError("未结交易读取失败，请刷新；不能据此判断没有订单。");
      }
    }
  }
  useEffect(() => {
    active.current = true;
    void refresh();
    const timer = setInterval(() => {
      if (!document.hidden) void refresh();
    }, 5000);
    return () => { active.current = false; clearInterval(timer); };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  return <div className="min-w-0">
    <button type="button" onClick={() => { setOpen(!open); void refresh(); }}
      aria-expanded={open} className="rounded border border-[var(--border)] px-2 py-1">
      未结交易 {loaded ? items.length : "—"}</button>
    {open && <div className="mt-2 max-h-[50vh] space-y-2 overflow-y-auto">
      <p className="text-[var(--muted)]">所有会话 · 只读核对。未结期间暂不能开始其他交易。</p>
      {error && <p role="alert">{error}</p>}
      {loaded && !error && items.length === 0 && <p>当前没有未结交易。</p>}
      {items.map((intent) => <div key={intent.id}
        className="rounded-md border border-[var(--border)] p-2">
        <button type="button" className="w-full break-words text-left"
          onClick={() => setSelected(intent)}>
          {intent.account.label} · {environmentLabel(intent.account.environment)}
          {" · "}{intent.preview.instId} · {INTENT_LABELS[intent.status]}
        </button>
        <button type="button" className="mt-2 underline" onClick={() => {
          useAppStore.getState().setAgentConversationId(intent.conversationId);
          setSelected(null);
          setOpen(false);
        }}>返回原会话</button>
      </div>)}
      {selected && <TradeIntentCard key={selected.id} intent={selected} readOnly />}
      <button type="button" onClick={() => void refresh()} className="underline">
        刷新未结交易</button>
    </div>}
  </div>;
}
