// 因子值 / 复合得分序列 → lightweight-charts 折线点位映射（复用 ohlc/indicators 范式）。
// 时间用秒级 UTC（与 K 线一致）；空安全 + 去重升序，供 FactorScoreChart 振荡 pane 消费。
import type { FactorPoint, ScorePoint } from "./factorTypes";

export interface LinePoint {
  time: number; // 秒级 UTC（lightweight-charts UTCTimestamp）
  value: number;
}

function dedupeSorted(rows: LinePoint[]): LinePoint[] {
  const map = new Map<number, number>();
  for (const r of rows) {
    if (Number.isFinite(r.value)) map.set(r.time, r.value);
  }
  return Array.from(map.entries())
    .map(([time, value]) => ({ time, value }))
    .sort((a, b) => a.time - b.time);
}

export function factorPointsToLine(points: FactorPoint[]): LinePoint[] {
  return dedupeSorted(points.map((p) => ({ time: p.ts, value: p.value })));
}

export function scorePointsToLine(points: ScorePoint[]): LinePoint[] {
  return dedupeSorted(points.map((p) => ({ time: p.ts, value: p.score })));
}

// 把一组点归一到 [0,1] 的 y 坐标（SVG sparkline 用）；返回 {minV,maxV,coords}。
export interface SparkGeom {
  minV: number;
  maxV: number;
  coords: { x: number; y: number }[];
}

export function sparkline(points: LinePoint[], width: number, height: number, pad = 2): SparkGeom {
  if (points.length === 0) return { minV: 0, maxV: 0, coords: [] };
  const values = points.map((p) => p.value);
  const minV = Math.min(...values);
  const maxV = Math.max(...values);
  const span = maxV - minV || 1;
  const innerW = Math.max(1, width - pad * 2);
  const innerH = Math.max(1, height - pad * 2);
  const n = Math.max(1, points.length - 1);
  const coords = points.map((p, i) => ({
    x: pad + (innerW * i) / n,
    y: pad + innerH - ((p.value - minV) / span) * innerH,
  }));
  return { minV, maxV, coords };
}
