"use client";

import clsx from "clsx";
import { Badge } from "@/components/ui";
import { useAppStore } from "@/lib/store";
import SimBalanceField from "./SimBalanceField";

/**
 * 顶部「模拟盘 / 实盘」分段开关（支柱 A / A2）。
 *
 * - 「模拟盘」：选中即 setAccountMode("sim") + setMode("paper")，右侧显示可编辑模拟资金。
 * - 「实盘」：诚实置灰（Phase 2 占位），点击无副作用——绝不接入真实下单（R1，诚实硬规则）。
 */
export default function AccountModeSwitch() {
  const { accountMode, setAccountMode, setMode } = useAppStore();

  const selectSim = () => {
    setAccountMode("sim");
    setMode("paper");
  };

  return (
    <div className="flex items-center gap-2">
      <div
        role="radiogroup"
        aria-label="账户模式"
        className="flex items-center gap-0.5 rounded-full bg-[var(--surface-2)] p-0.5"
      >
        <button
          type="button"
          role="radio"
          aria-checked={accountMode === "sim"}
          onClick={selectSim}
          className={clsx(
            "rounded-full px-3 py-1 text-xs font-medium transition-all duration-150 ease-smooth",
            accountMode === "sim"
              ? "bg-[var(--surface)] text-[var(--text)] shadow-soft"
              : "text-[var(--muted)] hover:text-[var(--text)]",
          )}
        >
          模拟盘
        </button>
        <button
          type="button"
          role="radio"
          aria-checked={false}
          aria-disabled
          disabled
          title="实盘下单为 Phase 2 能力；当前版本仅提供模拟盘，绝不接入真实下单"
          className="flex cursor-not-allowed items-center gap-1 rounded-full px-3 py-1 text-xs font-medium text-[var(--muted)] opacity-45"
        >
          实盘
          <Badge tone="neutral">敬请期待</Badge>
        </button>
      </div>
      {accountMode === "sim" && <SimBalanceField />}
    </div>
  );
}
