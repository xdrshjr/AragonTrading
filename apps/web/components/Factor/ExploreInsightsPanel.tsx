"use client";

import { Badge } from "@/components/ui";
import { formatIc, formatPct } from "@/lib/factor";
import type { FactorMember } from "@/lib/factorTypes";
import type { ExploreInsights, FactorContribution, SizeBucket } from "@/lib/exploreInsights";

/**
 * 因子贡献洞察面板（factor-combo-exploration-insights §2.2，纯展示）：把 computeInsights 的因子级归因渲染为
 * 「因子贡献榜」——发散水平条（绿=帮助 --long、红=拖累 --short，长度按 |边际提升| 归一）+ 出场 / 登顶命中 /
 * 平均学习权重，配「组合大小 × 平均样本外 IC」迷你趋势。样本不足时渲染友好提示而非误导图。
 *
 * 数据由弹窗外壳 computeInsights 算好后注入（P0-1），面板不自持 states/localCombos，杜绝与排行榜「最佳」口径漂移。
 */
export default function ExploreInsightsPanel({
  insights,
  labelOf,
  partial = false,
}: {
  insights: ExploreInsights;
  labelOf: (m: FactorMember) => string;
  partial?: boolean;
}) {
  if (!insights.hasEnoughSamples) {
    return (
      <section className="card flex flex-col gap-1 p-3" data-testid="insights-weak">
        <span className="text-xs font-semibold text-[var(--text)]">因子贡献洞察</span>
        <p className="text-[11px] text-[var(--muted)]">
          成功候选不足以做因子归因（至少需 2 个「成员不同」的成功候选）。请扩大组合策略 / 因子池 / 区间后重试。
        </p>
      </section>
    );
  }

  const maxAbsLift = Math.max(1e-9, ...insights.factors.map((f) => (f.marginalLift != null ? Math.abs(f.marginalLift) : 0)));
  return (
    <section className="card flex flex-col gap-3 p-3" data-testid="insights-panel">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-xs font-semibold text-[var(--text)]">因子贡献洞察</span>
        {partial && <Badge tone="accent">实时（部分）</Badge>}
        <span className="text-[11px] text-[var(--muted)]">{insights.succeededCount} 个成功候选 · 边际 IC 提升排序</span>
        {insights.succeededCount < 4 && <span className="text-[10px] text-[var(--muted)]">（样本较少，仅供参考）</span>}
      </div>

      <div className="flex flex-col gap-1.5">
        {insights.factors.map((f) => (
          <ContributionRow key={f.identity} contribution={f} label={labelOf(f.member)} maxAbsLift={maxAbsLift} total={insights.succeededCount} />
        ))}
      </div>

      <SizeTrend sizes={insights.sizes} />
    </section>
  );
}

// 单因子贡献行：左=名 + 出场；中=发散条（零居中）；右=IC 提升 + 登顶命中 + 平均权重。
function ContributionRow({
  contribution,
  label,
  maxAbsLift,
  total,
}: {
  contribution: FactorContribution;
  label: string;
  maxAbsLift: number;
  total: number;
}) {
  const { marginalLift, appearCount, absentCount, topHitRate, meanAbsWeight } = contribution;
  const tone = marginalLift == null ? "neutral" : marginalLift > 0 ? "long" : marginalLift < 0 ? "short" : "neutral";
  return (
    <div className="flex items-center gap-2" data-testid="contrib-row">
      <div className="flex w-32 shrink-0 flex-col">
        <span className="truncate text-[11px] font-medium text-[var(--text)]" title={label}>
          {label}
        </span>
        <span className="text-[10px] text-[var(--muted)]">出场 {appearCount}/{total}</span>
      </div>
      <DivergingBar lift={marginalLift} tone={tone} maxAbsLift={maxAbsLift} alwaysIncluded={appearCount > 0 && absentCount === 0} />
      <div className="flex w-28 shrink-0 flex-col items-end">
        <span className="tabular text-[11px] font-semibold" data-tone={tone} style={{ color: liftColor(tone) }}>
          {marginalLift == null ? "—" : formatIc(marginalLift)}
        </span>
        <span className="text-[10px] text-[var(--muted)]">
          登顶 {topHitRate == null ? "—" : formatPct(topHitRate)}
          {meanAbsWeight != null && <> · w̄ {meanAbsWeight.toFixed(2)}</>}
        </span>
      </div>
    </div>
  );
}

// 发散水平条：零居中，正向右 --long、负向左 --short，长度按 |lift|/maxAbsLift 归一；null → 中性虚线占位。
function DivergingBar({
  lift,
  tone,
  maxAbsLift,
  alwaysIncluded,
}: {
  lift: number | null;
  tone: "long" | "short" | "neutral";
  maxAbsLift: number;
  alwaysIncluded: boolean;
}) {
  if (lift == null) {
    return (
      <div className="relative flex h-4 flex-1 items-center" data-tone="neutral">
        <div className="h-px w-full border-t border-dashed border-[var(--border)]" />
        <span className="absolute left-1/2 -translate-x-1/2 rounded-full bg-[var(--surface-2)] px-1.5 text-[9px] text-[var(--muted)]">
          {alwaysIncluded ? "始终包含" : "样本不足"}
        </span>
      </div>
    );
  }
  const widthPct = (Math.abs(lift) / maxAbsLift) * 50;
  const positive = lift > 0;
  return (
    <div className="relative h-4 flex-1 rounded-full bg-[var(--surface-2)]">
      <div
        className="absolute top-0.5 h-3 rounded-full"
        data-tone={tone}
        style={{
          width: `${widthPct}%`,
          left: positive ? "50%" : undefined,
          right: positive ? undefined : "50%",
          background: liftColor(tone),
        }}
      />
      <div className="absolute left-1/2 top-0 h-4 w-px bg-[var(--border)]" />
    </div>
  );
}

// 组合大小趋势：紧凑分档「k 因子 · 平均 IC」，帮助判断「加因子是变好还是过拟合」。
function SizeTrend({ sizes }: { sizes: SizeBucket[] }) {
  if (sizes.length === 0) return null;
  const maxAbsIc = Math.max(1e-9, ...sizes.map((s) => (s.meanIc != null ? Math.abs(s.meanIc) : 0)));
  return (
    <div className="flex flex-col gap-1 border-t border-[var(--border)] pt-2">
      <span className="text-[10px] font-medium text-[var(--muted)]">组合大小 × 平均样本外 IC</span>
      <div className="flex items-end gap-2" data-testid="size-trend">
        {sizes.map((s) => (
          <div key={s.size} className="flex flex-1 flex-col items-center gap-0.5">
            <span className="tabular text-[10px] text-[var(--text)]">{formatIc(s.meanIc)}</span>
            <div className="flex h-10 w-full items-end">
              <div
                className="w-full rounded-t-[3px]"
                style={{
                  height: `${s.meanIc != null ? (Math.abs(s.meanIc) / maxAbsIc) * 100 : 4}%`,
                  background: s.meanIc != null && s.meanIc < 0 ? "var(--short)" : "var(--long)",
                  opacity: 0.7,
                }}
              />
            </div>
            <span className="text-[10px] text-[var(--muted)]">{s.size} 因子</span>
          </div>
        ))}
      </div>
    </div>
  );
}

function liftColor(tone: "long" | "short" | "neutral"): string {
  return tone === "long" ? "var(--long)" : tone === "short" ? "var(--short)" : "var(--muted)";
}
