"use client";

import { SOURCE_LABEL, sourceBreakdown } from "@/lib/discovery";
import type { DiscoveryIterationRow, SourceBudget } from "@/lib/discoveryTypes";

const COLORS: Record<string, string> = {
  mutate: "var(--accent)",
  crossover: "var(--long)",
  perturb: "var(--muted)",
  random: "var(--short)",
  llm: "color-mix(in srgb, var(--accent) 55%, var(--text))",
  seed: "var(--border)",
};

/**
 * 五维来源堆叠柱 + 自适应预算对照。
 *
 * 同时画出「本轮实际产出的来源分布」与「本轮生效的预算比例」：两者背离时一眼能看出
 * 某个来源在空转（例如父本不足导致变异份额回落到随机），这正是 §2.5 自适应是否真的
 * 接线了的可视化判据。
 */
export default function DiscoverySourceBars({
  row,
  budget,
}: {
  row: DiscoveryIterationRow | null;
  budget: SourceBudget;
}) {
  const parts = sourceBreakdown(row);
  const total = parts.reduce((sum, item) => sum + item.value, 0);

  return (
    <div className="flex flex-col gap-2">
      <div className="flex h-5 w-full overflow-hidden rounded-[6px] bg-[var(--surface-2)]">
        {total === 0 ? (
          <span className="flex w-full items-center justify-center text-[10px] text-[var(--muted)]">
            暂无来源数据
          </span>
        ) : (
          parts
            .filter((item) => item.value > 0)
            .map((item) => (
              <div
                key={item.key}
                title={`${item.label}：${item.value} 条`}
                style={{ width: `${(item.value / total) * 100}%`, background: COLORS[item.key] }}
                className="h-full"
              />
            ))
        )}
      </div>

      <ul className="grid grid-cols-2 gap-x-3 gap-y-1 text-[11px]">
        {(Object.keys(SOURCE_LABEL) as (keyof SourceBudget | "seed")[])
          .filter((key) => key !== "seed")
          .map((key) => {
            const produced = parts.find((item) => item.key === key)?.value ?? 0;
            const share = budget[key as keyof SourceBudget] ?? 0;
            return (
              <li key={key} className="flex items-center gap-1.5">
                <span
                  aria-hidden="true"
                  className="h-2 w-2 shrink-0 rounded-full"
                  style={{ background: COLORS[key] }}
                />
                <span className="flex-1 truncate text-[var(--muted)]">{SOURCE_LABEL[key]}</span>
                <span className="tabular text-[var(--text)]">{produced}</span>
                <span className="tabular text-[var(--muted)]">
                  / {(share * 100).toFixed(0)}%
                </span>
              </li>
            );
          })}
      </ul>
      <p className="text-[10px] leading-relaxed text-[var(--muted)]">
        左为本轮实际产出条数，右为本轮生效的预算比例（每 10 轮按入库率与去重率自适应调整）。
      </p>
    </div>
  );
}
