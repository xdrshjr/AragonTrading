"use client";

import clsx from "clsx";
import { Badge, Button, Select, Skeleton } from "@/components/ui";
import { eventLabel, eventLevelLabel, formatLocal, formatUtc } from "@/lib/journal";
import type { JournalEvent } from "@/lib/journalTypes";

/**
 * 系统事件表（ai-trade-audit-journal §2.6.3 / §4.5）。
 *
 * 级别筛选 + 分类徽标。事件 `code` 是**稳定契约**：它会进导出文件、会被离线脚本按值分支，
 * 故这里同时显示中文名与原码——只显示中文名会让离线侧对不上，只显示原码则用户读不懂。
 */
const LEVELS = [
  { value: "", label: "全部级别" },
  { value: "info", label: "信息" },
  { value: "warn", label: "告警" },
  { value: "error", label: "错误" },
];

export default function JournalEventTable({
  rows,
  level,
  onLevelChange,
  loading,
  error,
  onRetry,
  hasMore,
  onLoadMore,
}: {
  rows: JournalEvent[];
  level: string;
  onLevelChange: (value: string) => void;
  loading: boolean;
  error: string;
  onRetry: () => void;
  hasMore: boolean;
  onLoadMore: () => void;
}) {
  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex shrink-0 items-center gap-2 border-b border-[var(--border)] p-2">
        <Select
          aria-label="事件级别"
          value={level}
          onChange={(e) => onLevelChange(e.target.value)}
          className="w-32"
        >
          {LEVELS.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </Select>
      </div>

      {error ? (
        <div className="flex flex-1 flex-col items-center justify-center gap-2 p-6 text-xs">
          <p className="text-[var(--short-ink)]">载入系统事件失败：{error}</p>
          <Button size="sm" variant="ghost" onClick={onRetry}>
            重试
          </Button>
        </div>
      ) : loading && !rows.length ? (
        <div className="space-y-2 p-3">
          {[0, 1, 2, 3].map((i) => (
            <Skeleton key={i} className="h-7 w-full" />
          ))}
        </div>
      ) : !rows.length ? (
        <div className="flex flex-1 items-center justify-center p-6 text-center text-xs text-[var(--muted)]">
          当前筛选下没有系统事件。若采集下限被设成了 warn，info 级事件（如整轮跳过）
          整体不会被采集——那是显式选择的静默，不是缺口。
        </div>
      ) : (
        <div className="min-w-0 flex-1 overflow-x-auto overflow-y-auto">
          <table className="w-full min-w-[880px] text-xs">
            <thead className="sticky top-0 z-10 bg-[var(--surface-2)] text-[var(--muted)]">
              <tr>
                {["时间(本地)", "时间(UTC)", "级别", "分类", "事件", "标的", "说明"].map((label) => (
                  <th key={label} scope="col" className="whitespace-nowrap px-2 py-1.5 text-left font-medium">
                    {label}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.id} className="border-b border-[var(--border)]">
                  <td className="whitespace-nowrap px-2 py-1.5 tabular">
                    {formatLocal(row.wallTs || row.ts)}
                  </td>
                  <td className="whitespace-nowrap px-2 py-1.5 tabular text-[var(--muted)]">
                    {formatUtc(row.ts)}
                  </td>
                  <td className="whitespace-nowrap px-2 py-1.5">
                    <Badge tone={row.level === "error" ? "short" : row.level === "warn" ? "accent" : "neutral"}>
                      <span aria-hidden="true" className="mr-1">
                        {row.level === "error" ? "✕" : row.level === "warn" ? "!" : "·"}
                      </span>
                      {eventLevelLabel(row.level)}
                    </Badge>
                  </td>
                  <td className="whitespace-nowrap px-2 py-1.5 text-[var(--muted)]">{row.category}</td>
                  <td className="whitespace-nowrap px-2 py-1.5">
                    {eventLabel(row.code)}
                    <span className="ml-1 text-[10px] text-[var(--muted)]">{row.code}</span>
                  </td>
                  <td className="whitespace-nowrap px-2 py-1.5">{row.instId || "—"}</td>
                  <td className={clsx("px-2 py-1.5", "whitespace-nowrap")}>{row.message}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

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
