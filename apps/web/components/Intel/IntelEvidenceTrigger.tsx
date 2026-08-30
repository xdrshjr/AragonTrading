"use client";

import clsx from "clsx";
import { evidenceTriggerLabel, hasEvidence, quoteStateLabel } from "@/lib/intelEvidence";
import type { IntelItem } from "@/lib/intelTypes";

/**
 * 卡片内的证据入口：**触发器**与**「无引证」标记**二选一（§3.8 第 1 条）。
 *
 * **没有引文时绝不渲染一个空的触发器**——一个点开永远是空的控件比没有这个控件更糟。取而代之的是
 * 一枚极轻的灰色标记，`title` 说明原因，而原因来自后端下发的 `quoteState`（前端不猜：四种情况
 * 在界面上是四句不同的话，而前端手里没有任何能区分它们的信息）。
 *
 * 无障碍：`<button aria-expanded aria-controls>`；hover 显形但**键盘可达**（`focus-visible`
 * 同样显形）——只在 hover 时出现的控件，对键盘用户等于不存在。
 */
export default function IntelEvidenceTrigger({
  item,
  open,
  controls,
  onToggle,
}: {
  item: IntelItem;
  open: boolean;
  controls: string;
  onToggle: () => void;
}) {
  if (!hasEvidence(item)) {
    const reason = quoteStateLabel(item.quoteState);
    if (!reason) return null;
    return (
      <span
        data-testid="intel-no-evidence"
        title={reason}
        className="self-start rounded px-1 text-[10px] text-[var(--muted)] opacity-70"
      >
        无引证
      </span>
    );
  }
  return (
    <button
      type="button"
      data-testid="intel-evidence-trigger"
      aria-expanded={open}
      aria-controls={controls}
      onClick={onToggle}
      className={clsx(
        "self-start rounded px-1 py-0.5 text-left text-[10px] outline-none transition-opacity duration-150 ease-smooth",
        "text-[var(--accent-ink)] hover:bg-[var(--accent-soft)]",
        "focus-visible:opacity-100 focus-visible:ring-2 focus-visible:ring-[var(--accent)]",
        "motion-reduce:transition-none",
        open
          ? "opacity-100"
          : "opacity-0 group-hover/card:opacity-100 [@media(hover:none)]:opacity-100",
      )}
    >
      {open ? "⌃" : "⌄"} {evidenceTriggerLabel(item.quoteCount)}
    </button>
  );
}
