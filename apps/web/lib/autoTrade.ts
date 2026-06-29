// 自选自动交易分组纯件（watchlist-auto-trade-group §2.2 / §2.3）：百分比规整 / 守门 / 预算预览 /
// 可自动交易标的判定。全部无副作用纯函数，供 store action 与 AutoTradePanel / 弹窗复用并单测。
import { inferInstType } from "./instruments";
import type { AutoTradeMember } from "./liveAutoTypes";

// 与后端 settings.live_auto_*_capital_frac / live_default_leverage / live_auto_max_group_symbols 对齐。
export const LIVE_AUTO_DEFAULT_CAPITAL_FRAC = 0.01;
export const LIVE_AUTO_MAX_CAPITAL_FRAC = 0.05;
export const LIVE_AUTO_MAX_GROUP_SYMBOLS = 8;

/** 该标的是否可自动交易：仅 USDT 本位永续 / 交割合约（SPOT / 非 USDT 一律拒绝，§2.2 不变量 2）。 */
export function isAutoTradable(instId: string): boolean {
  const type = inferInstType(instId);
  if (type !== "SWAP" && type !== "FUTURES") return false;
  // SWAP: BASE-USDT-SWAP；FUTURES: BASE-USDT-<到期日>。计价段恒为第二段。
  const quote = instId.split("-")[1];
  return quote === "USDT";
}

/** 成员百分比之和（保留浮点；守门按 ≤ 100 + 容差判定）。 */
export function totalAllocation(members: AutoTradeMember[]): number {
  return members.reduce((sum, m) => sum + (Number.isFinite(m.allocationPct) ? m.allocationPct : 0), 0);
}

/** 新增成员的默认百分比：把剩余额度给它（保留既有成员手改值），下限 0。 */
export function defaultAllocationOnAdd(members: AutoTradeMember[]): number {
  return Math.max(0, Math.round((100 - totalAllocation(members)) * 100) / 100);
}

/** 手改某成员百分比时夹取：[1, 100 − Σ其余]（其余之和留出额度），并整规到 2 位小数。 */
export function clampAllocationOnEdit(
  members: AutoTradeMember[],
  instId: string,
  next: number,
): number {
  if (!Number.isFinite(next)) return 1;
  const others = members.filter((m) => m.instId !== instId).reduce((s, m) => s + m.allocationPct, 0);
  const upper = Math.max(1, Math.round((100 - others) * 100) / 100);
  const clamped = Math.min(Math.max(next, 1), upper);
  return Math.round(clamped * 100) / 100;
}

/** 平均分配：各 floor(100/n)，首成员吸收余数使和恰为 100（n=0 原样返回）。 */
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
  if (totalAllocation(input.members) > 100 + 1e-9) return "各标的百分比之和超过 100%，请下调";
  return null;
}

/** 单标的会话预算预览：总预算 × 该标的百分比 / 100（最终以服务端回执为准）。 */
export function memberBudget(totalBudgetUsd: number, allocationPct: number): number {
  return (totalBudgetUsd * allocationPct) / 100;
}

/** 留空预算时的保守默认（前端预览口径，§2.3 P1-1）：min(avail×1%×N, avail×5%)。 */
export function deriveGroupDefaultBudget(availUsdt: number, memberCount: number): number {
  const avail = Math.max(0, availUsdt);
  return Math.min(avail * LIVE_AUTO_DEFAULT_CAPITAL_FRAC * memberCount, avail * LIVE_AUTO_MAX_CAPITAL_FRAC);
}

/** 预算硬上限夹紧（前端预览，最终以服务端回执 totalBudgetUsd 为准）。 */
export function clampGroupBudgetToCap(budget: number, availUsdt: number): number {
  const cap = Math.max(0, availUsdt) * LIVE_AUTO_MAX_CAPITAL_FRAC;
  return cap > 0 ? Math.min(budget, cap) : budget;
}
