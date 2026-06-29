"use client";

import clsx from "clsx";
import { Switch } from "@/components/ui";
import { LIVE_AUTO_MAX_GROUP_SYMBOLS } from "@/lib/autoTrade";
import { colorForReturn, fmtMoney } from "@/lib/format";
import type { AutoTradeAllocationMode } from "@/lib/liveAutoTypes";
import type { LiveAutoGroupMemberState } from "@/lib/store";
import type { Instrument } from "@/lib/types";
import AutoTradeMemberRow from "./AutoTradeMemberRow";

// 自动交易区（watchlist-auto-trade-group §2.7）：header（分组开关 + 聚合态 + 已分配 + 平均分配）+
// 成员列表 + drop zone（接收从下方拖入的自选标的）。置顶、约占自选页签上半。
export interface AutoTradeMemberView {
  inst: Instrument;
  allocationPct: number;
  runtime: LiveAutoGroupMemberState | null;
}

interface AutoTradePanelProps {
  members: AutoTradeMemberView[];
  active: string;
  livePrice?: number | null;
  changePct?: number | null;
  totalPct: number;
  allocationMode: AutoTradeAllocationMode;
  running: boolean;
  submitting: boolean;
  gateReason: string | null; // 非运行态的禁用原因（运行态恒可点以停止）
  aggregatePnl: number | null;
  dropError: string | null; // 拖入不可自动交易标的等的就地提示
  dragOver: boolean;
  onToggle: (next: boolean) => void;
  onAllocationModeChange: (mode: AutoTradeAllocationMode) => void;
  onEvenSplit: () => void;
  onSelect: (instId: string) => void;
  onAllocationChange: (instId: string, pct: number) => void;
  onRemove: (instId: string) => void;
  onStopMember: (instId: string) => void;
  onDragStartMember: (e: React.DragEvent, instId: string) => void;
  onDragOver: (e: React.DragEvent) => void;
  onDragLeave: () => void;
  onDrop: (e: React.DragEvent) => void;
}

