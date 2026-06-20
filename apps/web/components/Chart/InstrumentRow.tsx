"use client";

import clsx from "clsx";
import { colorForReturn, fmtPct, fmtPrice } from "@/lib/format";
import type { Instrument } from "@/lib/types";

// 标的单行（instrument-sidebar-tabs §4.5）：星标 + 符号 + base/quote + 最新价 + 24h 涨跌。
// 三页签复用；当前选中行可由 WS 实时价覆盖快照价。星标点击 stopPropagation，不触发选中。
interface InstrumentRowProps {
  inst: Instrument;
  active: boolean;
  starred: boolean;
  livePrice?: number | null; // 仅 active 行由既有 WS 市场流覆盖
  liveChangePct?: number | null; // 仅 active 行（区间涨跌，真实）
  onSelect: (instId: string) => void;
  onToggleStar: (instId: string) => void;
}

const INST_TYPE_LABEL: Record<Instrument["instType"], string> = {
  SPOT: "现货",
  SWAP: "永续",
  FUTURES: "交割",
};

function StarIcon({ filled }: { filled: boolean }) {
  return (
    <svg
      viewBox="0 0 24 24"
      aria-hidden="true"
      className="h-3.5 w-3.5"
      fill={filled ? "currentColor" : "none"}
      stroke="currentColor"
      strokeWidth={filled ? 0 : 1.6}
      strokeLinejoin="round"
    >
      <path d="M12 2.6l2.74 5.55 6.13.89-4.43 4.32 1.04 6.1L12 16.66l-5.48 2.8 1.04-6.1L3.13 9.04l6.13-.89L12 2.6z" />
    </svg>
  );
}

export default function InstrumentRow({
  inst,
  active,
  starred,
  livePrice,
  liveChangePct,
  onSelect,
  onToggleStar,
}: InstrumentRowProps) {
  const price = active ? (livePrice ?? inst.last ?? null) : (inst.last ?? null);
  const pct = active ? (liveChangePct ?? inst.changePct ?? null) : (inst.changePct ?? null);

  return (
    <div
      className={clsx(
        "group flex items-center gap-2 border-l-2 pl-2 pr-3 transition-colors",
        active
          ? "border-[var(--accent)] bg-[var(--surface-2)]"
          : "border-transparent hover:bg-[var(--surface-2)]",
      )}
    >
      <button
        type="button"
        aria-pressed={starred}
        aria-label={`${starred ? "取消收藏" : "收藏"} ${inst.instId}`}
        onClick={(e) => {
          e.stopPropagation();
          onToggleStar(inst.instId);
        }}
        className={clsx(
          "shrink-0 rounded p-1 transition-colors",
          starred
            ? "text-[var(--accent)]"
            : "text-[var(--muted)] opacity-60 hover:opacity-100 hover:text-[var(--accent)]",
        )}
      >
        <StarIcon filled={starred} />
      </button>
      <button
        type="button"
        onClick={() => onSelect(inst.instId)}
        aria-current={active}
        className="flex min-w-0 flex-1 items-center justify-between gap-2 py-2 text-left"
      >
        <span className="flex min-w-0 flex-col">
          <span className="truncate text-xs font-medium text-[var(--text)]">
            {inst.instId}
          </span>
          <span className="truncate text-[10px] text-[var(--muted)]">
            {inst.base}/{inst.quote} · {INST_TYPE_LABEL[inst.instType]}
          </span>
        </span>
        <span className="flex shrink-0 flex-col items-end">
          <span className="text-[11px] tabular text-[var(--text)]">{fmtPrice(price)}</span>
          {pct != null && (
            <span className="text-[10px] tabular" style={{ color: colorForReturn(pct) }}>
              {fmtPct(pct)}
            </span>
          )}
        </span>
      </button>
    </div>
  );
}
