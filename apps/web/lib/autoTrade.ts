// 自选自动交易分组纯件（watchlist-auto-trade-group §2.2 / §2.3 + auto-trade-budget-and-allocation-settings
// §2.2 / §2.3）：百分比规整 / 守门 / 预算预览 / 可自动交易标的判定。全部无副作用纯函数，供 store action
// 与 AutoTradePanel / 弹窗复用并单测。预算上限不再写死，由 maxBudgetPct（设置中心配置）作为函数入参传入。
import { inferInstType } from "./instruments";
import type { AutoTradeAllocationMode, AutoTradeMember } from "./liveAutoTypes";

export const LIVE_AUTO_MAX_GROUP_SYMBOLS = 8;
// max_ratio（最大比例）模式下新增成员的保守默认上限百分比（与后端 settings.live_auto_default_max_ratio_pct 对齐）。
export const LIVE_AUTO_DEFAULT_MAX_RATIO_PCT = 20;
// 自动交易设置缺省总预算上限百分比（未读到设置时的前端兜底，与后端 repo 默认一致，§5.1）。
export const DEFAULT_AUTO_TRADE_MAX_BUDGET_PCT = 100;

/** 该标的是否可自动交易：仅 USDT 本位永续 / 交割合约（SPOT / 非 USDT 一律拒绝，§2.2 不变量 2）。 */
export function isAutoTradable(instId: string): boolean {
  const type = inferInstType(instId);
  if (type !== "SWAP" && type !== "FUTURES") return false;
  // SWAP: BASE-USDT-SWAP；FUTURES: BASE-USDT-<到期日>。计价段恒为第二段。
  const quote = instId.split("-")[1];
  return quote === "USDT";
}

/** 成员百分比之和（保留浮点；even 守门按 ≤ 100 + 容差判定，max_ratio 仅作展示）。 */
export function totalAllocation(members: AutoTradeMember[]): number {
  return members.reduce((sum, m) => sum + (Number.isFinite(m.allocationPct) ? m.allocationPct : 0), 0);
}

/** 新增成员的默认百分比：even → 剩余额度（100 − Σ，下限 0）；max_ratio → 固定保守默认上限。 */
export function defaultAllocationOnAdd(
  members: AutoTradeMember[],
  mode: AutoTradeAllocationMode = "even",
): number {
  if (mode === "max_ratio") return LIVE_AUTO_DEFAULT_MAX_RATIO_PCT;
  return Math.max(0, Math.round((100 - totalAllocation(members)) * 100) / 100);
}

/** 手改某成员百分比时夹取：even → [1, 100 − Σ其余]；max_ratio → [1, 100]（各自独立）。整规到 2 位小数。 */
export function clampAllocationOnEdit(
  members: AutoTradeMember[],
  instId: string,
  next: number,
  mode: AutoTradeAllocationMode = "even",
): number {
  if (!Number.isFinite(next)) return 1;
  if (mode === "max_ratio") {
    return Math.round(Math.min(Math.max(next, 1), 100) * 100) / 100;
  }
  const others = members.filter((m) => m.instId !== instId).reduce((s, m) => s + m.allocationPct, 0);
  const upper = Math.max(1, Math.round((100 - others) * 100) / 100);
  const clamped = Math.min(Math.max(next, 1), upper);
  return Math.round(clamped * 100) / 100;
}

/** 平均分配（仅 even 模式）：各 floor(100/n)，首成员吸收余数使和恰为 100（n=0 原样返回）。 */
export function evenSplit(members: AutoTradeMember[]): AutoTradeMember[] {
  const n = members.length;
  if (n === 0) return members;
  const base = Math.floor(100 / n);
  const remainder = 100 - base * n;
  return members.map((m, i) => ({ ...m, allocationPct: base + (i === 0 ? remainder : 0) }));
}

export interface GroupGateInput {
  accountMode: "sim" | "live";
  hasReadyDefault: boolean;
  liveAutoEnabled: boolean;
  members: AutoTradeMember[];
  running: boolean;
  mode: AutoTradeAllocationMode;
}

/** 分组开关守门：返回禁用原因（中文），全部满足返回 null（可启动）。运行中由调用方改判为「停止」。 */
export function resolveGroupGate(input: GroupGateInput): string | null {
  if (input.running) return null;
  if (input.accountMode !== "live") return "请先切换到实盘账户模式";
  if (!input.hasReadyDefault) return "请先在设置中配置默认实盘账户";
  if (!input.liveAutoEnabled) return "实时自动交易未启用（需后端开启 LIVE_AUTO_TRADING_ENABLED）";
  if (input.members.length === 0) return "请先把自选标的拖入自动交易区";
  if (input.members.length > LIVE_AUTO_MAX_GROUP_SYMBOLS) {
    return `自动交易最多 ${LIVE_AUTO_MAX_GROUP_SYMBOLS} 个标的，请移出部分`;
  }
  if (input.members.some((m) => !isAutoTradable(m.instId))) {
    return "含不支持自动交易的标的（仅 USDT 本位永续 / 交割合约）";
  }
  if (input.members.some((m) => !(m.allocationPct > 0))) return "存在百分比为 0 的标的，请先分配仓位";
  // even 模式：各标的为总预算的份额切分，强制 Σ ≤ 100；max_ratio 模式：各标的独立上限，跳过该闸。
  if (input.mode === "even" && totalAllocation(input.members) > 100 + 1e-9) {
    return "各标的百分比之和超过 100%，请下调";
  }
  return null;
}

/** 单标的会话预算预览：总预算 × 该标的百分比 / 100（最终以服务端回执为准；两模式同式）。 */
export function memberBudget(totalBudgetUsd: number, allocationPct: number): number {
  return (totalBudgetUsd * allocationPct) / 100;
}

/** 留空预算时的默认总预算（前端预览口径，§2.2）：可用余额 × maxBudgetPct%，**不再随成员数缩放**。 */
export function deriveGroupDefaultBudget(availUsdt: number, maxBudgetPct: number): number {
  return (Math.max(0, availUsdt) * maxBudgetPct) / 100;
}

/** 预算硬上限夹紧（前端预览，最终以服务端回执 totalBudgetUsd 为准）。 */
export function clampGroupBudgetToCap(budget: number, availUsdt: number, maxBudgetPct: number): number {
  const cap = groupBudgetCap(availUsdt, maxBudgetPct);
  return cap > 0 ? Math.min(budget, cap) : budget;
}

/** 分组预算硬上限（可用余额 × maxBudgetPct%）；avail 为 null / ≤0 时返回 0，表示无有效上限。 */
export function groupBudgetCap(availUsdt: number | null, maxBudgetPct: number): number {
  if (availUsdt == null) return 0;
  return (Math.max(0, availUsdt) * maxBudgetPct) / 100;
}

/**
 * 用户所填总预算是否会被硬上限夹紧（驱动确认弹窗的就地夹紧提示）。
 * 仅当存在有效上限（cap > 0，即余额已读取且 > 0）且填值严格超过上限时为真；
 * 留空、余额读取中（avail == null）、余额为 0 时一律返回 false，与 clampGroupBudgetToCap 的实际行为保持一致
 * （cap ≤ 0 时该函数原样返回不夹紧，故此处不得提示「已夹紧」）。
 */
export function isGroupBudgetClamped(
  capitalUsd: number | null,
  availUsdt: number | null,
  maxBudgetPct: number,
): boolean {
  const cap = groupBudgetCap(availUsdt, maxBudgetPct);
  return cap > 0 && capitalUsd != null && capitalUsd > cap;
}