export default function AutoTradePanel({
  members,
  active,
  livePrice,
  changePct,
  totalPct,
  allocationMode,
  running,
  submitting,
  gateReason,
  aggregatePnl,
  dropError,
  dragOver,
  onToggle,
  onAllocationModeChange,
  onEvenSplit,
  onSelect,
  onAllocationChange,
  onRemove,
  onStopMember,
  onDragStartMember,
  onDragOver,
  onDragLeave,
  onDrop,
}: AutoTradePanelProps) {
  // even 模式 Σ>100 才算超限（红）；max_ratio 各标的为独立上限、合计可超 100%，不标红。
  const overLimit = allocationMode === "even" && totalPct > 100 + 1e-9;
  // 运行态恒可点（停止）；停止态由 gateReason 决定能否启动（含空成员 / 和超限 / 0% 成员）。
  const blocked = !running && !!gateReason;

  return (
    <section
      aria-label="自动交易"
      onDragOver={onDragOver}
      onDragLeave={onDragLeave}
      onDrop={onDrop}
      className={clsx(
        "flex min-h-0 flex-1 flex-col transition-colors",
        dragOver && "bg-[var(--accent-soft)]/40 ring-2 ring-inset ring-[var(--accent)]/60",
      )}
    >
      <header className="shrink-0 space-y-1.5 border-b border-[var(--border)] bg-[var(--surface)] px-2 py-1.5">
        <div className="flex items-center justify-between gap-2">
          <span className="flex items-center gap-1.5 text-[11px] font-semibold text-[var(--text)]">
            自动交易
            {running ? (
              <span className="inline-flex items-center gap-1 text-[var(--long)]">
                <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-[var(--long)]" />
                运行中
              </span>
            ) : (
              <span className="text-[10px] font-normal text-[var(--muted)]">已停止</span>
            )}
            {running && aggregatePnl != null && (
              <span className="tabular text-[11px]" style={{ color: colorForReturn(aggregatePnl) }}>
                {fmtMoney(aggregatePnl)}
              </span>
            )}
          </span>
          <div
            title={gateReason ?? (running ? "停止全部自动交易并平仓" : "启动整组自动交易")}
            className={clsx(blocked && "cursor-not-allowed opacity-40")}
          >
            <Switch
              checked={running}
              onChange={(next) => (blocked || submitting ? undefined : onToggle(next))}
              label="自动交易分组开关"
            />
          </div>
        </div>

        <div className="flex items-center justify-between gap-2">
          <ModeToggle mode={allocationMode} disabled={running} onChange={onAllocationModeChange} />
          {allocationMode === "even" && (
            <button
              type="button"
              onClick={onEvenSplit}
              disabled={running || members.length === 0}
              className="rounded px-1.5 py-0.5 text-[10px] font-medium text-[var(--accent)] hover:bg-[var(--accent-soft)] disabled:opacity-40"
            >
              均分
            </button>
          )}
        </div>

        <div className="flex items-center justify-between gap-2 text-[10px]">
          {allocationMode === "even" ? (
            <span className="tabular text-[var(--muted)]">
              已分配{" "}
              <span className={clsx("font-semibold", overLimit ? "text-[var(--short)]" : "text-[var(--text)]")}>
                {Math.round(totalPct * 100) / 100}%
              </span>
              /100% · {members.length}/{LIVE_AUTO_MAX_GROUP_SYMBOLS}
            </span>
          ) : (
            <span className="tabular text-[var(--muted)]">
              合计上限{" "}
              <span className="font-semibold text-[var(--text)]">{Math.round(totalPct * 100) / 100}%</span>
              （各标的独立）· {members.length}/{LIVE_AUTO_MAX_GROUP_SYMBOLS}
            </span>
          )}
        </div>

        {/* 禁用 / 拖入错误就地可见原因（HCI：避免守门被误判为「按钮坏了」）。 */}
        {(blocked && gateReason) || dropError ? (
          <p className="truncate text-[10px] text-[var(--short)]" title={dropError ?? gateReason ?? ""}>
            {dropError ?? gateReason}
          </p>
        ) : null}
      </header>

      <div className="min-h-0 flex-1 overflow-auto py-1">
        {members.length === 0 ? (
          <p className="px-3 py-4 text-center text-[11px] leading-relaxed text-[var(--muted)]">
            把下方自选标的拖到这里，或点其「＋」加入；启动后将对所有成员同时自动交易
          </p>
        ) : (
          members.map((m) => (
            <AutoTradeMemberRow
              key={m.inst.instId}
              inst={m.inst}
              allocationPct={m.allocationPct}
              runtime={m.runtime}
              locked={running}
              active={m.inst.instId === active}
              livePrice={m.inst.instId === active ? livePrice : undefined}
              changePct={m.inst.instId === active ? changePct : undefined}
              invalid={overLimit}
              onSelect={onSelect}
              onAllocationChange={onAllocationChange}
              onRemove={onRemove}
              onStopMember={onStopMember}
              onDragStart={onDragStartMember}
            />
          ))
        )}
      </div>
    </section>
  );
}

// 分配模式切换（segmented）：平均分配（even，份额切分）/ 最大比例（max_ratio，各标的独立上限）。
const MODE_LABELS: Record<AutoTradeAllocationMode, string> = {
  even: "平均分配",
  max_ratio: "最大比例",
};

function ModeToggle({
  mode,
  disabled,
  onChange,
}: {
  mode: AutoTradeAllocationMode;
  disabled: boolean;
  onChange: (mode: AutoTradeAllocationMode) => void;
}) {
  return (
    <div
      role="group"
      aria-label="分配模式"
      className="flex items-center gap-0.5 rounded-md bg-[var(--surface-2)] p-0.5"
    >
      {(Object.keys(MODE_LABELS) as AutoTradeAllocationMode[]).map((m) => (
        <button
          key={m}
          type="button"
          disabled={disabled}
          aria-pressed={mode === m}
          onClick={() => onChange(m)}
          className={clsx(
            "rounded px-2 py-0.5 text-[10px] font-medium transition-colors",
            mode === m
              ? "bg-[var(--surface)] text-[var(--text)] shadow-sm"
              : "text-[var(--muted)] hover:text-[var(--text)]",
            disabled && "cursor-not-allowed opacity-50",
          )}
        >
          {MODE_LABELS[m]}
        </button>
      ))}
    </div>
  );
}
