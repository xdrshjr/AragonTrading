"use client";

import clsx from "clsx";
import { isAutoTradable } from "@/lib/autoTrade";
import type { Instrument } from "@/lib/types";
import InstrumentRow from "./InstrumentRow";

// 其他自选区（watchlist-auto-trade-group §2.7）：可拖拽自选行 + drop zone（接收从上方拖出的成员→移出自动交易）。
interface WatchOtherPanelProps {
  rows: Instrument[];
  active: string;
  livePrice?: number | null;
  changePct?: number | null;
  locked: boolean; // 分组运行中：禁用拖拽 / 加入
  dragOver: boolean;
  // 可见 header 文案与无障碍标签（缺省「其他自选」，向后兼容）；AI 会话下复用为「自选标的」列表。
  title?: string;
  // 空态提示文案（缺省沿用 locked/unlocked 现文案）；AI 会话下传专用文案。
  emptyHint?: string;
  onSelect: (instId: string) => void;
  onToggleStar: (instId: string) => void;
  onAddAuto: (instId: string) => void;
  onDragStart: (e: React.DragEvent, instId: string) => void;
  onDragOver: (e: React.DragEvent) => void;
  onDragLeave: () => void;
  onDrop: (e: React.DragEvent) => void;
}

export default function WatchOtherPanel({
  rows,
  active,
  livePrice,
  changePct,
  locked,
  dragOver,
  title = "其他自选",
  emptyHint,
  onSelect,
  onToggleStar,
  onAddAuto,
  onDragStart,
  onDragOver,
  onDragLeave,
  onDrop,
}: WatchOtherPanelProps) {
  const resolvedEmptyHint =
    emptyHint ??
    (locked
      ? "自选标的均在自动交易中"
      : "把上方标的拖到这里即移出自动交易；在现货 / 合约页签点 ★ 收藏更多");
  return (
    <section
      aria-label={title}
      onDragOver={onDragOver}
      onDragLeave={onDragLeave}
      onDrop={onDrop}
      className={clsx(
        "flex min-h-0 flex-1 flex-col border-t border-[var(--border)] transition-colors",
        dragOver && "bg-[var(--accent-soft)]/40 ring-2 ring-inset ring-[var(--accent)]/60",
      )}
    >
      <header
        data-nowrap="row"
        className="flex shrink-0 items-center justify-between gap-2 whitespace-nowrap px-2 py-1"
      >
        <span
          title={title}
          className="min-w-0 truncate text-[10px] font-semibold uppercase tracking-wide text-[var(--muted)]"
        >
          {title}
        </span>
        <span className="shrink-0 text-[10px] tabular text-[var(--muted)]">{rows.length}</span>
      </header>
      <div className="min-h-0 flex-1 overflow-auto pb-1">
        {rows.length === 0 ? (
          <p className="whitespace-normal break-words px-3 py-4 text-center text-[11px] leading-relaxed text-[var(--muted)]">
            {resolvedEmptyHint}
          </p>
        ) : (
          rows.map((inst) => (
            <InstrumentRow
              key={inst.instId}
              inst={inst}
              active={inst.instId === active}
              starred
              livePrice={inst.instId === active ? livePrice : undefined}
              liveChangePct={inst.instId === active ? changePct : undefined}
              onSelect={onSelect}
              onToggleStar={onToggleStar}
              draggable={!locked}
              onDragStart={onDragStart}
              onAddAuto={locked ? undefined : onAddAuto}
              canAddAuto={isAutoTradable(inst.instId)}
            />
          ))
        )}
      </div>
    </section>
  );
}
