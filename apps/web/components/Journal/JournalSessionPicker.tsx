"use client";

import { Select } from "@/components/ui";
import { formatLocal } from "@/lib/journal";
import type { JournalSession } from "@/lib/journalTypes";

/**
 * 会话选择器（ai-trade-audit-journal §4.1）。
 *
 * 与既有「历史会话」列表的区别是硬性的：那一份**刻意排除当前运行组**，而日志台必须能看
 * 正在跑的那一个——「刚才那一轮为什么这么决策」问的恰恰是还在跑的那个会话。故运行中的组
 * **置顶**并带一个状态记号。
 */
const STATUS_LABELS: Record<string, string> = {
  running: "运行中",
  paused: "已暂停",
  stopped: "已停止",
  done: "已结束",
  error: "异常",
  unknown: "未知",
};

function statusGlyph(status: string): string {
  // 形状 + 文案 + 颜色三重编码里的第一重：下拉项里没有颜色，故记号是必需的。
  if (status === "running") return "●";
  if (status === "paused") return "‖";
  if (status === "error") return "✕";
  return "○";
}

export function sortSessions(items: JournalSession[]): JournalSession[] {
  const rank = (item: JournalSession) => (item.status === "running" ? 0 : item.status === "paused" ? 1 : 2);
  return [...items].sort((a, b) => rank(a) - rank(b) || b.startedTs - a.startedTs);
}

export default function JournalSessionPicker({
  sessions,
  value,
  onChange,
  loading,
}: {
  sessions: JournalSession[];
  value: string;
  onChange: (groupId: string) => void;
  loading: boolean;
}) {
  const ordered = sortSessions(sessions);
  return (
    <Select
      aria-label="选择会话"
      value={value}
      disabled={loading}
      onChange={(e) => onChange(e.target.value)}
      className="max-w-[320px] text-xs"
    >
      <option value="">全部会话</option>
      {ordered.map((item) => (
        <option key={item.groupId} value={item.groupId}>
          {statusGlyph(item.status)} {item.groupId} · {item.instIds.join("/") || "—"} ·{" "}
          {formatLocal(item.startedTs)} · {STATUS_LABELS[item.status] ?? item.status} ·{" "}
          {item.decisionRows} 轮
        </option>
      ))}
    </Select>
  );
}
