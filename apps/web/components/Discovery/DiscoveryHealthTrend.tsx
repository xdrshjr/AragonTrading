"use client";

import {
  HEALTH_STATUS_TONE,
  formatRetention,
  healthStatusCn,
  toTrendPoints,
} from "@/lib/discoveryHealth";
import type { DiscoveryAssetCheckRow } from "@/lib/discoveryAssetTypes";

const WIDTH = 320;
const HEIGHT = 96;
const PAD = 8;
const TONE_VAR: Record<string, string> = {
  long: "var(--long)",
  short: "var(--short)",
  accent: "var(--accent)",
  neutral: "var(--muted)",
};

/**
 * 体检趋势折线（自绘 SVG，沿用 `DiscoverySourceBars` / `ExpressionTree` 的自绘姿态）。
 *
 * 承重的一条渲染纪律：**`unknown` / `error` 的点画空心且不与相邻点连线**。
 * 把不可判定的点连成线，等于替系统编造一段它根本没有结论的历史——而这恰恰是本轮
 * 「诚实优先于好看」姿态最容易被悄悄破坏的地方。
 */
export default function DiscoveryHealthTrend({
  checks,
}: {
  checks: DiscoveryAssetCheckRow[];
}) {
  const points = toTrendPoints(checks);
  if (points.length === 0) {
    return (
      <p className="text-[11px] text-[var(--muted)]">
        还没有体检记录。资产在挖掘窗口之后攒够新数据才会做第一次体检。
      </p>
    );
  }
  const decided = points.filter((point) => !point.undecided);
  if (decided.length === 0) {
    return (
      <p className="text-[11px] leading-relaxed text-[var(--muted)]">
        已有 {points.length} 次体检，但都因新增样本或暖机数据不足而暂不评级——
        这不是坏消息，等新数据攒够后会自动重评。
      </p>
    );
  }

  const maxRetention = Math.max(...decided.map((point) => point.retention ?? 0), 1);
  const stepX = points.length > 1 ? (WIDTH - PAD * 2) / (points.length - 1) : 0;
  const xOf = (index: number) => PAD + index * stepX;
  const yOf = (value: number) =>
    HEIGHT - PAD - (Math.max(0, value) / maxRetention) * (HEIGHT - PAD * 2);

  // 断线：只有**相邻两个都可判定**的点之间才连线段。
  const segments: string[] = [];
  points.forEach((point, index) => {
    const next = points[index + 1];
    if (!next || point.undecided || next.undecided) return;
    segments.push(
      `M ${xOf(index)},${yOf(point.retention ?? 0)} L ${xOf(index + 1)},${yOf(next.retention ?? 0)}`,
    );
  });

  const baseline = yOf(1);
  return (
    <div>
      <svg
        width={WIDTH}
        height={HEIGHT}
        role="img"
        aria-label="体检留存度趋势"
        data-testid="health-trend"
      >
        <line
          x1={PAD}
          y1={baseline}
          x2={WIDTH - PAD}
          y2={baseline}
          stroke="var(--border)"
          strokeDasharray="3 3"
          strokeWidth="1"
        />
        {segments.map((path) => (
          <path key={path} d={path} fill="none" stroke="var(--accent)" strokeWidth="1.5" />
        ))}
        {points.map((point, index) => {
          const color = TONE_VAR[HEALTH_STATUS_TONE[point.status] ?? "neutral"];
          const cy = point.undecided ? HEIGHT - PAD : yOf(point.retention ?? 0);
          return (
            <circle
              key={`${point.ts}-${index}`}
              cx={xOf(index)}
              cy={cy}
              r={3}
              fill={point.undecided ? "none" : color}
              stroke={color}
              strokeWidth="1.5"
              data-undecided={point.undecided ? "true" : "false"}
              data-status={point.status}
            >
              {/* 留存度文案走 `formatRetention` 单一实现：另写一份迟早与表格里的数字不一致。 */}
              <title>
                {`${healthStatusCn(point.status)}：留存度 ${formatRetention(point.retention)}`}
              </title>
            </circle>
          );
        })}
      </svg>
      <p className="mt-1 text-[10px] leading-relaxed text-[var(--muted)]">
        虚线 = 与登记时持平（留存度 100%）。空心点表示该次不可判定（暂不评级 / 体检异常），
        <b>不与相邻点连线</b>。所有数值为历史样本统计，不构成收益预期。
      </p>
    </div>
  );
}
