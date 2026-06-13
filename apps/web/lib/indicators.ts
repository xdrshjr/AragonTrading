// 技术指标体系：纯函数（SMA/EMA/BOLL/MACD/RSI/KDJ/VOL）+ 统一 Plot 抽象 + 声明式注册表。
// 与 compare.ts / format.ts 同约定：纯函数 + 同名 indicators.test.ts 单测；本文件**只依赖**
// lightweight-charts 的类型（UTCTimestamp）与 types.ts 的 Candle，不引入任何 React / DOM，
// 从而可在 jsdom 外纯逻辑单测。渲染层据 Plot 泛化建系列（line→addLineSeries，histogram→addHistogramSeries），
// 新增指标 = 注册表加一条 + 一个 compute，菜单与渲染都无需改（spec §4.2 / §5）。
import type { UTCTimestamp } from "lightweight-charts";
import type { Candle } from "./types";

// ── 渲染中间表示（指标计算到渲染的唯一中间形态，spec §7）──────────────
export type Pt = { time: UTCTimestamp; value: number };
export type HistPt = Pt & { color?: string };

export type Plot =
  | {
      kind: "line";
      id: string;
      title: string;
      color: string;
      lineWidth?: 1 | 2 | 3 | 4;
      lineStyle?: number; // LineStyle 枚举：0 实线 / 2 虚线
      data: Pt[];
    }
  | { kind: "histogram"; id: string; title: string; data: HistPt[]; base?: number };

export type IndicatorCategory = "overlay" | "pane";
export type IndicatorKey = "ma" | "ema" | "boll" | "macd" | "rsi" | "kdj" | "vol";

// 渲染时从 CSS 变量解析注入的主题色（主题切换可重算着色，spec §4.8）
export type ThemeColors = {
  long: string;
  short: string;
  muted: string;
  accent: string;
  text: string;
};

export interface IndicatorDef {
  key: IndicatorKey;
  label: string; // 中文 / 通用显示名，如 "MACD"
  description: string; // 弹窗副文本 / tooltip
  category: IndicatorCategory;
  defaultOn: boolean;
  paramsHint: string; // 显示用参数提示，如 "(12,26,9)"
  fixedRange?: { min: number; max: number }; // 副图固定值域（如 RSI 0~100）
  refLines?: { value: number; color?: string; title?: string }[]; // 副图参考线
  compute: (candles: Candle[], tc: ThemeColors) => Plot[];
}

// ── 核心数学纯函数（统一约定：暖机期不足时该位返回 undefined，曲线从首个有效点起绘）──

// 简单移动平均：i ≥ period-1 时取窗口均值。
export function sma(values: number[], period: number): (number | undefined)[] {
  const out: (number | undefined)[] = new Array(values.length).fill(undefined);
  if (period <= 0) return out;
  let sum = 0;
  for (let i = 0; i < values.length; i++) {
    sum += values[i];
    if (i >= period) sum -= values[i - period];
    if (i >= period - 1) out[i] = sum / period;
  }
  return out;
}

// 指数移动平均：k=2/(period+1)；以前 period 个值的 SMA 作种子（index=period-1），其后递推。
export function ema(values: number[], period: number): (number | undefined)[] {
  const out: (number | undefined)[] = new Array(values.length).fill(undefined);
  if (period <= 0 || values.length < period) return out;
  const k = 2 / (period + 1);
  let seed = 0;
  for (let i = 0; i < period; i++) seed += values[i];
  seed /= period;
  out[period - 1] = seed;
  let prev = seed;
  for (let i = period; i < values.length; i++) {
    const cur = values[i] * k + prev * (1 - k);
    out[i] = cur;
    prev = cur;
  }
  return out;
}

