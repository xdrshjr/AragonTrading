// 交易经验库的展示层常量与纯格式化函数（ai-trade-experience-context-graph §七）。
//
// **全部住在本文件**：`lib/aiTrade.ts` 实测 1412 行，已超 CLAUDE.md 的 800 行阈值，本特性在
// 那边的净增预算是 **0**。
//
// 中文词表与后端 `experience/taxonomy.py` 的 `AXIS_LABEL_ZH` / `VALUE_LABEL_ZH` **逐字同步**，
// 由源码扫描用例 `experienceLabels.source.test.ts` 钉死（正则须写 `\r?\n`——本仓库
// `core.autocrlf=true`，写死 `\n` 会在干净检出上恒红）。
import type {
  AiTradeExperienceRule,
  ExperienceMaterializeHealth,
  ExperienceNode,
  ExperienceSource,
  ExperienceTier,
} from "./experienceTypes";

/**
 * 启动弹窗的默认值。
 *
 * 需求明写「默认开启」。真正让它敢默认开启的不是这个常量，而是**影响方向单侧**：经验只进
 * prompt，反思官的裁决空间只有 confirm / abort / 缩仓，所以最坏后果恒为「少做一笔」，
 * 永远不会是「多做、做反、做大」。
 */
export const AI_TRADE_EXPERIENCE_DEFAULTS: AiTradeExperienceRule = {
  enabled: true,
  scope: "symbol",
  write: true,
};

/** 语境轴的中文展示名（与后端 `taxonomy.AXIS_LABEL_ZH` 逐字一致）。 */
export const AXIS_LABEL_ZH: Record<string, string> = {
  symbol: "标的",
  bar: "周期",
  regime: "体制",
  volatility: "波动",
  event: "事件",
  side: "方向",
  wake: "唤醒",
  exit: "退出",
  horizon: "持仓周期",
  cost_regime: "成本档",
  confidence: "置信度",
};

/** 语境取值的中文展示名（与后端 `taxonomy.VALUE_LABEL_ZH` 逐字一致）。 */
export const VALUE_LABEL_ZH: Record<string, string> = {
  trend_up: "上升趋势",
  trend_down: "下降趋势",
  range: "震荡",
  high_volatility: "高波动",
  low_volatility: "低波动",
  normal: "常波动",
  crash: "急跌",
  breakout: "突破",
  none: "无事件",
  long: "做多",
  short: "做空",
  candle_close: "新K线收线",
  bar_advanced: "补跑上根",
  signal_flip: "信号翻转",
  resting_order: "挂单待成",
  profit_tier: "利润档位",
  price_breakout: "价格异动",
  position_watch: "持仓盯市",
  heartbeat: "心跳",
  agent: "Agent主动平",
  agentReverse: "Agent反手",
  profitGuard: "利润保护",
  exchangeOco: "交易所OCO",
  timeExit: "到期离场",
  sessionStop: "会话止盈损",
  windowEnd: "区间结束",
  external: "外部干预",
  hourly_swing: "小时波段",
  intraday_swing: "日内波段",
  short_swing: "短波段",
  trend_hold: "趋势持仓",
  custom: "自定义",
  low: "低",
  mid: "中",
  high: "高",
  unknown: "未知",
};

/** 各轴的可选取值（预演选择器与手工新增表单共用；`symbol` / `bar` 由调用方按实际库存给）。 */
export const AXIS_VALUES: Record<string, string[]> = {
  regime: ["trend_up", "trend_down", "range", "unknown"],
  volatility: ["high_volatility", "low_volatility", "normal", "unknown"],
  event: ["crash", "breakout", "none", "unknown"],
  side: ["long", "short", "unknown"],
  wake: ["candle_close", "bar_advanced", "signal_flip", "resting_order", "profit_tier",
         "price_breakout", "position_watch", "heartbeat", "unknown"],
  exit: ["agent", "agentReverse", "profitGuard", "exchangeOco", "timeExit", "sessionStop",
         "windowEnd", "external", "unknown"],
  horizon: ["hourly_swing", "intraday_swing", "short_swing", "trend_hold", "custom",
            "none", "unknown"],
  cost_regime: ["low", "mid", "high", "unknown"],
  confidence: ["low", "mid", "high", "unknown"],
};

/** 档位的中文名（`archived` 只在版本链里出现）。 */
export const TIER_LABEL_ZH: Record<ExperienceTier, string> = {
  iron: "铁律",
  normal: "常规",
  candidate: "候选",
  archived: "历史版本",
};

/** 来源的中文名。 */
export const SOURCE_LABEL_ZH: Record<ExperienceSource, string> = {
  stat: "统计",
  narrative: "叙事",
  manual: "手工",
  imported: "导入",
  context: "语境",
};

/** 轴的中文名；未登记的轴原样回显（绝不显示一个空字符串让用户以为界面坏了）。 */
export function axisLabel(axis: string): string {
  return AXIS_LABEL_ZH[axis] ?? axis;
}

/** 取值的中文名；标的名与未登记取值原样回显。 */
export function valueLabel(value: string): string {
  return VALUE_LABEL_ZH[value] ?? value;
}

/** 把一个语境签名压成一句人读的短语（`高波动+突破 做多`）。 */
export function describeSignature(signature: Record<string, string>): string {
  const parts = Object.entries(signature)
    .filter(([axis, value]) => axis !== "symbol" && axis !== "bar" && value !== "unknown")
    .map(([, value]) => valueLabel(value));
  return parts.length ? parts.join("+") : "全局";
}

