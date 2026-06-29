"use client";

import clsx from "clsx";
import type { FactorContribution } from "@/lib/types";
import { contributionBars } from "@/lib/tradeExplain";

/**
 * 因子贡献 diverging 水平条形图（auto-trade-explainability §三）。
 *
 * 纯 div 实现（无第三方图表依赖）：以零轴为中心，正贡献（多头）向右、负贡献（空头）向左，
 * 按 |贡献| 归一化宽度并降序排列；top-driver 加粗高亮、弱贡献淡显，符合「先概览后细节」的渐进披露。
 * 配色复用既有 token（多 `--long` / 空 `--short` / 中性 `--muted`），不新增全局 CSS 变量。
 */
const DIRECTION_LABEL: Record<"long" | "short", string> = { long: "多", short: "空" };

export default function FactorContributionBars({
  factors,
}: {
  factors?: FactorContribution[] | null;
}) {
  const bars = contributionBars(factors ?? undefined);
  if (bars.length === 0) return null;

  const byKey = new Map(factors?.map((f) => [f.factorKey, f]) ?? []);

  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex items-center justify-between text-[10px] uppercase tracking-wide text-[var(--muted)]">
        <span>因子归因（按贡献度）</span>
        <span className="flex items-center gap-2">
          <Legend tone="short" label="空头贡献" />
          <Legend tone="long" label="多头贡献" />
        </span>
      </div>
      <ul className="flex flex-col gap-1" aria-label="因子贡献明细">
        {bars.map((bar) => {
          const factor = byKey.get(bar.factorKey);
          return (
            <li key={bar.factorKey} className="grid grid-cols-[7.5rem_1fr_4.5rem] items-center gap-2">
              <span
                className={clsx(
                  "flex items-center gap-1 truncate text-[11px]",
                  bar.isTopDriver ? "font-semibold text-[var(--text)]" : "text-[var(--muted)]",
                )}
                title={`${bar.name}（${DIRECTION_LABEL[factor?.direction ?? bar.tone]}）`}
              >
                {bar.isTopDriver && (
                  <span
                    className="h-1.5 w-1.5 shrink-0 rounded-full"
                    style={{ background: `var(--${bar.tone})` }}
                    aria-hidden
                  />
                )}
                <span className="truncate">{bar.name}</span>
              </span>
              <DivergingBar pct={bar.pct} tone={bar.tone} dim={!bar.isTopDriver} />
              <span
                className="text-right text-[11px] tabular"
                style={{ color: `var(--${bar.tone})`, opacity: bar.isTopDriver ? 1 : 0.7 }}
                title={factorHint(factor)}
              >
                {signedContribution(factor)}
              </span>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

/** 单条 diverging 条：零轴居中，正向右、负向左，宽度为 |贡献| 归一化的一半幅度。 */
function DivergingBar({
  pct,
  tone,
  dim,
}: {
  pct: number;
  tone: "long" | "short";
  dim: boolean;
}) {
  const half = Math.max(0, Math.min(pct, 100)) / 2; // 每侧最多占半幅
  return (
    <div className="relative h-3 w-full rounded-sm bg-[var(--surface-2)]">
      <div className="absolute left-1/2 top-0 h-full w-px bg-[var(--border)]" aria-hidden />
      <div
        className="absolute top-0 h-full rounded-sm transition-[width]"
        style={{
          background: `var(--${tone})`,
          opacity: dim ? 0.45 : 0.9,
          width: `${half}%`,
          ...(tone === "long" ? { left: "50%" } : { right: "50%" }),
        }}
      />
    </div>
  );
}

function Legend({ tone, label }: { tone: "long" | "short"; label: string }) {
  return (
    <span className="flex items-center gap-1 text-[10px] text-[var(--muted)]">
      <span className="h-1.5 w-1.5 rounded-full" style={{ background: `var(--${tone})` }} aria-hidden />
      {label}
    </span>
  );
}

function signedContribution(factor?: FactorContribution): string {
  if (!factor || !Number.isFinite(factor.contribution)) return "—";
  const c = factor.contribution;
  return `${c >= 0 ? "+" : ""}${c.toFixed(3)}`;
}

function factorHint(factor?: FactorContribution): string {
  if (!factor) return "";
  return `权重 ${factor.weight.toFixed(2)} · z ${factor.zscore.toFixed(2)} · 占比 ${(factor.share * 100).toFixed(0)}%`;
}
