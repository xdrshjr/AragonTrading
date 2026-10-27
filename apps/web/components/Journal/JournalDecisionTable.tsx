"use client";

import clsx from "clsx";
import { useCallback, useEffect, useRef } from "react";
import { Badge, Button, Skeleton } from "@/components/ui";
import {
  actionLabel,
  degradeLabel,
  formatLocal,
  formatNumber,
  formatUtc,
  linkConfidenceGlyph,
  linkConfidenceLabel,
  linkConfidenceTone,
  triggerLabel,
} from "@/lib/journal";
import type { DecisionBrief } from "@/lib/journalTypes";

/**
 * 决策轮次表（ai-trade-audit-journal §2.6.3）。
 *
 * 表格契约是硬性的：容器 `overflow-x-auto` + 表自身 `min-w-[…]` + 每个单元格 `whitespace-nowrap`。
 * 这不是发明——仓库 `Factor` / `Settings` 下的 8 张表早已是这个写法，本轮是搬运。
 *
 * 键盘可达：行 `tabIndex=0` + `aria-selected`，`↑/↓` 移动选中并滚动到可视区。分页「加载更多」
 * 是**真按钮**而不是无限滚动——审计场景需要可预期的位置。
 *
 * 空态要说清**为什么空**（总闸关 / 采集档为 minimal / 该会话尚无决策 / 过滤太窄），
 * 而不是一句放之四海而皆准的「暂无数据」。
 */
export interface JournalDecisionTableProps {
  rows: DecisionBrief[];
  selectedId: number | null;
  onSelect: (row: DecisionBrief) => void;
  loading: boolean;
  error: string;
  onRetry: () => void;
  hasMore: boolean;
  onLoadMore: () => void;
  /** 空态原因（由视图壳按当前门控 / 采集档 / 过滤条件解出，组件不重算）。 */
  emptyReason: string;
}

