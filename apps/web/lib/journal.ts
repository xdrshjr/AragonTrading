// 交易日志的纯派生与中文文案（ai-trade-audit-journal §六 第 6 条）。
//
// **组件里不写 switch**：降级码 / 退出归因 / 唤醒原因 / 事件码 / 关联置信度的中文化表各一张，
// 全部集中在这里。未知码一律**回退到原码**而不是空串——一个显示成空白的降级原因，
// 会让用户以为「这一轮没有降级」。
//
// ⚠️ **门禁层名是唯一的例外，且方向相反**：本文件里**不许有**任何门禁层名的中文化表。
// 层名一律随 `gates[].label` 从服务端下发（真源 `open_funnel.LAYER_LABELS`）。在前端补一张表
// 就是本仓明令禁止的第二份真源，而它漂移时**不报任何错**。由源码扫描用例
// `components/Journal/journalNoLayerLabels.source.test.ts` 冻结。
import type { CaptureTier, ContextMode, LinkConfidence } from "./journalTypes";

/** 降级码中文化。取自后端 `open_funnel._DEGRADE_PREFIXES` 与 `decider._map_decision` 的码集。 */
const DEGRADE_LABELS: Record<string, string> = {
  decision_timeout: "决策超时",
  llm_error: "模型调用失败",
  no_tool_call: "模型未产出工具调用",
  reasoning_truncated: "输出预算在调用工具之前耗尽",
  content_filtered: "模型侧内容拦截",
  invalid_action: "动作非法",
  missing_size: "缺少仓位比例",
  missing_limit_price: "缺少限价",
  limit_price_out_of_band: "限价超出价带",
  invalid_order_type: "委托方式非法",
  low_confidence: "置信度低于门槛",
  low_consensus: "同向共识不足",
  edge_below_cost: "预期收益低于成本门槛",
  profit_cooldown: "刚做过保护性平仓，正在冷却",
  drawdown_brake: "回撤刹车",
  loss_streak: "连亏熔断",
  // ⚠️ 本表里若干条的文案**刻意避开**了门禁层名的字面（层名的真源在后端 `open_funnel`，
  // 前端一个字都不许抄）。它们是**降级码**的中文化，与层名是两回事；写成同一串字会让
  // `journalNoLayerLabels.source.test.ts` 那道门禁误判成「前端抄了第二份层名表」，
  // 而放宽那道门禁的代价远大于在这里换一个说法。**这条注释自己也不许引用层名原文**——
  // 门禁扫的是源码原文，不区分注释与代码（那正是它拦得住「抄一张表进来」的原因）。
  orphan_position: "交易所侧有一笔未接管的持仓",
  exit_order_slot_busy: "挂单槽位被占用，本轮未平成",
};

/** 事件码中文化。**码是稳定契约**：新增码必须同时进后端码表与这张表。 */
const EVENT_LABELS: Record<string, string> = {
  GROUP_STARTED: "分组已启动",
  GROUP_STOPPED: "分组已停止",
  GROUP_ENDED: "分组已归档",
  GROUP_PAUSED: "分组已暂停",
  GROUP_RESUMED: "分组已继续",
  DECISION_DEGRADED: "本轮决策降级",
  ROUND_SKIPPED: "本轮被整轮跳过",
  AI_TRADE_ROUND_FAILED: "本轮决策异常",
  LIVENESS_STALLED: "活性看门狗告警",
  LIVENESS_HALTED: "活性看门狗整组停手",
  FEED_DEGRADED: "行情降级",
  FEED_RECOVERED: "行情已自愈",
  ORPHAN_POSITION: "侦测到未接管持仓",
  // 同 DEGRADE_LABELS 的那条注释：措辞刻意避开门禁层名的字面（门禁扫源码原文，不区分注释）。
  BACKSTOP_REPAIRED: "兜底委托已补齐",
  BACKSTOP_FAILED: "兜底委托补单失败",
  SHUTDOWN_DROP: "关停时有日志未写入",
};

const TRIGGER_LABELS: Record<string, string> = {
  candle: "K 线确认",
  interval: "定时唤醒",
  monitor: "盯市 tick",
  intel: "情报唤醒",
  manual: "手动触发",
};

const EXIT_SOURCE_LABELS: Record<string, string> = {
  agent: "模型自主平仓",
  agentOverride: "模型覆盖离场",
  profit_guard: "利润保护",
  profitGuard: "利润保护",
  stop_loss: "止损",
  take_profit: "止盈",
  external: "外部平仓",
  session_exit: "会话终结平仓",
  // 同上：这一条说的是**退出归因**，与同名的那一道门禁层不是一回事，故换一个不撞的说法。
  backstop: "由交易所侧的兜底委托成交",
  unknown: "未归因",
};

const ACTION_LABELS: Record<string, string> = {
  open_long: "开多",
  open_short: "开空",
  close: "平仓",
  hold: "观望",
  cancel_order: "撤单",
};

const LINK_LABELS: Record<LinkConfidence, string> = {
  exact: "按交易所单号精确关联",
  heuristic: "按时间与数量启发式关联",
  none: "未关联到成交",
};

/** 关联置信度的**形状**记号：语义绝不靠颜色单独承载（形状 + 文案 + 颜色三者齐全）。 */
const LINK_GLYPHS: Record<LinkConfidence, string> = {
  exact: "✓",
  heuristic: "≈",
  none: "—",
};

const TIER_LABELS: Record<CaptureTier, string> = {
  minimal: "精简（只留结论与门禁链）",
  standard: "标准（含上下文与模型输出）",
  full: "完整（含提示词原文与深度思考）",
};

