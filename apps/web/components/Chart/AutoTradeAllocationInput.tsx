"use client";

import clsx from "clsx";

// 受控百分比输入（watchlist-auto-trade-group §2.7）：步进 ±5、夹取由 store action（clampAllocationOnEdit）
// 负责，和超限时由父级传 invalid 着红边。运行期 locked 时禁用编辑。
export default function AutoTradeAllocationInput({
  value,
  onChange,
  disabled,
  invalid,
}: {
  value: number;
  onChange: (next: number) => void;
  disabled?: boolean;
  invalid?: boolean;
}) {
  return (
    <div
      className={clsx(
        "flex items-center gap-0.5 rounded-md border px-1 py-0.5",
        invalid ? "border-[var(--short)]" : "border-[var(--border)]",
        disabled && "pointer-events-none opacity-50",
      )}
    >
      <button
        type="button"
        aria-label="减少 5%"
        disabled={disabled}
        onClick={() => onChange(value - 5)}
        className="rounded px-1 text-[11px] leading-none text-[var(--muted)] hover:text-[var(--text)]"
      >
        −
      </button>
      <input
        type="text"
        inputMode="decimal"
        value={Number.isFinite(value) ? String(value) : ""}
        disabled={disabled}
        aria-label="可用仓位百分比"
        onChange={(e) => {
          const n = Number(e.target.value);
          if (e.target.value.trim() === "" || Number.isFinite(n)) onChange(Number.isFinite(n) ? n : 0);
        }}
        className="w-8 bg-transparent text-center text-[11px] tabular text-[var(--text)] outline-none"
      />
      <span className="text-[10px] text-[var(--muted)]">%</span>
      <button
        type="button"
        aria-label="增加 5%"
        disabled={disabled}
        onClick={() => onChange(value + 5)}
        className="rounded px-1 text-[11px] leading-none text-[var(--muted)] hover:text-[var(--text)]"
      >
        +
      </button>
    </div>
  );
}