// 布林带：mid=SMA(period)，std=窗口总体标准差，upper=mid+mult·std，lower=mid-mult·std。
export function bollinger(
  closes: number[],
  period = 20,
  mult = 2,
): { mid: (number | undefined)[]; upper: (number | undefined)[]; lower: (number | undefined)[] } {
  const mid = sma(closes, period);
  const upper: (number | undefined)[] = new Array(closes.length).fill(undefined);
  const lower: (number | undefined)[] = new Array(closes.length).fill(undefined);
  for (let i = 0; i < closes.length; i++) {
    const m = mid[i];
    if (m === undefined) continue;
    let sumSq = 0;
    for (let j = i - period + 1; j <= i; j++) {
      const d = closes[j] - m;
      sumSq += d * d;
    }
    const std = Math.sqrt(sumSq / period); // 总体标准差
    upper[i] = m + mult * std;
    lower[i] = m - mult * std;
  }
  return { mid, upper, lower };
}

// MACD：dif=EMA(fast)-EMA(slow)（从 slow-1 起有效）；dea=EMA(dif,signal)；hist=(dif-dea)·2（国内惯例 ×2）。
export function macd(
  closes: number[],
  fast = 12,
  slow = 26,
  signal = 9,
): { dif: (number | undefined)[]; dea: (number | undefined)[]; hist: (number | undefined)[] } {
  const emaFast = ema(closes, fast);
  const emaSlow = ema(closes, slow);
  const dif: (number | undefined)[] = new Array(closes.length).fill(undefined);
  for (let i = 0; i < closes.length; i++) {
    const f = emaFast[i];
    const s = emaSlow[i];
    if (f !== undefined && s !== undefined) dif[i] = f - s;
  }
  // dea = EMA(dif, signal)：dif 前段为 undefined，须对压实后的有效子序列求 EMA 再映射回原下标。
  const compactVals: number[] = [];
  const compactIdx: number[] = [];
  for (let i = 0; i < dif.length; i++) {
    const v = dif[i];
    if (v !== undefined) {
      compactVals.push(v);
      compactIdx.push(i);
    }
  }
  const deaCompact = ema(compactVals, signal);
  const dea: (number | undefined)[] = new Array(closes.length).fill(undefined);
  for (let k = 0; k < compactIdx.length; k++) {
    const v = deaCompact[k];
    if (v !== undefined) dea[compactIdx[k]] = v;
  }
  const hist: (number | undefined)[] = new Array(closes.length).fill(undefined);
  for (let i = 0; i < closes.length; i++) {
    const di = dif[i];
    const de = dea[i];
    if (di !== undefined && de !== undefined) hist[i] = (di - de) * 2;
  }
  return { dif, dea, hist };
}

// RSI（Wilder 平滑）：首个 avgGain/avgLoss 取前 period 根涨跌幅简单均值（首个有效点在 index=period），
// 其后 avg=(prevAvg·(period-1)+cur)/period；RSI=100-100/(1+avgGain/avgLoss)；avgLoss=0 时 RSI=100。
export function rsi(closes: number[], period: number): (number | undefined)[] {
  const out: (number | undefined)[] = new Array(closes.length).fill(undefined);
  if (period <= 0 || closes.length <= period) return out;
  let gainSum = 0;
  let lossSum = 0;
  for (let i = 1; i <= period; i++) {
    const ch = closes[i] - closes[i - 1];
    if (ch >= 0) gainSum += ch;
    else lossSum += -ch;
  }
  let avgGain = gainSum / period;
  let avgLoss = lossSum / period;
  out[period] = avgLoss === 0 ? 100 : 100 - 100 / (1 + avgGain / avgLoss);
  for (let i = period + 1; i < closes.length; i++) {
    const ch = closes[i] - closes[i - 1];
    const gain = ch > 0 ? ch : 0;
    const loss = ch < 0 ? -ch : 0;
    avgGain = (avgGain * (period - 1) + gain) / period;
    avgLoss = (avgLoss * (period - 1) + loss) / period;
    out[i] = avgLoss === 0 ? 100 : 100 - 100 / (1 + avgGain / avgLoss);
  }
  return out;
}

