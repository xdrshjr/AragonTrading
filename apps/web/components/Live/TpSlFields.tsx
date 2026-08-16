"use client";

import clsx from "clsx";
import { Input } from "@/components/ui";
import { fmtPrice } from "@/lib/format";

export type TpSlMode = "price" | "amount";

// 止盈止损折叠区（§2.4）：按价格 / 按金额双模录入；仅填其一也合法（缺项不下发对应字段）。
// 受控展示组件——换算（按金额→触发价）在父层 liveOrder 完成后回传 tpTriggerPx/slTriggerPx 供预览。
export default function TpSlFields({
  expanded,
  onToggleExpanded,
  mode,
  onModeChange,
  tpInput,
  slInput,
  onTpInput,
  onSlInput,
  tpTriggerPx,
  slTriggerPx,
  disabled,
}: {
  expanded: boolean;
  onToggleExpanded: () => void;
  mode: TpSlMode;
  onModeChange: (mode: TpSlMode) => void;
  tpInput: string;
  slInput: string;
  onTpInput: (value: string) => void;
  onSlInput: (value: string) => void;
  tpTriggerPx: number | null;
  slTriggerPx: number | null;
  disabled?: boolean;
}) {
  const unit = mode === "price" ? "触发价" : "金额 USDT";
  return (
    <div className="flex flex-col gap-2">
      <button
        type="button"
        onClick={onToggleExpanded}
        className="flex items-center justify-between text-xs font-medium text-[var(--muted)] hover:text-[var(--text)]"
      >
        <span>止盈 / 止损（可选）</span>
        <span className="text-[10px]">{expanded ? "收起 ▴" : "展开 ▾"}</span>
      </button>

      {expanded && (
        <div className="flex flex-col gap-2">
          <div className="inline-flex gap-1 self-start rounded-[10px] bg-[var(--surface-2)] p-1">
            <ModeTab active={mode === "price"} onClick={() => onModeChange("price")} label="按价格" />
            <ModeTab active={mode === "amount"} onClick={() => onModeChange("amount")} label="按金额" />
          </div>

          <div className="grid grid-cols-2 gap-2">
            <label className="flex flex-col gap-1">
              <span className="text-[10px] text-[var(--long-ink)]">止盈{unit}</span>
              <Input
                type="number"
                inputMode="decimal"
                min={0}
                value={tpInput}
                disabled={disabled}
                aria-label={`止盈${unit}`}
                onChange={(e) => onTpInput(e.target.value)}
                className="text-right tabular"
              />
            </label>
            <label className="flex flex-col gap-1">
              <span className="text-[10px] text-[var(--short-ink)]">止损{unit}</span>
              <Input
                type="number"
                inputMode="decimal"
                min={0}
                value={slInput}
                disabled={disabled}
                aria-label={`止损${unit}`}
                onChange={(e) => onSlInput(e.target.value)}
                className="text-right tabular"
              />
            </label>
          </div>

          {mode === "amount" && (
            <p className="text-[10px] leading-relaxed text-[var(--muted)]">
              触发价预览 · 止盈 {tpTriggerPx != null ? fmtPrice(tpTriggerPx) : "—"} / 止损{" "}
              {slTriggerPx != null ? fmtPrice(slTriggerPx) : "—"}（按预计盈亏额换算，触发后市价）
            </p>
          )}
        </div>
      )}
    </div>
  );
}

function ModeTab({ active, onClick, label }: { active: boolean; onClick: () => void; label: string }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={clsx(
        // 与限价/市价、全仓/逐仓同一种「填充式分段」语言：选中=surface 填充+阴影。
        "rounded-[7px] px-3 py-1 text-[11px] font-medium transition-all duration-150",
        active
          ? "bg-[var(--surface)] text-[var(--text)] shadow-soft"
          : "text-[var(--muted)] hover:text-[var(--text)]",
      )}
    >
      {label}
    </button>
  );
}
