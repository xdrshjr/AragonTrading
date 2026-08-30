"use client";

import { useQuery } from "@tanstack/react-query";
import clsx from "clsx";
import { useMemo, useState } from "react";
import { Skeleton, Tabs } from "@/components/ui";
import { api } from "@/lib/api";
import {
  type FilteredInstruments,
  filterAndRankInstruments,
  inferInstType,
  type InstType,
  MAX_VISIBLE_ROWS,
  type QuoteFilter,
  resolveWatchlistRows,
} from "@/lib/instruments";
import { type InstSidebarTab, useAppStore } from "@/lib/store";
import type { Instrument } from "@/lib/types";
import InstrumentRow from "./InstrumentRow";
import WatchlistTab from "./WatchlistTab";

// 三页签标的侧边栏（instrument-sidebar-tabs §4）：自选 / 现货 / 合约。
// 各类型一次 REST + React Query 去重缓存；客户端模糊搜索 + 成交额降序 + 渲染上限；
// 自选跨类型归并解析；严守硬约束——取数失败显式错误态 + 重试，绝不伪造数据。
interface InstrumentSidebarProps {
  active: string; // 当前 instId
  onSelect: (instId: string) => void; // = store.setInst
  livePrice?: number | null; // 当前标的实时价（仅高亮行由 WS 覆盖）
  changePct?: number | null; // 当前标的区间涨跌幅（真实）
}

const SIDEBAR_TABS: { key: InstSidebarTab; label: string }[] = [
  { key: "watch", label: "自选" },
  { key: "spot", label: "现货" },
  { key: "swap", label: "合约" },
];

const SUBTYPE_OPTIONS: { key: "ALL" | InstType; label: string }[] = [
  { key: "ALL", label: "全部" },
  { key: "SWAP", label: "永续" },
  { key: "FUTURES", label: "交割" },
];

const QUOTE_OPTIONS: { key: QuoteFilter; label: string }[] = [
  { key: "ALL", label: "全部" },
  { key: "USDT", label: "USDT" },
  { key: "USDC", label: "USDC" },
  { key: "USD", label: "USD" },
];

function useInstrumentsQuery(instType: InstType, enabled: boolean) {
  return useQuery({
    queryKey: ["instruments", instType],
    queryFn: () => api.instruments(instType),
    staleTime: 5 * 60_000,
    enabled,
  });
}

