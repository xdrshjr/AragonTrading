"use client";

import clsx from "clsx";
import type { Strategy } from "@/lib/types";
import { Badge, Button } from "@/components/ui";

/**
 * 对比清单单行：左侧选择区（checkbox + 名称 + 描述，整块点击即勾选）与行尾「详情」按钮分离。
 * 详情按钮置于选择区 `<label>` 之外并 `stopPropagation`，确保「点详情不会顺带切换勾选」（关键交互正确性）。
 * 视觉对齐 `StrategyList` 行（圆角 / 边框 / hover / 选中态 / 徽标），让对比清单与回测清单观感统一。
 */
export default function CompareStrategyRow({
  strategy,
  checked,
  disabled,
  onToggle,
  onOpenDetail,
}: {
  strategy: Strategy;
  checked: boolean;
  disabled: boolean;
  onToggle: (id: number) => void;
  onOpenDetail: (s: Strategy) => void;
}) {
  return (
    <div
      className={clsx(
        "group rounded-[10px] border px-3 py-2.5 transition-all duration-150",
        checked
          ? "border-[var(--accent)] bg-[var(--accent-soft)]"
          : "border-[var(--border)] bg-[var(--surface)] hover:border-[var(--accent)] hover:bg-[var(--surface-2)]",
        strategy.unavailable && "opacity-50",
      )}
    >
      <div className="flex items-start gap-2">
        <label className="flex min-w-0 flex-1 cursor-pointer items-start gap-2">
          <input
            type="checkbox"
            checked={checked}
            disabled={disabled}
            onChange={() => onToggle(strategy.id)}
            className="mt-0.5 h-3.5 w-3.5 shrink-0 accent-[var(--accent)] disabled:cursor-not-allowed"
            aria-label={`选择 ${strategy.name}`}
          />
          <span className="flex min-w-0 flex-col gap-0.5">
            <span className="truncate text-sm font-semibold text-[var(--text)]">{strategy.name}</span>
            {strategy.description && (
              <span className="line-clamp-2 text-[11px] leading-snug text-[var(--muted)]">
                {strategy.description}
              </span>
            )}
          </span>
        </label>
        <div className="flex shrink-0 items-center gap-1.5">
          {strategy.kind === "combo" && <Badge tone="accent">组合</Badge>}
          {strategy.kind === "factor" && <Badge tone="accent">因子</Badge>}
          {strategy.builtin && <Badge tone="neutral">内置</Badge>}
          {strategy.unavailable && <Badge tone="short">不可用</Badge>}
          <Button
            variant="ghost"
            size="sm"
            onClick={(e) => {
              e.stopPropagation();
              onOpenDetail(strategy);
            }}
          >
            详情
          </Button>
        </div>
      </div>
    </div>
  );
}
