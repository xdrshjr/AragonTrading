"use client";

import clsx from "clsx";
import { colorForReturn, fmtMoney, fmtPrice } from "@/lib/format";
import type { LiveAutoGroupMemberState } from "@/lib/store";
import type { Instrument } from "@/lib/types";
import AutoTradeAllocationInput from "./AutoTradeAllocationInput";

// 自动交易成员行（watchlist-auto-trade-group §2.7）：行情 + 拖拽手柄 + 百分比输入（停止态）/
// 单标的运行态（持仓 + 会话盈亏 + 单独停止）（运行态）+ 移出（停止态）。
interface AutoTradeMemberRowProps {
  inst: Instrument;
  allocationPct: number;
  runtime: LiveAutoGroupMemberState | null;
  locked: boolean; // 分组运行中：锁定编辑 / 拖拽
  active: boolean;
  livePrice?: number | null;
  changePct?: number | null;
  invalid: boolean; // 和超限：百分比输入着红边
  onSelect: (instId: string) => void;
  onAllocationChange: (instId: string, pct: number) => void;
  onRemove: (instId: string) => void;
  onStopMember: (instId: string) => void;
  onDragStart: (e: React.DragEvent, instId: string) => void;
}

const POSITION_LABEL: Record<string, string> = { long: "多", short: "空", flat: "空仓" };

export default function AutoTradeMemberRow({
  inst,
  allocationPct,
  runtime,
  locked,
  active,
  livePrice,
  changePct,
  invalid,
  onSelect,
  onAllocationChange,
  onRemove,
  onStopMember,
  onDragStart,
}: AutoTradeMemberRowProps) {
  const price = active ? (livePrice ?? inst.last ?? null) : (inst.last ?? null);
  const side = runtime?.status?.positionSide ?? runtime?.position?.side ?? "flat";
  const pnl = runtime?.status?.sessionPnlUsd ?? null;
  const running = runtime != null && runtime.status?.state !== "stopped";

  return (
    <div
      draggable={!locked}
      onDragStart={(e) => (locked ? e.preventDefault() : onDragStart(e, inst.instId))}
      className={clsx(
        "group flex items-center gap-1.5 border-l-2 pl-1.5 pr-2 transition-colors",
        active
          ? "border-[var(--accent)] bg-[var(--surface-2)]"
          : "border-transparent hover:bg-[var(--surface-2)]",
      )}
    >
      {/* 拖拽手柄（运行期锁定时变只读视觉）。 */}
      <span
        aria-hidden="true"
        className={clsx(
          "shrink-0 select-none text-[var(--muted)]",
          locked ? "opacity-30" : "cursor-grab opacity-60 group-hover:opacity-100",
        )}
        title={locked ? "运行中不可调整，请先停止" : "拖拽以移出自动交易"}
      >
        ⠿
      </span>
      <button
        type="button"
        onClick={() => onSelect(inst.instId)}
        aria-current={active}
        className="flex min-w-0 flex-1 items-center justify-between gap-2 py-1.5 text-left"
      >
        <span className="flex min-w-0 flex-col">
          <span className="truncate text-xs font-medium text-[var(--text)]">{inst.instId}</span>
          <span className="truncate text-[10px] text-[var(--muted)]">
            {running ? (
              <span className="inline-flex items-center gap-1">
                <span
                  className={clsx(
                    "h-1.5 w-1.5 rounded-full",
                    side === "long"
                      ? "bg-[var(--long)]"
                      : side === "short"
                        ? "bg-[var(--short)]"
                        : "bg-[var(--muted)]",
                  )}
                />
                {POSITION_LABEL[side] ?? "空仓"}
                {pnl != null && (
                  <span className="tabular" style={{ color: colorForReturn(pnl) }}>
                    {fmtMoney(pnl)}
                  </span>
                )}
              </span>
            ) : (
              `${fmtPrice(price)}${changePct != null && active ? "" : ""}`
            )}
          </span>
        </span>
      </button>

      {running ? (
        <button
          type="button"
          onClick={() => onStopMember(inst.instId)}
          className="shrink-0 rounded-md border border-[var(--short)] px-1.5 py-0.5 text-[10px] font-medium text-[var(--short)] hover:bg-[var(--short)]/10"
          title="停止该标的自动交易并平仓"
        >
          停止
        </button>
      ) : (
        <div className="flex shrink-0 items-center gap-1">
          <AutoTradeAllocationInput
            value={allocationPct}
            disabled={locked}
            invalid={invalid}
            onChange={(n) => onAllocationChange(inst.instId, n)}
          />
          <button
            type="button"
            aria-label={`从自动交易移出 ${inst.instId}`}
            disabled={locked}
            onClick={() => onRemove(inst.instId)}
            className="rounded p-0.5 text-[var(--muted)] hover:text-[var(--short)] disabled:opacity-30"
            title="移出自动交易（仍保留在自选）"
          >
            <svg viewBox="0 0 24 24" className="h-3.5 w-3.5" fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round">
              <path d="M6 6l12 12M18 6L6 18" />
            </svg>
          </button>
        </div>
      )}
    </div>
  );
}