// KDJ：RSV=(C-LLV(low,n))/(HHV(high,n)-LLV(low,n))·100（分母 0 取 50）；
// K=(m1-1)/m1·prevK+1/m1·RSV（种子 50）；D=(m2-1)/m2·prevD+1/m2·K（种子 50）；J=3K-2D。
export function kdj(
  candles: Candle[],
  n = 9,
  m1 = 3,
  m2 = 3,
): { k: (number | undefined)[]; d: (number | undefined)[]; j: (number | undefined)[] } {
  const len = candles.length;
  const k: (number | undefined)[] = new Array(len).fill(undefined);
  const d: (number | undefined)[] = new Array(len).fill(undefined);
  const j: (number | undefined)[] = new Array(len).fill(undefined);
  let prevK = 50;
  let prevD = 50;
  for (let i = 0; i < len; i++) {
    if (i < n - 1) continue;
    let ll = Infinity;
    let hh = -Infinity;
    for (let p = i - n + 1; p <= i; p++) {
      if (candles[p].l < ll) ll = candles[p].l;
      if (candles[p].h > hh) hh = candles[p].h;
    }
    const denom = hh - ll;
    const rsv = denom === 0 ? 50 : ((candles[i].c - ll) / denom) * 100;
    const curK = ((m1 - 1) / m1) * prevK + (1 / m1) * rsv;
    const curD = ((m2 - 1) / m2) * prevD + (1 / m2) * curK;
    k[i] = curK;
    d[i] = curD;
    j[i] = 3 * curK - 2 * curD;
    prevK = curK;
    prevD = curD;
  }
  return { k, d, j };
}

// ── 计算辅助：把「与 candles 一一对应、含暖机 undefined」的值数组转成只含有效点的 Pt[] ──
function lineData(candles: Candle[], vals: (number | undefined)[]): Pt[] {
  const out: Pt[] = [];
  for (let i = 0; i < candles.length; i++) {
    const v = vals[i];
    if (v === undefined || Number.isNaN(v)) continue;
    out.push({ time: candles[i].ts as UTCTimestamp, value: v });
  }
  return out;
}

// ── 多子线指标的固定配色（在明/暗双主题下均可读；涨跌色由 tc.long/tc.short 注入）──
const MA_PERIODS = [5, 10, 20, 60] as const;
const MA_COLORS = ["#e8a33d", "#3b82f6", "#a855f7", "#14b8a6"];
const EMA_PERIODS = [12, 26] as const;
const EMA_COLORS = ["#ec4899", "#06b6d4"];
const RSI_PERIODS = [6, 12, 24] as const;
const TRIO_COLORS = ["#e8a33d", "#3b82f6", "#a855f7"]; // RSI / KDJ 三线共用
const MACD_DIF_COLOR = "#2962ff";
const MACD_DEA_COLOR = "#ff6d00";

