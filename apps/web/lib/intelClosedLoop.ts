// 情报闭环的纯文案函数（ai-trade-intel-closed-loop §3.7.5）。
// **零 React、零请求**——它们全部可单测，且被卡片、徽章、两个新页签四处共用。
//
// 文案集中在这里是一条既有规矩（`lib/intel.ts` 已立）：组件里散写字符串会让「关联净额」这种
// **有意为之**的措辞在下一次改版里被顺手改成「贡献」，而那正是 R-1 唯一的缓解手段。
import type { IntelItem, IntelVerdict } from "./intelTypes";

/** 判定徽章的四态文案。`""` ⇒ 空串 ⇒ **整个徽章不渲染**（不是渲染一个「未知」）。 */
export function verdictLabel(verdict: IntelVerdict): string {
  if (verdict === "confirmed") return "已证实";
  if (verdict === "refuted") return "已证伪";
  if (verdict === "unresolved") return "待定";
  return "";
}

/** 判定徽章的配色变量（与全站语义色同源）。 */
export function verdictTone(verdict: IntelVerdict): string {
  if (verdict === "confirmed") return "var(--ok)";
  if (verdict === "refuted") return "var(--danger)";
  if (verdict === "unresolved") return "var(--muted)";
  return "var(--muted)";
}

/**
 * 净额的配色语义。
 *
 * **零值用 `--muted` 而不是 `--up`**：一个「打平」被画成绿色，会让一排零值看起来像一排小胜。
 */
export function netPnlTone(netPnlUsd: number): string {
  if (netPnlUsd > 0) return "var(--up)";
  if (netPnlUsd < 0) return "var(--down)";
  return "var(--muted)";
}

/** 归因轨的一行文案；`citeCount === 0` ⇒ 空串 ⇒ **整行不渲染**（不出现「引用 0 次」的噪音）。 */
export function attributionText(item: IntelItem): string {
  if (!item.citeCount) return "";
  const parts = [`引用 ${item.citeCount}`];
  if (item.citeEntryCount > 0) parts.push(`入场 ${item.citeEntryCount}`);
  if (item.settledCount > 0) {
    parts.push(`已结算 ${item.settledCount}`, `胜 ${item.winCount}`);
    parts.push(`关联净额 ${formatNetPnl(item.netPnlUsd)}`);
  }
  return parts.join(" · ");
}

/** 归因轨的完整口径（挂在 `title` 上）。数字之外必须有一句说明它**不是**因果。 */
export function attributionTitle(item: IntelItem): string {
  const settled = `入场引用 ${item.citeEntryCount} 次中已结算 ${item.settledCount} 次；`;
  const record = `胜 ${item.winCount} 负 ${Math.max(0, item.settledCount - item.winCount)}；`;
  return (
    `${settled}${record}累计关联净额按往返结算口径。` +
    "这是**共现**统计，不是因果归因：一笔交易通常同时采信了多条证据。"
  );
}

/** 带正负号的净额（两位小数 + USDT）。 */
export function formatNetPnl(netPnlUsd: number): string {
  const sign = netPnlUsd > 0 ? "+" : "";
  return `${sign}${netPnlUsd.toFixed(2)} USDT`;
}

/** 信源状态的中文标签（空态刻意回空串：没表态的域名不该显示一个「正常」徽章）。 */
export function sourceStateLabel(state: "" | "blocked" | "trusted"): string {
  if (state === "blocked") return "已拉黑";
  if (state === "trusted") return "更信任";
  return "";
}

/** 被证伪条目在卡片上的警示行文案（与「我不同意」按钮成对出现，缺一不可）。 */
export const REFUTED_WARNING = "⚠ 已被后续报道推翻，已停止喂给交易 Agent";

/** 影响力页签顶部常驻的口径说明。**实现时不要为了排版把它折叠起来**（评审留意事项 1）。 */
export const IMPACT_CAVEAT =
  "这些数字是共现统计，不是因果归因：一笔交易通常同时采信了多条证据。";

/** 影响力页签的四种空态。四段文案**互不相同**——一个假装「没有数据」的面板会被读成「情报没用」。 */
export type ImpactEmptyKind = "cite-off" | "no-citations" | "attribution-off" | "not-armed";

export function impactEmptyText(kind: ImpactEmptyKind): string {
  if (kind === "cite-off") {
    return "情报引用已在设置中关闭：模型看不到编号，也就无法点名采信任何一条情报。";
  }
  if (kind === "attribution-off") {
    return "引用归因已在设置中关闭：引用照常记录，但不做往返结算，故这里没有净额可显示。";
  }
  if (kind === "not-armed") {
    return "本标的的情报层尚未装配（未配置检索源）：先在设置中心选一个检索源并填入 API Key。";
  }
  return "模型尚未在任何一轮决策里点名引用过情报。它们仍在情报库中并可能被注入，只是还没有被引用过。";
}

/** 核查轮在审计页签上的一行摘要。 */
export function verifyRoundSummary(actions: Record<string, number>): string {
  const total =
    (actions.confirmed ?? 0) + (actions.refuted ?? 0) + (actions.unresolved ?? 0);
  if (!total) return "本轮没有待核查条目";
  return (
    `核查 ${total} 条 → 证实 ${actions.confirmed ?? 0} · ` +
    `证伪 ${actions.refuted ?? 0} · 待定 ${actions.unresolved ?? 0}`
  );
}

/**
 * 「N 条判定因未指出依据被降为待定」的灰字行。
 *
 * **这正是本特性最值得让用户看见的那一行**：它是「模型不能空口断言」这条承诺的可见证据。
 */
export function verdictNoRefNote(actions: Record<string, number>): string {
  const n = actions.verdict_no_ref ?? 0;
  return n > 0 ? `${n} 条判定因未指出依据被降为待定` : "";
}
