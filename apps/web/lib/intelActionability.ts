// 情报可行动化的纯文案 / 纯排序函数（ai-trade-intel-actionability §3.4）。
// **零 React、零请求**——它们全部可单测，且被动态页签、告警行、战绩块、矛盾徽标四处共用。
//
// 文案集中在这里是一条既有规矩（`lib/intel.ts` 与 `lib/intelClosedLoop.ts` 已立）：组件里散写
// 字符串会让「关联净额」「样本不足」这种**有意为之**的措辞在下一次改版里被顺手改掉，而它们
// 正是 R-1 / R-6 唯一的缓解手段。
import type {
  IntelAlert,
  IntelConflict,
  IntelEfficacy,
  IntelItem,
  IntelSourceRow,
  IntelStat,
} from "./intelTypes";

/** 一条告警行的三态。文案分开是刻意的——三者说的是完全不同的三件事。 */
export type AlertRowState =
  | "suppressed"
  | "waiting"
  | "expired"
  | "settled";

/**
 * 一条告警现在处于哪一态。
 *
 * `expired` 是**渲染期判定，不回写库**：一个只为了让界面好看而产生的写操作，会与真正的回写
 * 竞态（后端 §3.1.2 的成文裁决）。判据是「已告警、库里仍无结果、且超过了待处理时限」。
 */
export function alertRowState(
  alert: IntelAlert,
  nowMs: number,
  pendingTtlS: number,
): AlertRowState {
  if (!alert.fired) return "suppressed";
  if (alert.outcome) return "settled";
  const ageS = Math.max(0, nowMs / 1000 - alert.createdTs);
  return ageS > Math.max(1, pendingTtlS) ? "expired" : "waiting";
}

/** 告警行的一句话。**抑制码与结果码的中文都由后端下发**，前端只挑说哪一句。 */
export function alertRowText(alert: IntelAlert, state: AlertRowState): string {
  if (state === "suppressed") {
    return `未告警 · ${alert.suppressedText || alert.suppressedCode || "原因未知"}`;
  }
  if (state === "waiting") return "已告警 · 等待会话响应";
  if (state === "expired") return "已告警 · 会话未在时限内响应";
  return `已告警 · ${alert.outcomeText || alert.outcome}`;
}

/**
 * 告警行的色调（只有 `woke` 用强调色：满屏强调色等于没有强调色）。
 *
 * ⚠️ 取 `--accent-ink` 而不是 `--accent`：本仓库有一条成文的对比度纪律「**面色不得用作文字
 * 色**」，并为它立了一条源码扫描门禁（`components/ui/contrastTokens.source.test.ts`，扫描根
 * 含 `lib/`）。这里返回的值直接进 `style={{ color }}`。
 */
export function alertRowTone(alert: IntelAlert, state: AlertRowState): string {
  if (state === "suppressed") return "var(--muted)";
  if (state === "settled" && alert.outcome === "woke") return "var(--accent-ink)";
  if (state === "expired") return "var(--warn)";
  return "var(--text)";
}

/** 一组折叠后的告警行：连续同码的「未告警」压成一行 + 计数。 */
export interface AlertGroup {
  key: string;
  head: IntelAlert;
  count: number;
}

/**
 * 把连续同抑制码的未告警行折叠成一行。
 *
 * 不折叠的后果不是排版难看：一个配置偏严的用户会看到几十行「未告警 · 影响力未达门槛」，
 * 把真正的事件（一次 `woke`、一次 `dropped_feed_stale`）整个淹掉。**已告警的行永不折叠**
 * ——每一次真的打断了决策的告警都值得单独占一行。
 */
export function collapseAlerts(alerts: IntelAlert[]): AlertGroup[] {
  const out: AlertGroup[] = [];
  for (const alert of alerts) {
    const last = out[out.length - 1];
    const foldable = !alert.fired && alert.suppressedCode;
    if (last && foldable && !last.head.fired && last.head.suppressedCode === alert.suppressedCode) {
      last.count += 1;
      continue;
    }
    out.push({ key: `alert-${alert.id}`, head: alert, count: 1 });
  }
  return out;
}

/**
 * 准确率的展示文案；`null` ⇒ 「样本不足」而不是「0%」。
 *
 * 「没数据」与「全错」是两件事，写成同一个值就是把一次没跑过的统计伪装成一次很差的成绩——
 * 而这一列正是用户决定拉黑谁的依据。
 */
