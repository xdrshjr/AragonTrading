"use client";

import { Badge, Button, Input, Select } from "@/components/ui";
import { captureTierLabel } from "@/lib/journal";
import type { JournalFilters, JournalSession, JournalStats } from "@/lib/journalTypes";
import JournalSessionPicker from "./JournalSessionPicker";

/**
 * 工具条（ai-trade-audit-journal §2.6.1 的第一行与第二行）。
 *
 * 会话选择 + 8 个过滤控件 + 健康度徽标 + 导出按钮。
 *
 * **一个口径数字都不硬编码**：采集档、丢弃条数等一律取自 `/journal/stats` 的回执
 * （教训来源：硬编码的「近 50 轮」一旦与常量分叉就开始说谎）。
 */
const ACTIONS = [
  { value: "", label: "全部动作" },
  { value: "open_long", label: "开多" },
  { value: "open_short", label: "开空" },
  { value: "close", label: "平仓" },
  { value: "hold", label: "观望" },
];

const TRIGGERS = [
  { value: "", label: "全部唤醒" },
  { value: "candle", label: "K 线确认" },
  { value: "interval", label: "定时唤醒" },
  { value: "monitor", label: "盯市 tick" },
];

/** `q` 的长度上界与后端 `Query(max_length=64)` 同源；超出即 422，故这里先拦。 */
export const MAX_QUERY_CHARS = 64;

function Toggle({
  label,
  checked,
  onChange,
}: {
  label: string;
  checked: boolean;
  onChange: (next: boolean) => void;
}) {
  return (
    <label className="flex cursor-pointer items-center gap-1 whitespace-nowrap text-xs">
      <input type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)} />
      {label}
    </label>
  );
}

export default function JournalToolbar({
  sessions,
  sessionsLoading,
  filters,
  onFiltersChange,
  stats,
  onOpenExport,
  onOpenHealth,
  onRefresh,
}: {
  sessions: JournalSession[];
  sessionsLoading: boolean;
  filters: JournalFilters;
  onFiltersChange: (next: JournalFilters) => void;
  stats: JournalStats | null;
  onOpenExport: () => void;
  onOpenHealth: () => void;
  onRefresh: () => void;
}) {
  const patch = (next: Partial<JournalFilters>) => onFiltersChange({ ...filters, ...next });
  const gap = stats ? stats.counters.dropped + stats.counters.droppedOnShutdown : 0;

  return (
    <div className="shrink-0 space-y-1.5 border-b border-[var(--border)] p-2">
      <div className="flex flex-wrap items-center gap-2">
        <JournalSessionPicker
          sessions={sessions}
          value={filters.groupId ?? ""}
          onChange={(groupId) => patch({ groupId: groupId || undefined })}
          loading={sessionsLoading}
        />
        {stats && (
          <button
            type="button"
            onClick={onOpenHealth}
            className="rounded-[10px] outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]"
          >
            <Badge tone={gap > 0 ? "accent" : stats.enabled ? "long" : "neutral"}>
              <span aria-hidden="true" className="mr-1">
                {gap > 0 ? "!" : stats.enabled ? "●" : "○"}
              </span>
              采集 {captureTierLabel(stats.captureTier)}
              {gap > 0 ? ` · 有 ${gap} 条缺口` : ""}
            </Badge>
          </button>
        )}
        <div className="ml-auto flex items-center gap-1.5">
          <Button size="sm" variant="ghost" onClick={onRefresh}>
            刷新
          </Button>
          <Button size="sm" variant="primary" onClick={onOpenExport}>
            导出 ⤓
          </Button>
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <Input
          aria-label="标的"
          placeholder="标的（如 BTC-USDT-SWAP）"
          value={filters.instId ?? ""}
          onChange={(e) => patch({ instId: e.target.value || undefined })}
          className="w-48 text-xs"
        />
        <Input
          aria-label="周期"
          placeholder="周期"
          value={filters.bar ?? ""}
          onChange={(e) => patch({ bar: e.target.value || undefined })}
          className="w-24 text-xs"
        />
        <Select
          aria-label="动作"
          value={filters.action ?? ""}
          onChange={(e) => patch({ action: e.target.value || undefined })}
          className="text-xs"
        >
          {ACTIONS.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </Select>
        <Select
          aria-label="唤醒原因"
          value={filters.trigger ?? ""}
          onChange={(e) => patch({ trigger: e.target.value || undefined })}
          className="text-xs"
        >
          {TRIGGERS.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </Select>
        <Toggle
          label="仅成交"
          checked={Boolean(filters.executedOnly)}
          onChange={(next) => patch({ executedOnly: next || undefined })}
        />
        <Toggle
          label="仅降级"
          checked={Boolean(filters.degradedOnly)}
          onChange={(next) => patch({ degradedOnly: next || undefined })}
        />
        <Toggle
          label="门禁否决"
          checked={Boolean(filters.gateVetoedOnly)}
          onChange={(next) => patch({ gateVetoedOnly: next || undefined })}
        />
        <Input
          aria-label="关键字"
          placeholder="在决策理由里搜索"
          maxLength={MAX_QUERY_CHARS}
          value={filters.q ?? ""}
          onChange={(e) => patch({ q: e.target.value.slice(0, MAX_QUERY_CHARS) || undefined })}
          className="w-56 text-xs"
        />
      </div>
    </div>
  );
}
