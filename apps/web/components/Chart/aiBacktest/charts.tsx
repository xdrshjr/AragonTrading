// AI 回测水下回撤图 / 周期收益分布（ai-auto-trading-backtest-analytics §2.4）。
// 轻量内联 SVG / flex 柱，无外部图表依赖（仿既有 Sparkline 手法）；从 result.equity 纯派生。
import { periodReturns, underwaterSeries } from "@/lib/backtestStats";
import { fmtPct } from "@/lib/format";
import type { EquityPoint } from "@/lib/types";

function EmptyMini({ hint }: { hint: string }) {
  return (
    <div className="flex h-full w-full items-center justify-center text-[11px] text-[var(--muted)]">
      {hint}
    </div>
  );
}

/** 水下回撤面积图：0 轴在顶，越深越向下（红色面积）；缺 drawdown 时由峰值现算兜底。 */
export function UnderwaterChart({ equity }: { equity: EquityPoint[] }) {
  const series = underwaterSeries(equity);
  if (series.length < 2) return <EmptyMini hint="无足够数据绘制水下回撤" />;
  const w = 100;
  const h = 40;
  const deepest = Math.min(...series.map((p) => p.drawdown), 0);
  const span = Math.abs(deepest) || 1;
  const pts = series.map((p, i) => ({
    x: (i / (series.length - 1)) * w,
    y: (Math.abs(p.drawdown) / span) * h,
  }));
  const line = pts.map((p, i) => `${i === 0 ? "M" : "L"}${p.x.toFixed(2)},${p.y.toFixed(2)}`).join(" ");
  const area = `M0,0 ${pts.map((p) => `L${p.x.toFixed(2)},${p.y.toFixed(2)}`).join(" ")} L${w},0 Z`;
  return (
    <svg viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none" className="h-full w-full" aria-label="水下回撤">
      <path d={area} fill="var(--short)" fillOpacity={0.18} />
      <path d={line} fill="none" stroke="var(--short)" strokeWidth={1} vectorEffect="non-scaling-stroke" />
    </svg>
  );
}

/** 周期收益柱：正绿负红，以中央 0 轴向上/下延伸；hover 显日期与收益（自动降密，§2.4）。 */
export function PeriodReturnBars({ equity }: { equity: EquityPoint[] }) {
  const data = periodReturns(equity);
  if (data.length === 0) return <EmptyMini hint="无足够数据计算周期收益" />;
  const maxAbs = Math.max(...data.map((d) => Math.abs(d.ret)), 1e-9);
  return (
    <div className="relative flex h-24 items-stretch gap-[2px]">
      <div className="pointer-events-none absolute inset-x-0 top-1/2 border-t border-dashed border-[var(--border)]" />
      {data.map((d, i) => {
        const height = (Math.abs(d.ret) / maxAbs) * 50;
        const up = d.ret >= 0;
        return (
          <div key={`${d.label}-${i}`} className="relative flex-1" title={`${d.label} · ${fmtPct(d.ret)}`}>
            <div
              className="absolute inset-x-0"
              style={{
                top: up ? `${50 - height}%` : "50%",
                height: `${height}%`,
                backgroundColor: up ? "var(--long)" : "var(--short)",
              }}
            />
          </div>
        );
      })}
    </div>
  );
}
