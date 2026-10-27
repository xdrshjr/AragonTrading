"use client";

import { Badge, Button, Skeleton } from "@/components/ui";
import {
  actionLabel,
  exitSourceLabel,
  formatLocal,
  formatNumber,
  linkConfidenceGlyph,
  linkConfidenceLabel,
  linkConfidenceTone,
} from "@/lib/journal";
import type { JournalFillRow } from "@/lib/journalTypes";

/**
 * 成交与订单表（ai-trade-audit-journal §2.6.3 / §4.5）。
 *
 * 每行带 `decisionRef`——**成交找得回决策**是审计的另一半（决策找得到成交只是一半）。
 * 点行即跳回产生它的那一轮决策。
 *
 * 表格契约同款：容器 `overflow-x-auto` + 表 `min-w-[…]` + 单元格 `whitespace-nowrap`。
 */
export default function JournalFillTable({
  rows,
  loading,
  error,
  onRetry,
  hasMore,
  onLoadMore,
  onOpenDecision,
}: {
  rows: JournalFillRow[];
  loading: boolean;
  error: string;
  onRetry: () => void;
  hasMore: boolean;
  onLoadMore: () => void;
  onOpenDecision: (decisionId: number) => void;
}) {
  if (error) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-2 p-6 text-xs">
        <p className="text-[var(--short-ink)]">载入成交与订单失败：{error}</p>
        <Button size="sm" variant="ghost" onClick={onRetry}>
          重试
        </Button>
      </div>
    );
  }
  if (loading && !rows.length) {
    return (
      <div className="space-y-2 p-3">
        {[0, 1, 2, 3].map((i) => (
          <Skeleton key={i} className="h-7 w-full" />
        ))}
      </div>
    );
  }
  if (!rows.length) {
    return (
      <div className="flex h-full items-center justify-center p-6 text-center text-xs text-[var(--muted)]">
        当前筛选下没有已成交的轮次。把「仅成交」关掉、或放宽时间范围再看看。
      </div>
    );
  }

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="min-w-0 flex-1 overflow-x-auto overflow-y-auto">
        <table className="w-full min-w-[960px] text-xs">
          <thead className="sticky top-0 z-10 bg-[var(--surface-2)] text-[var(--muted)]">
            <tr>
              {[
                "时间(本地)",
                "轮次",
                "标的",
                "动作",
                "腿数",
                "成交价",
                "数量",
                "手续费",
                "已实现盈亏",
                "退出归因",
                "clOrdId",
                "关联",
              ].map((label) => (
                <th key={label} scope="col" className="whitespace-nowrap px-2 py-1.5 text-left font-medium">
                  {label}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => {
              const lastLeg = row.legs[row.legs.length - 1];
              return (
                <tr
                  key={row.decisionRef.id}
                  tabIndex={0}
                  onClick={() => onOpenDecision(row.decisionRef.id)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") onOpenDecision(row.decisionRef.id);
                  }}
                  className="cursor-pointer border-b border-[var(--border)] outline-none hover:bg-[var(--surface-2)] focus-visible:bg-[var(--surface-2)]"
                >
                  <td className="whitespace-nowrap px-2 py-1.5 tabular">
                    {formatLocal(row.wallTs || row.ts)}
                  </td>
                  <td className="whitespace-nowrap px-2 py-1.5 tabular">
                    {row.decisionRef.roundNo}
                  </td>
                  <td className="whitespace-nowrap px-2 py-1.5">{row.instId}</td>
                  <td className="whitespace-nowrap px-2 py-1.5">{actionLabel(row.action)}</td>
                  <td className="whitespace-nowrap px-2 py-1.5 tabular">{row.legs.length}</td>
                  <td className="whitespace-nowrap px-2 py-1.5 tabular">
                    {formatNumber(lastLeg?.px, 4)}
                  </td>
                  <td className="whitespace-nowrap px-2 py-1.5 tabular">
                    {formatNumber(lastLeg?.sz, 4)}
                  </td>
                  <td className="whitespace-nowrap px-2 py-1.5 tabular">
                    {formatNumber(lastLeg?.fee, 4)}
                  </td>
                  <td className="whitespace-nowrap px-2 py-1.5 tabular">
                    {row.trip ? formatNumber(row.trip.pnl, 4) : "—"}
                  </td>
                  <td className="whitespace-nowrap px-2 py-1.5 text-[var(--muted)]">
                    {row.trip ? exitSourceLabel(row.trip.exitSource) || "—" : "—"}
                  </td>
                  <td className="whitespace-nowrap px-2 py-1.5 text-[var(--muted)]">
                    {row.clOrdId || "—"}
                  </td>
                  <td className="whitespace-nowrap px-2 py-1.5">
                    <Badge tone={linkConfidenceTone(row.decisionRef.linkConfidence)}>
                      <span aria-hidden="true" className="mr-1">
                        {linkConfidenceGlyph(row.decisionRef.linkConfidence)}
                      </span>
                      {linkConfidenceLabel(row.decisionRef.linkConfidence)}
                    </Badge>
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