/** 相对时间（「3 分钟前」）；0 / 缺席 ⇒ 「尚未」——**绝不显示 1970**。 */
export function relativeTime(ts: number | null | undefined, nowMs = Date.now()): string {
  if (!ts) return "尚未";
  const seconds = Math.max(0, Math.round(nowMs / 1000 - ts));
  if (seconds < 60) return "刚刚";
  if (seconds < 3600) return `${Math.floor(seconds / 60)} 分钟前`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)} 小时前`;
  return `${Math.floor(seconds / 86400)} 天前`;
}

/** 绝对时刻（`08-24 21:30`）；0 ⇒ 空串。 */
export function shortTime(ts: number | null | undefined): string {
  if (!ts) return "";
  const date = new Date(ts * 1000);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

/** 字符预算文案（`312 / 480 字`）。 */
export function budgetText(used: number, budget: number): string {
  return `${used} / ${budget} 字`;
}

/**
 * 比率类统计量（`winRate` / `costShare`）的百分比文案；缺席 ⇒ 「—」。
 *
 * **它们在 wire 里是 0~1 的小数**（`aggregate.compute_stats`：`wins / n`、
 * `meanCostBps / meanGrossBps`），后端渲染 L0 文案时同样要 `× 100`（`aggregate._pct`）。
 * 直接 `toFixed(0) + "%"` 会把 38% 的胜率显示成「0%」、61% 显示成「1%」——一个连自己的胜率
 * 都写错的看板，比没有这一栏更坏。
 */
export function formatPercent(value: unknown, digits = 0): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return "—";
  return `${(value * 100).toFixed(digits)}%`;
}

/** 一条经验的 t 统计量（用于列表列）；缺席 ⇒ 「—」。 */
export function formatTStat(value: unknown): string {
  return typeof value === "number" && Number.isFinite(value) ? value.toFixed(1) : "—";
}

/** 一条经验的净额均值 bps；缺席 ⇒ 「—」。 */
export function formatNetBps(value: unknown): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return "—";
  return `${value > 0 ? "+" : ""}${value.toFixed(1)}bps`;
}

/** 一条经验在列表 / 抽屉里的档位标签（静音优先——它是用户看到的第一个事实）。 */
export function tierLabel(node: Pick<ExperienceNode, "tier" | "muted" | "pinned">): string {
  if (node.muted) return "已沉降";
  if (node.pinned) return "已置顶";
  return TIER_LABEL_ZH[node.tier] ?? node.tier;
}

/** 概览条底部注记的一条（诚实性键的渲染载体，I-8）。 */
export interface HonestyNote {
  text: string;
  /** true ⇒ 用 `--warn` 色（规模墙 / 撕裂 / 溢出三种「说话」的状态）。 */
  warn: boolean;
}

/**
 * 把物化健康与两个采集缺口翻译成用户能据以行动的句子。
 *
 * **这三个状态位的存在理由是同一条**：一个跑不完的后台任务如果不说话，用户看到的就是
 * 「经验不再更新了，但哪里都没有报错」。所以每一条都必须带一个**可执行**的建议，
 * 而不是一句「异常」。
 */
export function honestyNotes(input: {
  health: ExperienceMaterializeHealth | null | undefined;
  excludedByCostBasis: number;
  harvestGapTotal: number;
}): HonestyNote[] {
  const notes: HonestyNote[] = [];
  const health = input.health;
  if (health && health.lastTs) {
    notes.push({
      text: `上次重建：${shortTime(health.lastTs)} · 耗时 ${(health.lastMs / 1000).toFixed(1)}s`
        + ` · ${health.events} 条事件样本`,
      warn: false,
    });
  }
  if (health?.overflow) {
    notes.push({
      text: `经验库已超过自动重建规模（${health.events} 条事件），自动重建已停止。`
        + "请手动重建，或导出归档后再继续积累。",
      warn: true,
    });
  } else if (health?.slow) {
    notes.push({
      text: "经验库规模已接近自动重建的预算，后台重建随时可能跟不上。建议手动重建或导出归档。",
      warn: true,
    });
  }
  if (health?.torn) {
    notes.push({
      text: "上次重建未走完全部步骤，图可能不完整（新沉淀的经验可能暂时检索不到）。建议手动重建。",
      warn: true,
    });
  }
  if (input.excludedByCostBasis > 0) {
    notes.push({
      text: `另有 ${input.excludedByCostBasis} 笔往返因成本口径不可比未计入 bps 统计。`,
      warn: false,
    });
  }
  if (input.harvestGapTotal > 0) {
    notes.push({
      text: `另有 ${input.harvestGapTotal} 笔往返因续跑接管 / 暂停期离线平仓未能采集——`
        + "这些往返偏向亏损样本，本表因此偏乐观。",
      warn: false,
    });
  }
  return notes;
}

/**
 * 检索预演顶部那条状态行（§7.1）。
 *
 * 预演读的是**当前 DB 里的图**，运行中的会话用的是**启动那一刻**载入内存的快照。少了这句，
 * 这一屏在最需要它的时刻（会话正在跑）说的是假话——**一屏假话比没有这一屏更坏。**
 */
export function previewParityText(
  live: { instId: string; graphAsOfTs: number; diverged: boolean }[],
): string {
  if (!live.length) return "这就是下一次启动会话时 Agent 会看到的内容。";
  const diverged = live.filter((row) => row.diverged);
  if (!diverged.length) {
    return `这就是 ${live.map((row) => row.instId).join(" / ")} 的 Agent 下一轮会看到的内容。`;
  }
  const names = diverged.map((row) => `${row.instId}（快照 ${shortTime(row.graphAsOfTs)}）`);
  return `${names.join("、")} 的会话正在使用启动时的经验库快照，与下方预演（当前库）不同`
    + "——新沉淀的经验要到下次自动重建或下次启动会话时才会生效。";
}
