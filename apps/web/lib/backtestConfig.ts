// 回测配置纯函数（strategy-backtest-config-modal §4.3）：区间预设换算、bar→秒、估算根数、
// 校验、默认成本、标签映射、datetime-local ⇄ UTC 秒。无副作用、易单测，供弹窗与面板共用。
import type { Bar, BacktestConfig, RangePreset } from "./types";

export const BARS: Bar[] = ["1m", "5m", "15m", "30m", "1H", "4H", "1D"];

export const BAR_LABEL: Record<Bar, string> = {
  "1m": "1 分钟",
  "5m": "5 分钟",
  "15m": "15 分钟",
  "30m": "30 分钟",
  "1H": "1 小时",
  "4H": "4 小时",
  "1D": "1 天",
};

// 区间预设顺序（chips 渲染用）；custom 独立成末位。
export const RANGE_PRESETS: RangePreset[] = ["7d", "30d", "90d", "180d", "1y", "custom"];

export const PRESET_LABEL: Record<RangePreset, string> = {
  "7d": "近 7 天",
  "30d": "近 30 天",
  "90d": "近 90 天",
  "180d": "近 180 天",
  "1y": "近 1 年",
  custom: "自定义",
};

// bar → 秒（与后端 mtf.bar_to_seconds 同表）。
const BAR_SECONDS: Record<Bar, number> = {
  "1m": 60,
  "5m": 300,
  "15m": 900,
  "30m": 1800,
  "1H": 3600,
  "4H": 14400,
  "1D": 86400,
};

export function barToSeconds(bar: Bar): number {
  return BAR_SECONDS[bar] ?? 3600;
}

const DAY_SECONDS = 86400;
const PRESET_DAYS: Record<Exclude<RangePreset, "custom">, number> = {
  "7d": 7,
  "30d": 30,
  "90d": 90,
  "180d": 180,
  "1y": 365,
};

// 预设 → 起止（终点=now，起点=now − 天数×86400）；调用方传入 nowSec 便于测试确定性。
export function presetToRange(
  preset: Exclude<RangePreset, "custom">,
  nowSec: number,
): { startTs: number; endTs: number } {
  return { startTs: nowSec - PRESET_DAYS[preset] * DAY_SECONDS, endTs: nowSec };
}

// 估算区间内应有的已确认 K 线根数：floor((end−start)/barSec)+1；非法区间回 0。
export function estimateBars(bar: Bar, startTs: number, endTs: number): number {
  if (!(endTs > startTs)) return 0;
  return Math.floor((endTs - startTs) / barToSeconds(bar)) + 1;
}

// 默认成本与周期（与后端 settings 默认对齐：资金 10000、费 5bps、滑点 2bps、杠杆 1x、周期 1H、区间 近30天）。
export const DEFAULT_BACKTEST_CONFIG: Omit<BacktestConfig, "startTs" | "endTs"> = {
  bar: "1H",
  rangePreset: "30d",
  initialCapital: 10000,
  feesBps: 5,
  slippageBps: 2,
  leverage: 1,
};

export interface ConfigValidation {
  ok: boolean;
  errors: string[];
  warnings: string[];
}

// 边界校验：start<end、cap>0、fee/slip>=0、lev>=1 为硬错误（禁用运行）；估算根数 < 暖机为软告警（仍可运行）。
export function validateConfig(cfg: BacktestConfig, warmupBars?: number): ConfigValidation {
  const errors: string[] = [];
  const warnings: string[] = [];
  if (!(cfg.startTs < cfg.endTs)) errors.push("起始时间必须早于结束时间");
  if (!(cfg.initialCapital > 0)) errors.push("初始资金必须大于 0");
  if (!(cfg.feesBps >= 0)) errors.push("手续费不能为负");
  if (!(cfg.slippageBps >= 0)) errors.push("滑点不能为负");
  if (!(cfg.leverage >= 1)) errors.push("杠杆不能小于 1");
  if (warmupBars && cfg.startTs < cfg.endTs) {
    const bars = estimateBars(cfg.bar, cfg.startTs, cfg.endTs);
    if (bars < warmupBars) {
      warnings.push(
        `预计约 ${bars} 根 K 线，少于策略暖机所需约 ${warmupBars} 根，区间过短可能安静空仓`,
      );
    }
  }
  return { ok: errors.length === 0, errors, warnings };
}

// 种子：有持久化偏好则原样复用；否则默认成本 + 自定义区间承接当前图表 bar 与回测区间（不臆造陈旧绝对时间）。
export function seedBacktestConfig(opts: {
  stored: BacktestConfig | null;
  bar: Bar;
  startTs: number;
  endTs: number;
}): BacktestConfig {
  if (opts.stored) return opts.stored;
  return {
    ...DEFAULT_BACKTEST_CONFIG,
    bar: opts.bar,
    rangePreset: "custom",
    startTs: opts.startTs,
    endTs: opts.endTs,
  };
}

// 打开弹窗时按 now 重派生预设区间（custom 原样返回），杜绝持久化的「近 30 天」变陈旧。
export function refreshPresetRange(cfg: BacktestConfig, nowSec: number): BacktestConfig {
  if (cfg.rangePreset === "custom") return cfg;
  return { ...cfg, ...presetToRange(cfg.rangePreset, nowSec) };
}

function pad(n: number): string {
  return n < 10 ? `0${n}` : `${n}`;
}

// UTC 秒 → <input type="datetime-local"> 值（本地时间 YYYY-MM-DDTHH:mm）。
export function tsToLocalInput(tsSec: number): string {
  const d = new Date(tsSec * 1000);
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

// datetime-local 值（本地时间）→ UTC 秒；非法输入回 null（由调用方回退原值）。
export function localInputToTs(value: string): number | null {
  const ms = Date.parse(value);
  return Number.isFinite(ms) ? Math.floor(ms / 1000) : null;
}
