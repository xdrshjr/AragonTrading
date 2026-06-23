"use client";

import clsx from "clsx";
import type { OrderDirection } from "@/lib/tradingTypes";

// 买多（绿）/ 买空（红）分段切换（实盘下单方向，§2.3）。
// 语义色用设计 token（--long / --short）；选中态填充对应色，未选中为静默灰。
export default function DirectionToggle({
  value,
  onChange,
  disabled,
}: {
  value: OrderDirection;
  onChange: (next: OrderDirection) => void;
  disabled?: boolean;
}) {
  return (
    <div
      role="radiogroup"
      aria-label="下单方向"
      className="grid grid-cols-2 gap-1 rounded-[10px] bg-[var(--surface-2)] p-1"
    >
      <DirectionButton
        active={value === "long"}
        tone="long"
        label="买多 · 开多"
        disabled={disabled}
        onClick={() => onChange("long")}
      />
      <DirectionButton
        active={value === "short"}
        tone="short"
        label="买空 · 开空"
        disabled={disabled}
        onClick={() => onChange("short")}
      />
    </div>
  );
}

function DirectionButton({
  active,
  tone,
  label,
  disabled,
  onClick,
}: {
  active: boolean;
  tone: "long" | "short";
  label: string;
  disabled?: boolean;
  onClick: () => void;
}) {
  const color = tone === "long" ? "var(--long)" : "var(--short)";
  return (
    <button
      type="button"
      role="radio"
      aria-checked={active}
      disabled={disabled}
      onClick={onClick}
      className={clsx(
        "rounded-[7px] px-3 py-1.5 text-xs font-semibold transition-all duration-150 ease-smooth",
        "disabled:cursor-not-allowed disabled:opacity-40",
        active ? "text-white shadow-soft" : "text-[var(--muted)] hover:text-[var(--text)]",
      )}
      style={active ? { background: color } : undefined}
    >
      {label}
    </button>
  );
}
