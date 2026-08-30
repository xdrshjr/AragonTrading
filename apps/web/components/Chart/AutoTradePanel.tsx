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
  onOpenRecords: () => void; // 打开「自动交易收益」记录面板（按时间维度汇总每次完整交易）
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
  onOpenRecords,
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
        {/* 第 1 行 Type A：左簇标题为唯一压力吸收点，右簇开关恒不收缩。
            min-h-[24px] 把停止 / 运行两态行高锁成同值，避免启停瞬间抖一格并推动下面两行。 */}
        <div
          data-nowrap="row"
          data-testid="auto-trade-header-row"
          className="flex min-h-[24px] items-center justify-between gap-2 whitespace-nowrap"
        >
          <span className="flex min-w-0 items-center gap-1.5 text-[11px] font-semibold text-[var(--text)]">
            <span className="min-w-0 truncate" title="自动交易">
              自动交易
            </span>
            {running ? (
              <span className="inline-flex shrink-0 items-center gap-1 text-[var(--long-ink)]">
                <span className="h-1.5 w-1.5 shrink-0 animate-pulse rounded-full bg-[var(--long)]" />
                运行中
              </span>
            ) : (
              <span className="shrink-0 text-[10px] font-normal text-[var(--muted)]">已停止</span>
            )}
            {running && aggregatePnl != null && (
              <span
                className="shrink-0 tabular text-[11px]"
                style={{ color: colorForReturn(aggregatePnl) }}
              >
                {fmtMoney(aggregatePnl)}
              </span>
            )}
          </span>
          <div
            title={gateReason ?? (running ? "停止全部自动交易并平仓" : "启动整组自动交易")}
            className={clsx("shrink-0", blocked && "cursor-not-allowed opacity-40")}
          >
            <Switch
              checked={running}
              onChange={(next) => (blocked || submitting ? undefined : onToggle(next))}
              label="自动交易分组开关"
            />
          </div>
        </div>

        {/* 第 2 行 Type A：两个条目都不可压缩，宽度不足时整行由 header 的既有边界兜住。 */}
        <div
          data-nowrap="row"
          className="flex items-center justify-between gap-2 whitespace-nowrap"
        >
          <ModeToggle mode={allocationMode} disabled={running} onChange={onAllocationModeChange} />
          {allocationMode === "even" && (
            <button
              type="button"
              onClick={onEvenSplit}
              disabled={running || members.length === 0}
              className="shrink-0 rounded px-1.5 py-0.5 text-[10px] font-medium text-[var(--accent-ink)] hover:bg-[var(--accent-soft)] disabled:opacity-40"
            >
              均分
            </button>
          )}
        </div>

        {/* 第 3 行 Type A：左侧汇总是本行唯一吸收点（min-w-0 truncate + title），
            右簇「成员计数 + 收益记录」恒不收缩。「收益记录」从第 1 行下沉到这里：
            它是低频查看动作，不该与「立即停止全部自动交易并平仓」的开关并排（§8.4）。
            max_ratio 的「（各标的独立）」降级进 title —— 它与正上方 ModeToggle 的
            「最大比例」是同一件事的第二次表述，却占掉 200px 档 38% 的可用宽。 */}
        <div
          data-nowrap="row"
          data-testid="auto-trade-summary-row"
          className="flex items-center justify-between gap-2 whitespace-nowrap text-[10px]"
        >
          <span
            data-testid="auto-trade-allocation-summary"
            className="min-w-0 truncate tabular text-[var(--muted)]"
            title={
              allocationMode === "even"
                ? "已分配占总预算的比例，Σ 不得超过 100%"
                : "每个标的各自的仓位上限，各标的独立，合计可超过 100%"
            }
          >
            {allocationMode === "even" ? "已分配 " : "合计上限 "}
            <span
              className={clsx(
                "font-semibold",
                overLimit ? "text-[var(--short-ink)]" : "text-[var(--text)]",
              )}
            >
              {Math.round(totalPct * 100) / 100}%
            </span>
            {allocationMode === "even" ? "/100%" : ""}
          </span>
          <span className="flex shrink-0 items-center gap-1.5">
            <span className="tabular text-[var(--muted)]">
              {members.length}/{LIVE_AUTO_MAX_GROUP_SYMBOLS}
            </span>
            <button
              type="button"
              onClick={onOpenRecords}
              title="按时间维度查看每次完整交易的收益与总收益率"
              className="inline-flex shrink-0 items-center gap-1 rounded px-1.5 py-0.5 text-[10px] font-medium text-[var(--accent-ink)] transition-colors hover:bg-[var(--accent-soft)]"
            >
              <svg width="11" height="11" viewBox="0 0 16 16" fill="none" aria-hidden>
                <path
                  d="M2 13.5V9.5M6 13.5V5.5M10 13.5V7.5M14 13.5V3.5"
                  stroke="currentColor"
                  strokeWidth="1.6"
                  strokeLinecap="round"
                />
              </svg>
              收益记录
            </button>
          </span>
        </div>

        {/* 禁用 / 拖入错误就地可见原因（HCI：避免守门被误判为「按钮坏了」）。 */}
        {(blocked && gateReason) || dropError ? (
          <p
            className="whitespace-normal break-words text-[10px] text-[var(--short-ink)]"
            title={dropError ?? gateReason ?? ""}
          >
            {dropError ?? gateReason}
          </p>
        ) : null}
      </header>

      <div className="min-h-0 flex-1 overflow-auto py-1">
        {members.length === 0 ? (
          <p className="whitespace-normal break-words px-3 py-4 text-center text-[11px] leading-relaxed text-[var(--muted)]">
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
      className="flex shrink-0 items-center gap-0.5 rounded-md bg-[var(--surface-2)] p-0.5"
    >
      {(Object.keys(MODE_LABELS) as AutoTradeAllocationMode[]).map((m) => (
        <button
          key={m}
          type="button"
          disabled={disabled}
          aria-pressed={mode === m}
          onClick={() => onChange(m)}
          className={clsx(
            "shrink-0 rounded px-2 py-0.5 text-[10px] font-medium transition-colors",
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