// ── 声明式注册表（数组顺序即菜单顺序：先主图叠加，后副图）──
export const INDICATORS: IndicatorDef[] = [
  {
    key: "ma",
    label: "MA 均线",
    description: "多周期简单移动平均",
    category: "overlay",
    defaultOn: true,
    paramsHint: "(5,10,20,60)",
    compute: (candles) => {
      const closes = candles.map((c) => c.c);
      return MA_PERIODS.map((p, idx) => ({
        kind: "line" as const,
        id: String(p),
        title: `MA${p}`,
        color: MA_COLORS[idx],
        lineWidth: 1 as const,
        data: lineData(candles, sma(closes, p)),
      }));
    },
  },
  {
    key: "ema",
    label: "EMA 指数均线",
    description: "指数加权移动平均",
    category: "overlay",
    defaultOn: false,
    paramsHint: "(12,26)",
    compute: (candles) => {
      const closes = candles.map((c) => c.c);
      return EMA_PERIODS.map((p, idx) => ({
        kind: "line" as const,
        id: String(p),
        title: `EMA${p}`,
        color: EMA_COLORS[idx],
        lineWidth: 1 as const,
        data: lineData(candles, ema(closes, p)),
      }));
    },
  },
  {
    key: "boll",
    label: "BOLL 布林带",
    description: "上中下三轨（中轨虚线）",
    category: "overlay",
    defaultOn: true,
    paramsHint: "(20,2)",
    compute: (candles, tc) => {
      const closes = candles.map((c) => c.c);
      const { mid, upper, lower } = bollinger(closes, 20, 2);
      return [
        {
          kind: "line",
          id: "upper",
          title: "BOLL 上轨",
          color: tc.accent,
          lineWidth: 1,
          data: lineData(candles, upper),
        },
        {
          kind: "line",
          id: "mid",
          title: "BOLL 中轨",
          color: tc.muted,
          lineWidth: 1,
          lineStyle: 2,
          data: lineData(candles, mid),
        },
        {
          kind: "line",
          id: "lower",
          title: "BOLL 下轨",
          color: tc.accent,
          lineWidth: 1,
          data: lineData(candles, lower),
        },
      ];
    },
  },
  {
    key: "macd",
    label: "MACD",
    description: "平滑异同移动平均",
    category: "pane",
    defaultOn: true,
    paramsHint: "(12,26,9)",
    compute: (candles, tc) => {
      const closes = candles.map((c) => c.c);
      const { dif, dea, hist } = macd(closes, 12, 26, 9);
      const histData: HistPt[] = [];
      for (let i = 0; i < candles.length; i++) {
        const v = hist[i];
        if (v === undefined || Number.isNaN(v)) continue;
        histData.push({
          time: candles[i].ts as UTCTimestamp,
          value: v,
          color: v >= 0 ? tc.long : tc.short,
        });
      }
      return [
        { kind: "histogram", id: "hist", title: "MACD", data: histData, base: 0 },
        {
          kind: "line",
          id: "dif",
          title: "DIF",
          color: MACD_DIF_COLOR,
          lineWidth: 1,
          data: lineData(candles, dif),
        },
        {
          kind: "line",
          id: "dea",
          title: "DEA",
          color: MACD_DEA_COLOR,
          lineWidth: 1,
          data: lineData(candles, dea),
        },
      ];
    },
  },
  {
    key: "rsi",
    label: "RSI 相对强弱",
    description: "0~100 强弱区间",
    category: "pane",
    defaultOn: false,
    paramsHint: "(6,12,24)",
    fixedRange: { min: 0, max: 100 },
    refLines: [
      { value: 70, title: "70" },
      { value: 50 },
      { value: 30, title: "30" },
    ],
    compute: (candles) => {
      const closes = candles.map((c) => c.c);
      return RSI_PERIODS.map((p, idx) => ({
        kind: "line" as const,
        id: String(p),
        title: `RSI${p}`,
        color: TRIO_COLORS[idx],
        lineWidth: 1 as const,
        data: lineData(candles, rsi(closes, p)),
      }));
    },
  },
  {
    key: "kdj",
    label: "KDJ 随机指标",
    description: "随机指标 K / D / J",
    category: "pane",
    defaultOn: false,
    paramsHint: "(9,3,3)",
    compute: (candles) => {
      const { k, d, j } = kdj(candles, 9, 3, 3);
      return [
        { kind: "line", id: "k", title: "K", color: TRIO_COLORS[0], lineWidth: 1, data: lineData(candles, k) },
        { kind: "line", id: "d", title: "D", color: TRIO_COLORS[1], lineWidth: 1, data: lineData(candles, d) },
        { kind: "line", id: "j", title: "J", color: TRIO_COLORS[2], lineWidth: 1, data: lineData(candles, j) },
      ];
    },
  },
  {
    key: "vol",
    label: "VOL 成交量",
    description: "按 K 线涨跌着色的成交量",
    category: "pane",
    defaultOn: false,
    paramsHint: "",
    compute: (candles, tc) => {
      const data: HistPt[] = candles.map((c) => ({
        time: c.ts as UTCTimestamp,
        value: c.vol,
        color: c.c >= c.o ? tc.long : tc.short,
      }));
      return [{ kind: "histogram", id: "vol", title: "VOL", data, base: 0 }];
    },
  },
];

export function getIndicator(key: string): IndicatorDef | undefined {
  return INDICATORS.find((d) => d.key === key);
}

// 由注册表 defaultOn 派生的默认开关态（store 初值 / 恢复默认 / 持久化迁移目标）。
export const DEFAULT_INDICATORS: Record<IndicatorKey, boolean> = INDICATORS.reduce(
  (acc, d) => {
    acc[d.key] = d.defaultOn;
    return acc;
  },
  {} as Record<IndicatorKey, boolean>,
);
