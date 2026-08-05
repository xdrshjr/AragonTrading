"use client";

import { funnelOf, rejectReasonCn, topRejectReasons } from "@/lib/discovery";
import type { DiscoveryIterationRow } from "@/lib/discoveryTypes";

/**
 * 漏斗：生成 → 审出 → 验证 → 入库（自绘 SVG，无图表库依赖）+ 拒绝原因 Top5。
 *
 * 诚实优先于漂亮（§2.13）：没有迭代数据时显示引导文案而不是一根 0 宽的假柱子。
 */
export default function DiscoveryFunnel({ row }: { row: DiscoveryIterationRow | null }) {
  const stages = funnelOf(row);
  const top = stages[0]?.value ?? 0;
  const reasons = topRejectReasons(row?.rejectedByReason, 5);

  if (!row) {
    return (
      <p className="text-xs text-[var(--muted)]">
        还没有完成的迭代 —— 第一轮结束后这里会显示「生成 → 审出 → 验证 → 入库」的逐段数字。
      </p>
    );
  }

  return (
    <div className="flex flex-col gap-3">
      <ul className="flex flex-col gap-1.5">
        {stages.map((stage) => {
          const ratio = top > 0 ? stage.value / top : 0;
          return (
            <li key={stage.key} className="flex items-center gap-2">
              <span className="w-10 shrink-0 text-[11px] text-[var(--muted)]">{stage.label}</span>
              <div className="h-4 flex-1 overflow-hidden rounded-[6px] bg-[var(--surface-2)]">
                <div
                  className="h-full rounded-[6px] bg-[var(--accent)] transition-[width] duration-300 ease-smooth"
                  style={{ width: `${Math.max(ratio * 100, stage.value > 0 ? 3 : 0)}%` }}
                />
              </div>
              <span className="w-10 shrink-0 text-right text-xs tabular text-[var(--text)]">
                {stage.value}
              </span>
            </li>
          );
        })}
      </ul>

      <div>
        <h4 className="mb-1 text-[11px] font-medium text-[var(--muted)]">拒绝原因 Top 5</h4>
        {reasons.length === 0 ? (
          <p className="text-[11px] text-[var(--muted)]">本轮没有候选被拒。</p>
        ) : (
          <ul className="flex flex-col gap-1">
            {reasons.map((item) => (
              <li key={item.code} className="flex items-center justify-between gap-2 text-[11px]">
                <span className="truncate text-[var(--text)]" title={rejectReasonCn(item.code)}>
                  <code className="mr-1 text-[var(--muted)]">{item.code}</code>
                  {item.label}
                </span>
                <span className="shrink-0 tabular text-[var(--muted)]">{item.count}</span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