export function accuracyLabel(accuracy: number | null | undefined): string {
  if (accuracy === null || accuracy === undefined) return "样本不足";
  return `${(accuracy * 100).toFixed(0)}%`;
}

/** 战绩桶的一行摘要；零样本 ⇒ 空串 ⇒ **整行不渲染**（不出现「0 胜 0 负」的噪音）。 */
export function statText(stat: IntelStat | null | undefined): string {
  if (!stat || stat.samples === 0) return "";
  const parts: string[] = [];
  if (stat.settled > 0) {
    parts.push(`已结算 ${stat.settled}`, `胜 ${stat.wins}`);
  }
  if (stat.confirmed + stat.refuted + stat.unresolved > 0) {
    parts.push(`证实 ${stat.confirmed}`, `证伪 ${stat.refuted}`, `待定 ${stat.unresolved}`);
  }
  return parts.join(" · ");
}

/**
 * 先验是否已经生效（样本是否够 `kMin`）。
 *
 * 界面上必须**如实写成「样本 3/8，先验未生效」**而不是画一个假的权重条：本轮的先验是**每标的
 * 一份**（§十 非目标 1 的红线），新用户很久看不到它生效，靠池化把这件事藏起来是不可接受的。
 */
export function efficacyReadyText(efficacy: IntelEfficacy | null | undefined): string {
  if (!efficacy) return "";
  if (!efficacy.enabled) return "战绩统计已在设置中关闭";
  if (efficacy.sampleN >= efficacy.kMin) {
    return `样本 ${efficacy.sampleN}，先验已生效（有界加分，只影响排序与举证责任）`;
  }
  return `样本 ${efficacy.sampleN}/${efficacy.kMin}，先验未生效（样本不足时权重恒为 0）`;
}

/** 类别权重的展示：正为「更靠前」、负为「更靠后」、0 为「无影响」。 */
export function weightLabel(weight: number): string {
  if (weight > 0.0005) return `+${weight.toFixed(3)}（排序更靠前）`;
  if (weight < -0.0005) return `${weight.toFixed(3)}（排序更靠后）`;
  return "0（无影响）";
}

/** 按准确率降序排信源行；`null`（样本不足）一律排在**最后**，不与真实读数混排。 */
export function sortSourcesByAccuracy(rows: IntelSourceRow[]): IntelSourceRow[] {
  return [...rows].sort((a, b) => {
    const left = a.accuracy;
    const right = b.accuracy;
    if (left === null && right === null) return b.itemCount - a.itemCount;
    if (left === null) return 1;
    if (right === null) return -1;
    return right - left || b.itemCount - a.itemCount;
  });
}

/** `itemId → 对方 itemId`（卡片徽标据它显示「与哪一条矛盾」）。 */
export function conflictPartners(conflicts: IntelConflict[]): Record<number, number> {
  const out: Record<number, number> = {};
  for (const pair of conflicts) {
    if (out[pair.aId] === undefined) out[pair.aId] = pair.bId;
    if (out[pair.bId] === undefined) out[pair.bId] = pair.aId;
  }
  return out;
}

/** 处于矛盾中的条目 id 集合（「仅看冲突（N）」筛选的判据）。 */
export function conflictIds(conflicts: IntelConflict[]): Set<number> {
  return new Set(conflicts.flatMap((pair) => [pair.aId, pair.bId]));
}

/** 矛盾徽标的悬浮说明：对方的标题 + 已悬置轮数（拿不到对方 ⇒ 只说前半句）。 */
export function conflictTitle(
  pair: IntelConflict | undefined,
  partner: IntelItem | undefined,
): string {
  if (!pair) return "";
  const held = pair.openRounds > 0 ? `，已悬置 ${pair.openRounds} 轮整理` : "";
  const who = partner ? `：「${partner.headline}」` : "";
  // ⚠️ 这段文案会原样进 `title=` 属性：**绝不能带 Markdown 的星号**（JSX 不渲染它们，
  // 用户看到的就是两个字面星号——本仓库已经复发过三次，并为此立了一条源码扫描门禁）。
  return (
    `与库中另一条同题反向情报冲突${who}${held}。` +
    "系统不会自动删除任何一方——两条相反的报道里哪一条为真，确定性规则判断不了；" +
    "它只负责让分歧对模型和你都可见。"
  );
}