function SearchBar({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  return (
    <div className="relative">
      <svg
        viewBox="0 0 24 24"
        aria-hidden="true"
        className="pointer-events-none absolute left-2 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-[var(--muted)]"
        fill="none"
        stroke="currentColor"
        strokeWidth={1.8}
        strokeLinecap="round"
      >
        <circle cx="11" cy="11" r="7" />
        <path d="M20 20l-3.2-3.2" />
      </svg>
      <input
        value={value}
        onChange={(e) => onChange(e.target.value)}
        aria-label="搜索标的"
        placeholder="搜索币种 / 合约，如 BTC、SOL-USDT-SWAP"
        className="w-full rounded-[8px] border border-[var(--border)] bg-[var(--surface)] py-1.5 pl-7 pr-7 text-xs text-[var(--text)] transition-colors placeholder:text-[var(--muted)] focus:border-[var(--accent)] focus:outline-none"
      />
      {value && (
        <button
          type="button"
          onClick={() => onChange("")}
          aria-label="清除搜索"
          className="absolute right-1.5 top-1/2 -translate-y-1/2 rounded p-0.5 text-[var(--muted)] hover:text-[var(--text)]"
        >
          <svg viewBox="0 0 24 24" className="h-3.5 w-3.5" fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round">
            <path d="M6 6l12 12M18 6L6 18" />
          </svg>
        </button>
      )}
    </div>
  );
}

function FilterChips<T extends string>({
  options,
  value,
  onChange,
}: {
  options: { key: T; label: string }[];
  value: T;
  onChange: (v: T) => void;
}) {
  return (
    <div data-nowrap="row" className="flex flex-wrap gap-1 whitespace-nowrap">
      {options.map((o) => (
        <button
          key={o.key}
          type="button"
          aria-pressed={value === o.key}
          onClick={() => onChange(o.key)}
          className={clsx(
            "shrink-0 rounded-full px-2 py-0.5 text-[10px] font-medium transition-colors",
            value === o.key
              ? "bg-[var(--accent-soft)] text-[var(--accent-ink)]"
              : "bg-[var(--surface-2)] text-[var(--muted)] hover:text-[var(--text)]",
          )}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

export default function InstrumentSidebar({
  active,
  onSelect,
  livePrice,
  changePct,
}: InstrumentSidebarProps) {
  const watchlist = useAppStore((s) => s.watchlist);
  const tab = useAppStore((s) => s.instSidebarTab);
  const setTab = useAppStore((s) => s.setInstSidebarTab);
  const toggleWatchlist = useAppStore((s) => s.toggleWatchlist);

  const [query, setQuery] = useState("");
  const [subtype, setSubtype] = useState<"ALL" | InstType>("ALL");
  const [quote, setQuote] = useState<QuoteFilter>("ALL");

  // 自选页签仅按自选项实际涉及的类型取数（RV-5），现货/合约页签按各自类型取数。
  const watchTypes = useMemo(() => new Set(watchlist.map(inferInstType)), [watchlist]);
  const needSpot = tab === "spot" || (tab === "watch" && watchTypes.has("SPOT"));
  const needSwap = tab === "swap" || (tab === "watch" && watchTypes.has("SWAP"));
  const needFutures = tab === "swap" || (tab === "watch" && watchTypes.has("FUTURES"));

  const spotQ = useInstrumentsQuery("SPOT", needSpot);
  const swapQ = useInstrumentsQuery("SWAP", needSwap);
  const futuresQ = useInstrumentsQuery("FUTURES", needFutures);

  const activeQueries = [
    needSpot ? spotQ : null,
    needSwap ? swapQ : null,
    needFutures ? futuresQ : null,
  ].filter(Boolean) as ReturnType<typeof useInstrumentsQuery>[];
  const isLoading = activeQueries.some((q) => q.isLoading);
  const errorMsg = activeQueries.find((q) => q.isError)?.error;
  const isError = activeQueries.some((q) => q.isError);

  const watchSet = useMemo(() => new Set(watchlist), [watchlist]);

  const view: FilteredInstruments = useMemo(() => {
    if (tab === "watch") {
      const byInstId = new Map<string, Instrument>();
      for (const list of [spotQ.data, swapQ.data, futuresQ.data]) {
        for (const it of list ?? []) byInstId.set(it.instId, it);
      }
      return filterAndRankInstruments(resolveWatchlistRows(watchlist, byInstId), { query });
    }
    if (tab === "spot") {
      return filterAndRankInstruments(spotQ.data, { query });
    }
    // 合约页签：SWAP∪FUTURES 归并，子类型默认「全部」横跨永续+交割（RV-3 / C-2）。
    const contracts = [...(swapQ.data ?? []), ...(futuresQ.data ?? [])];
    return filterAndRankInstruments(contracts, {
      query,
      quote,
      instTypes: subtype === "ALL" ? undefined : [subtype],
    });
  }, [tab, query, quote, subtype, watchlist, spotQ.data, swapQ.data, futuresQ.data]);

  const ready = !isLoading && !isError;
  // 自选页签改渲染上下分区容器（WatchlistTab）；现货 / 合约维持单列表（rows / empty）。
  const showWatch = ready && tab === "watch";
  const showRows = ready && tab !== "watch" && view.rows.length > 0;
  const showEmpty = ready && tab !== "watch" && view.rows.length === 0;

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div
        data-nowrap="row"
        className="shrink-0 space-y-2 whitespace-nowrap border-b border-[var(--border)] bg-[var(--surface)] px-2 pb-2 pt-2"
      >
        <Tabs tabs={SIDEBAR_TABS} active={tab} onChange={(k) => setTab(k as InstSidebarTab)} />
        <SearchBar value={query} onChange={setQuery} />
        {tab === "swap" && (
          <div className="space-y-1.5">
            <FilterChips options={SUBTYPE_OPTIONS} value={subtype} onChange={setSubtype} />
            <FilterChips options={QUOTE_OPTIONS} value={quote} onChange={setQuote} />
          </div>
        )}
      </div>

      <div className={clsx("min-h-0 flex-1", showWatch ? "flex flex-col" : "overflow-auto")}>
        {isLoading && (
          <div className="space-y-2 p-2">
            {Array.from({ length: 6 }).map((_, i) => (
              <Skeleton key={i} className="h-9 w-full rounded" />
            ))}
          </div>
        )}

        {isError && (
          <div className="flex flex-col items-start gap-2 whitespace-normal break-words p-3">
            <p className="text-[11px] font-medium text-[var(--short-ink)]">
              无法连接 OKX，请检查网络 / 代理。
            </p>
            {errorMsg instanceof Error && (
              <p className="text-[10px] text-[var(--muted)]">{errorMsg.message}</p>
            )}
            <button
              type="button"
              onClick={() => activeQueries.forEach((q) => q.refetch())}
              className="rounded-[8px] bg-[var(--surface-2)] px-2.5 py-1 text-[11px] font-medium text-[var(--text)] hover:bg-[var(--border)]"
            >
              重试
            </button>
          </div>
        )}

        {showWatch && (
          <WatchlistTab
            rows={view.rows}
            active={active}
            onSelect={onSelect}
            livePrice={livePrice}
            changePct={changePct}
          />
        )}

        {showEmpty && (
          <div className="flex flex-col items-center gap-1.5 px-3 py-8 text-center">
            <p className="whitespace-normal break-words text-[11px] text-[var(--muted)]">
              {query ? `未找到匹配「${query}」的标的` : "暂无标的"}
            </p>
          </div>
        )}

        {showRows && (
          <div className="flex flex-col py-1">
            {view.rows.map((inst) => (
              <InstrumentRow
                key={inst.instId}
                inst={inst}
                active={inst.instId === active}
                starred={watchSet.has(inst.instId)}
                livePrice={inst.instId === active ? livePrice : undefined}
                liveChangePct={inst.instId === active ? changePct : undefined}
                onSelect={onSelect}
                onToggleStar={toggleWatchlist}
              />
            ))}
            {view.truncated && (
              <p className="whitespace-normal break-words px-3 py-2 text-center text-[10px] text-[var(--muted)]">
                仅显示前 {MAX_VISIBLE_ROWS} 条（共 {view.total}），输入关键字以精确查找
              </p>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