const CONTEXT_MODE_LABELS: Record<ContextMode, string> = {
  full: "整段多轮对话（与模型当时看到的一致）",
  lastTurnOnly: "只含最后一轮（与模型当时看到的不同）",
};

/** 九个 skipped 桶的中文名。`contextIncomplete` 与别的桶分列是刻意的（见 spec §2.5.2）。 */
const SKIPPED_LABELS: Record<string, string> = {
  gateVetoed: "被确定性门禁改写或否决",
  degraded: "本轮决策降级（输出是系统合成的观望）",
  unsettled: "尚未产生完整往返结果",
  hold: "观望轮",
  lowConfidence: "关联置信度不足",
  promptDrift: "提示词无法逐字重建",
  payloadTruncated: "上下文被截断",
  minimalTier: "采集档为精简，不含模型输入输出",
  contextIncomplete: "上下文链重建不出来（前驱轮缺席）",
};

const _EVENT_LEVEL_LABELS: Record<string, string> = {
  info: "信息",
  warn: "告警",
  error: "错误",
};

function lookup(table: Record<string, string>, code: string | null | undefined): string {
  if (!code) return "";
  // 未知码回退到**原码**而不是空串：一个显示成空白的降级原因会被读成「没有降级」。
  return table[code] ?? code;
}

export const degradeLabel = (code: string | null | undefined) => lookup(DEGRADE_LABELS, code);
export const eventLabel = (code: string | null | undefined) => lookup(EVENT_LABELS, code);
export const triggerLabel = (code: string | null | undefined) => lookup(TRIGGER_LABELS, code);
export const exitSourceLabel = (code: string | null | undefined) =>
  lookup(EXIT_SOURCE_LABELS, code);
export const actionLabel = (code: string | null | undefined) => lookup(ACTION_LABELS, code);
export const eventLevelLabel = (code: string | null | undefined) =>
  lookup(_EVENT_LEVEL_LABELS, code);
export const skippedLabel = (key: string) => lookup(SKIPPED_LABELS, key);
export const captureTierLabel = (tier: CaptureTier) => TIER_LABELS[tier] ?? tier;
export const contextModeLabel = (mode: ContextMode) => CONTEXT_MODE_LABELS[mode] ?? mode;

export function linkConfidenceLabel(value: LinkConfidence | null | undefined): string {
  return value ? LINK_LABELS[value] ?? value : LINK_LABELS.none;
}

export function linkConfidenceGlyph(value: LinkConfidence | null | undefined): string {
  return value ? LINK_GLYPHS[value] ?? "—" : "—";
}

/** 关联置信度的色调（`Badge` 的 tone）。颜色只是三重编码里的一重。 */
export function linkConfidenceTone(
  value: LinkConfidence | null | undefined,
): "neutral" | "long" | "accent" {
  if (value === "exact") return "long";
  if (value === "heuristic") return "accent";
  return "neutral";
}

/** 门禁三态 + 未知态的中文说明。**层名不在这里**——它随 wire 下发。 */
export function gateVerdictLabel(verdict: string, armed: boolean | null): string {
  if (armed === null) return "未知（该层未上报装配状态）";
  if (verdict === "veto") return "否决";
  if (verdict === "skip") return "未装配（本会话未开启）";
  return "放行";
}

/**
 * 体积格式化（健康卡与导出预检共用）。
 *
 * ⚠️ `null` / `undefined` 必须**先于** `Number()` 判掉：`Number(null) === 0`，不先判就会把
 * 「没这个读数」显示成「0 B」——那正是本轮从头到尾拒绝的那种补零。
 */
export function formatBytes(bytes: number | null | undefined): string {
  if (bytes === null || bytes === undefined) return "—";
  const value = Number(bytes);
  if (!Number.isFinite(value) || value < 0) return "—";
  if (value < 1024) return `${value} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let scaled = value / 1024;
  let unit = 0;
  while (scaled >= 1024 && unit < units.length - 1) {
    scaled /= 1024;
    unit += 1;
  }
  return `${scaled.toFixed(scaled >= 100 ? 0 : 1)} ${units[unit]}`;
}

/** 时长格式化（预估耗时）。`null` / `undefined` 同款先判（见 `formatBytes` 的理由）。 */
export function formatSeconds(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return "—";
  const value = Number(seconds);
  if (!Number.isFinite(value) || value < 0) return "—";
  if (value < 60) return `${Math.round(value)} 秒`;
  const minutes = Math.floor(value / 60);
  const rest = Math.round(value % 60);
  return rest ? `${minutes} 分 ${rest} 秒` : `${minutes} 分钟`;
}

/** 秒级 UTC ts → `YYYY-MM-DD HH:mm:ss`（UTC）。审计口径必须无歧义，故两列并排显示。 */
export function formatUtc(ts: number | null | undefined): string {
  if (ts === null || ts === undefined) return "—";
  const value = Number(ts);
  if (!Number.isFinite(value) || value <= 0) return "—";
  return new Date(value * 1000).toISOString().replace("T", " ").slice(0, 19);
}

/** 秒级 UTC ts → 本地时间。 */
export function formatLocal(ts: number | null | undefined): string {
  if (ts === null || ts === undefined) return "—";
  const value = Number(ts);
  if (!Number.isFinite(value) || value <= 0) return "—";
  const d = new Date(value * 1000);
  const pad = (n: number) => String(n).padStart(2, "0");
  return (
    `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ` +
    `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
  );
}

/** 数字格式化：`null` / 非有限数一律 `—`，**绝不显示 0**（`Number(null) === 0`，故先判）。 */
export function formatNumber(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined) return "—";
  const num = Number(value);
  return Number.isFinite(num) ? num.toFixed(digits) : "—";
}
