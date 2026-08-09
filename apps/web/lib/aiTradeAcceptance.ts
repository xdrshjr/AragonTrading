// 周期验收卡与退出来源卡的前端共用件（ai-trade-horizon-acceptance-loop §2.5）。
//
// **本文件不含任何阈值判定逻辑**：判定只在后端（`horizon_acceptance.build_acceptance`），
// 三个世界（运行期 / 历史详情 / 回测面板）读的是同一份已判定的 wire。前端再判一次就会立刻造出
// 第二个口径——`edgeSource` 与 `aiTradeObservability.ts` 已经为这件事出过事故。
//
// 同理，行标签、门槛文案、display 串全部由后端下发，此处只做**呈现层**的映射（颜色 / 徽标 /
// 兜底文案）与一条渲染门控。
import type {
  AiTradeAcceptance,
  AiTradeAcceptanceVerdict,
  AiTradeExitMix,
  AiTradeExitSourceRow,
} from "./aiTradeTypes";

export type { AiTradeAcceptance, AiTradeExitMix } from "./aiTradeTypes";

// 三态徽标。`insufficient` 用 **--muted（灰）而不是黄色**：黄会被读成「快好了」，
// 而样本不足的语义是「什么都还不知道」——这条区分是 L1 放行纪律唯一的视觉保证。
export const ACCEPTANCE_VERDICT_META: Record<
  AiTradeAcceptanceVerdict,
  { label: string; icon: string; color: string }
> = {
  pass: { label: "已通过", icon: "✓", color: "var(--success, var(--long))" },
  fail: { label: "未通过", icon: "⛔", color: "var(--danger, var(--short))" },
  insufficient: { label: "样本不足", icon: "…", color: "var(--muted)" },
};

// 九值退出来源的中文标签（与后端 `acceptance_text.EXIT_SOURCE_LABELS` 镜像；后端 wire 只发枚举
// 值，故此处保留一份呈现层映射——它不是判定口径，不会造成读数分叉）。
export const EXIT_SOURCE_LABELS: Record<string, string> = {
  agent: "Agent 主动平仓",
  agentReverse: "Agent 反手",
  profitGuard: "利润保护层",
  exchangeOco: "交易所止盈止损",
  timeExit: "强制时间平仓",
  sessionStop: "会话止盈损",
  windowEnd: "区间收尾平仓",
  external: "外部 / 强平",
  unknown: "未记录来源",
};

// 堆叠比例条的配色：全部取自既有 `--chart-*` 设计 token，不新增任何一个。
const EXIT_SOURCE_COLORS: Record<string, string> = {
  agent: "var(--chart-1, var(--accent))",
  agentReverse: "var(--chart-2, var(--accent))",
  profitGuard: "var(--chart-3, var(--long))",
  exchangeOco: "var(--chart-4, var(--short))",
  timeExit: "var(--chart-5, var(--muted))",
  sessionStop: "var(--chart-6, var(--muted))",
  windowEnd: "var(--chart-7, var(--muted))",
  external: "var(--chart-8, var(--muted))",
  unknown: "var(--border)",
};

/** 退出来源标签（未知枚举照原样显示，绝不静默吞掉一个后端新增的来源）。 */
export function exitSourceLabel(source: string): string {
  return EXIT_SOURCE_LABELS[source] ?? source;
}

export function exitSourceColor(source: string): string {
  return EXIT_SOURCE_COLORS[source] ?? "var(--border)";
}

/**
 * 退出来源卡是否值得渲染。
 *
 * **只有 `unknown` 一种来源 ⇒ 不渲染**：老会话不该看到一张全是「未记录来源」的表——那不是信息，
 * 是噪音（沿用 `WakeAttributionCard` 已立好的同款纪律）。
 */
export function hasExitAttribution(exits: AiTradeExitMix | null | undefined): boolean {
  if (!exits || exits.totalExits <= 0 || exits.bySource.length === 0) return false;
  return exits.bySource.some((row) => row.source !== "unknown");
}

/** `unknown` 恒排最后一段（其余保持后端给的 count 降序，刷新时条目不跳）。 */
export function orderedExitRows(exits: AiTradeExitMix): AiTradeExitSourceRow[] {
  const known = exits.bySource.filter((r) => r.source !== "unknown");
  const unknown = exits.bySource.filter((r) => r.source === "unknown");
  return [...known, ...unknown];
}

/** 胜率文案；样本不足 ⇒ 明说「样本不足」而不是渲染一个 0%。 */
export function formatExitWinRate(row: AiTradeExitSourceRow): string {
  if (row.winRate == null) return "样本不足";
  return `${(row.winRate * 100).toFixed(1)}%`;
}

/** 平均持仓时长；无时长样本 ⇒ `—`（该来源的笔数仍照常显示）。 */
export function formatExitHold(row: AiTradeExitSourceRow): string {
  return row.avgHoldSeconds == null ? "—" : formatSeconds(row.avgHoldSeconds);
}

/** 秒 → 人话时长（与后端 `acceptance_text.format_hold_duration` 同一套规则）。 */
export function formatSeconds(seconds: number): string {
  const total = Math.max(0, Math.floor(seconds));
  if (total < 60) return `${total}s`;
  if (total < 3600) {
    const rest = total % 60;
    return rest ? `${Math.floor(total / 60)}m ${rest}s` : `${Math.floor(total / 60)}m`;
  }
  if (total < 86400) {
    const mins = Math.floor((total % 3600) / 60);
    return mins ? `${Math.floor(total / 3600)}h ${mins}m` : `${Math.floor(total / 3600)}h`;
  }
  const hours = Math.floor((total % 86400) / 3600);
  return hours ? `${Math.floor(total / 86400)}d ${hours}h` : `${Math.floor(total / 86400)}d`;
}

/**
 * 验收卡是否值得渲染。
 *
 * 未装配周期契约 / 无验收数据 ⇒ 整卡不渲染——绝不在一个没有周期契约的会话上挂一张空卡
 * （与既有 `HorizonLiveCard` 的纪律逐字一致）。
 */
export function hasAcceptance(card: AiTradeAcceptance | null | undefined): boolean {
  return !!card && Array.isArray(card.rows) && card.rows.length > 0;
}

/** 行徽标颜色；**`insufficient` 永远不返回绿色**（A-2 用例按此断言）。 */
export function verdictColor(verdict: AiTradeAcceptanceVerdict): string {
  return ACCEPTANCE_VERDICT_META[verdict]?.color ?? "var(--muted)";
}

/** 资金费口径的人话说明（`null` 与 `0` 必须说成两件事，放行条件 5）。 */
export function describeFundingBasis(
  fundingCostUsd: number | null | undefined,
  basis: string | null | undefined,
): string {
  if (fundingCostUsd == null) return "口径不可得";
  if (fundingCostUsd === 0) return "未跨结算点";
  return basis === "settled+accrued" ? "交易所结算口径" : "本地计提口径";
}
