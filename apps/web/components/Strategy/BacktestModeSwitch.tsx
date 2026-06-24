"use client";

import { useAppStore } from "@/lib/store";
import type { BacktestKind } from "@/lib/types";

// 回测模式切换（普通回测 | 高级回测）+ 高级下二级选择「滑动窗口回测」（v1 仅此一项，
// 样式预留为可扩展的分段控件）。复刻 ChartToolbar 的 MODES 分段样式（spec §7.1 / v2·P2-7）。
const KINDS: { key: BacktestKind; label: string }[] = [
  { key: "simple", label: "普通回测" },
  { key: "walkforward", label: "高级回测" },
];

export default function BacktestModeSwitch() {
  const { backtestKind, setBacktestKind } = useAppStore();

  return (
    <div className="flex flex-col gap-2">
      <div className="flex gap-0.5 rounded-[10px] bg-[var(--surface-2)] p-0.5">
        {KINDS.map((k) => (
          <button
            key={k.key}
            type="button"
            onClick={() => setBacktestKind(k.key)}
            className={
              "flex-1 rounded-[7px] px-2.5 py-1 text-xs font-medium transition-all " +
              (backtestKind === k.key
                ? "bg-[var(--surface)] text-[var(--text)] shadow-soft"
                : "text-[var(--muted)] hover:text-[var(--text)]")
            }
          >
            {k.label}
          </button>
        ))}
      </div>

      {backtestKind === "walkforward" && (
        <div className="flex gap-0.5 rounded-[8px] bg-[var(--surface-2)] p-0.5">
          <button
            type="button"
            className="flex-1 rounded-[6px] bg-[var(--surface)] px-2 py-1 text-[11px] font-medium text-[var(--text)] shadow-soft"
            title="把长区间拆成多个等长窗口，逐窗口独立回测，看策略稳定性与抗过拟合"
          >
            滑动窗口回测
          </button>
        </div>
      )}
    </div>
  );
}
