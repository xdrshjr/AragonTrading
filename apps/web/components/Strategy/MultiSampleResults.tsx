"use client";

import { useQuery } from "@tanstack/react-query";
import clsx from "clsx";
import { useEffect, useRef, useState } from "react";
import { Button, Skeleton } from "@/components/ui";
import { api } from "@/lib/api";
import { downloadCsv, equityToCsv } from "@/lib/export";
import { colorForReturn, fmtPct } from "@/lib/format";
import { multiSampleTabTone } from "@/lib/multiSample";
import type { MultiSampleItem, MultiSampleResult } from "@/lib/types";
import MetricsCards from "./MetricsCards";

/**
 * 多样本回测结果区（multi-sample-backtest §6.2）：按标的分页签呈现各自绩效卡片 / 覆盖横幅 / 净值导出。
 *
 * 自绘横向可滚动 tab 条（不复用受限的 `Tabs` 原语，见 ui/index.tsx:353「R-2」），并补回 a11y：
 * `role="tablist"/tab/tabpanel"` + `aria-selected` + ←/→ 方向键切换。错误标的展示错误卡片，
 * 单标的失败不影响其余标的展示。
 */
export default function MultiSampleResults({
  result,
  loading,
}: {
  result: MultiSampleResult | null;
  loading?: boolean;
}) {
  const items = result?.items ?? [];
  const [activeInstId, setActiveInstId] = useState<string | null>(null);

  // 结果变化时把激活页签对齐到首个标的（结果替换 / 标的集合变化都安全重置）。
  useEffect(() => {
    if (items.length === 0) {
      setActiveInstId(null);
      return;
    }
    setActiveInstId((prev) =>
      prev && items.some((it) => it.instId === prev) ? prev : items[0].instId,
    );
  }, [items]);

  if (loading && !result) {
    return (
      <div className="flex flex-col gap-2">
        <div className="flex gap-2">
          {Array.from({ length: 3 }).map((_, i) => (
            <Skeleton key={i} className="h-7 w-24" />
          ))}
        </div>
        <MetricsCards loading />
      </div>
    );
  }

  if (items.length === 0) {
    return (
      <div className="rounded-[10px] border border-[var(--border)] bg-[var(--surface-2)] px-3 py-4 text-[11px] text-[var(--muted)]">
        暂无多样本回测结果。
      </div>
    );
  }

  const active = items.find((it) => it.instId === activeInstId) ?? items[0];

  return (
    <div className="flex flex-col gap-2">
      <TabBar items={items} activeInstId={active.instId} onSelect={setActiveInstId} />
      <ItemPanel item={active} />
    </div>
  );
}

function TabBar({
  items,
  activeInstId,
  onSelect,
}: {
  items: MultiSampleItem[];
  activeInstId: string;
  onSelect: (instId: string) => void;
}) {
  const ref = useRef<HTMLDivElement>(null);

  const onKeyDown = (e: React.KeyboardEvent<HTMLDivElement>) => {
    if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
    e.preventDefault();
    const idx = items.findIndex((it) => it.instId === activeInstId);
    const delta = e.key === "ArrowRight" ? 1 : -1;
    const nextIdx = (idx + delta + items.length) % items.length;
    onSelect(items[nextIdx].instId);
    ref.current?.querySelectorAll<HTMLButtonElement>('[role="tab"]')[nextIdx]?.focus();
  };

  return (
    <div
      ref={ref}
      role="tablist"
      aria-label="多样本回测标的"
      onKeyDown={onKeyDown}
      className="flex gap-1 overflow-x-auto border-b border-[var(--border)]"
    >
      {items.map((it) => {
        const isActive = it.instId === activeInstId;
        const tone = multiSampleTabTone(it);
        return (
          <button
            key={it.instId}
            role="tab"
            id={`ms-tab-${it.instId}`}
            aria-controls={`ms-panel-${it.instId}`}
            aria-selected={isActive}
            tabIndex={isActive ? 0 : -1}
            onClick={() => onSelect(it.instId)}
            className={clsx(
              "-mb-px flex shrink-0 items-center gap-1.5 border-b-2 px-3 py-1.5 text-xs font-medium transition-colors duration-150",
              isActive
                ? "border-[var(--accent)] text-[var(--text)]"
                : "border-transparent text-[var(--muted)] hover:text-[var(--text)]",
            )}
          >
            <span>{it.instId}</span>
            {it.status === "error" ? (
              <span className="text-[var(--short-ink)]" aria-hidden>
                !
              </span>
            ) : (
              <span
                className="tabular text-[10px]"
                style={{ color: tone === "neutral" ? "var(--muted)" : colorForReturn(it.metrics?.totalReturn ?? 0) }}
              >
                {fmtPct(it.metrics?.totalReturn)}
              </span>
            )}
          </button>
        );
      })}
    </div>
  );
}

function ItemPanel({ item }: { item: MultiSampleItem }) {
  return (
    <div
      role="tabpanel"
      id={`ms-panel-${item.instId}`}
      aria-labelledby={`ms-tab-${item.instId}`}
      className="flex flex-col gap-2"
    >
      {item.status === "error" ? <ErrorCard item={item} /> : <OkPanel item={item} />}
    </div>
  );
}

function OkPanel({ item }: { item: MultiSampleItem }) {
  // 该标的净值懒取（仅激活页签 + status==="ok" 时拉取），供「导出净值 CSV」。
  const equityQ = useQuery({
    queryKey: ["equity", item.runId],
    queryFn: () => api.equity(item.runId as number),
    enabled: Boolean(item.runId),
  });

  return (
    <>
      {item.truncated && (
        <div className="rounded-[8px] border border-[var(--border)] bg-[var(--surface-2)] px-3 py-2 text-[11px] text-[var(--muted)]">
          ⚠ 区间过长 / 历史不足，仅覆盖最近 {item.effectiveCandleCount} / {item.requestedBars} 根，收益率为部分区间结果。
        </div>
      )}
      <div className="flex justify-end">
        <Button
          variant="ghost"
          size="sm"
          disabled={!equityQ.data?.length}
          onClick={() =>
            item.runId &&
            equityQ.data &&
            downloadCsv(`aragon-equity-${item.instId}-run${item.runId}.csv`, equityToCsv(equityQ.data))
          }
        >
          导出净值 CSV
        </Button>
      </div>
      <MetricsCards metrics={item.metrics} />
    </>
  );
}

function ErrorCard({ item }: { item: MultiSampleItem }) {
  return (
    <div className="rounded-[10px] border border-[var(--short)] bg-[var(--surface)] px-3 py-3 text-[11px]">
      <div className="font-medium text-[var(--short-ink)]">该标的回测失败</div>
      {item.error && <div className="mt-1 break-words text-[var(--muted)]">{item.error}</div>}
      <div className="mt-1 text-[var(--muted)]">可缩小区间或确认标的有效后重试。</div>
    </div>
  );
}