export default function JournalDecisionTable({
  rows,
  selectedId,
  onSelect,
  loading,
  error,
  onRetry,
  hasMore,
  onLoadMore,
  emptyReason,
}: JournalDecisionTableProps) {
  const bodyRef = useRef<HTMLTableSectionElement>(null);

  const move = useCallback(
    (delta: number) => {
      if (!rows.length) return;
      const index = rows.findIndex((row) => row.id === selectedId);
      const next = Math.min(rows.length - 1, Math.max(0, (index < 0 ? 0 : index) + delta));
      onSelect(rows[next]);
    },
    [rows, selectedId, onSelect],
  );

  // 选中行滚动到可视区：连续上下走时列表必须跟着动，否则「↑↓ 选择」在长列表上等于没有。
  useEffect(() => {
    if (selectedId == null) return;
    const node = bodyRef.current?.querySelector<HTMLTableRowElement>(
      `[data-row-id="${selectedId}"]`,
    );
    // `scrollIntoView` 在 jsdom 里不存在（也可能被某些内嵌浏览器裁掉）。它是**渐进增强**，
    // 不是契约：拿不到就不滚，绝不让一个可有可无的体验优化把整张表打崩。
    node?.scrollIntoView?.({ block: "nearest" });
  }, [selectedId, rows]);

  if (error) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-2 p-6 text-xs">
        <p className="text-[var(--short-ink)]">载入决策留痕失败：{error}</p>
        <Button size="sm" variant="ghost" onClick={onRetry}>
          重试
        </Button>
      </div>
    );
  }

  if (loading && !rows.length) {
    return (
      <div className="space-y-2 p-3">
        {[0, 1, 2, 3, 4, 5].map((i) => (
          <Skeleton key={i} className="h-7 w-full" />
        ))}
      </div>
    );
  }

  if (!rows.length) {
    return (
      <div className="flex h-full items-center justify-center p-6 text-center text-xs text-[var(--muted)]">
        {emptyReason}
      </div>
    );
  }

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="min-w-0 flex-1 overflow-x-auto overflow-y-auto">
        <table className="w-full min-w-[880px] text-xs">
          <thead className="sticky top-0 z-10 bg-[var(--surface-2)] text-[var(--muted)]">
            <tr>
              {[
                "时间(本地)",
                "时间(UTC)",
                "轮次",
                "标的",
                "唤醒",
                "模型动作",
                "最终动作",
                "仓位",
                "置信",
                "降级",
                "成交",
              ].map((label) => (
                <th
                  key={label}
                  scope="col"
                  className="whitespace-nowrap px-2 py-1.5 text-left font-medium"
                >
                  {label}
                </th>
              ))}
            </tr>
          </thead>
          <tbody ref={bodyRef} onKeyDown={(e) => {
            if (e.key === "ArrowDown") {
              e.preventDefault();
              move(1);
            } else if (e.key === "ArrowUp") {
              e.preventDefault();
              move(-1);
            }
          }}>
            {rows.map((row) => {
              const selected = row.id === selectedId;
              const rewritten = row.finalAction !== row.action;
              return (
                <tr
                  key={row.id}
                  data-row-id={row.id}
                  tabIndex={0}
                  aria-selected={selected}
                  onClick={() => onSelect(row)}
                  onFocus={() => onSelect(row)}
                  className={clsx(
                    "cursor-pointer border-b border-[var(--border)] outline-none",
                    selected
                      ? "bg-[var(--accent-soft)] text-[var(--accent-ink)]"
                      : "hover:bg-[var(--surface-2)] focus-visible:bg-[var(--surface-2)]",
                  )}
                >
                  <td className="whitespace-nowrap px-2 py-1.5 tabular">
                    {formatLocal(row.wallTs || row.ts)}
                  </td>
                  <td className="whitespace-nowrap px-2 py-1.5 tabular text-[var(--muted)]">
                    {formatUtc(row.ts)}
                  </td>
                  <td className="whitespace-nowrap px-2 py-1.5 tabular">{row.roundNo}</td>
                  <td className="whitespace-nowrap px-2 py-1.5">{row.instId}</td>
                  <td className="whitespace-nowrap px-2 py-1.5">{triggerLabel(row.trigger)}</td>
                  <td className="whitespace-nowrap px-2 py-1.5">{actionLabel(row.action)}</td>
                  <td className="whitespace-nowrap px-2 py-1.5">
                    {actionLabel(row.finalAction)}
                    {rewritten && (
                      <span className="ml-1 text-[10px] text-[var(--muted)]">（被改写）</span>
                    )}
                  </td>
                  <td className="whitespace-nowrap px-2 py-1.5 tabular">
                    {row.sizeFraction == null ? "—" : formatNumber(row.sizeFraction, 2)}
                  </td>
                  <td className="whitespace-nowrap px-2 py-1.5 tabular">
                    {row.confidence == null ? "—" : formatNumber(row.confidence, 2)}
                  </td>
                  <td className="whitespace-nowrap px-2 py-1.5 text-[var(--muted)]">
                    {degradeLabel(row.degradeReason) || "—"}
                  </td>
                  <td className="whitespace-nowrap px-2 py-1.5">
                    {row.executed ? (
                      <Badge tone={linkConfidenceTone(row.linkConfidence)}>
                        {linkConfidenceGlyph(row.linkConfidence)}{" "}
                        {linkConfidenceLabel(row.linkConfidence)}
                      </Badge>
                    ) : (
                      <span className="text-[var(--muted)]">未下单</span>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <div className="shrink-0 border-t border-[var(--border)] p-2 text-center">
        {hasMore ? (
          <Button size="sm" variant="ghost" onClick={onLoadMore} disabled={loading}>
            {loading ? "载入中…" : "加载更多"}
          </Button>
        ) : (
          <span className="text-[11px] text-[var(--muted)]">已到本次筛选的末尾</span>
        )}
      </div>
    </div>
  );
}
