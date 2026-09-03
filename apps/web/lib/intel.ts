// 情报层的纯函数（ai-trade-market-intel §6.2 #37）：标签、相对时间、排序、未读判定、降级兜底文案。
// **零 React、零请求**——它们全部可单测，且被弹窗、按钮、设置卡三处共用。
// 分组级回执的类型住在 `aiTradeTypes`（它是 AI 自动交易的启动契约的一部分），文案装配点在
// 这里——「情报的中文措辞只有一个出处」这条纪律优先于「按文件分家」。**仅类型导入，无环**。
import type { AiTradeIntelReceipt } from "./aiTradeTypes";
import type {
  AiTradeIntelBinding,
  AiTradeIntelSummary,
  IntelBindingMode,
  IntelBindingReason,
  IntelCategory,
  IntelDegrade,
  IntelImpact,
  IntelItem,
  IntelStance,
  IntelTier,
} from "./intelTypes";

export const TIER_ORDER: IntelTier[] = ["ultra", "mid", "long"];

export const TIER_LABEL: Record<IntelTier, string> = {
  ultra: "超短",
  mid: "中期",
  long: "长期",
};

export const CATEGORY_LABEL: Record<IntelCategory, string> = {
  market: "市场",
  sentiment: "情绪",
  macro: "宏观",
  onchain: "链上",
  project: "项目",
  risk: "风险",
};

export const STANCE_LABEL: Record<IntelStance, string> = {
  bullish: "偏多",
  bearish: "偏空",
  neutral: "中性",
};

export const IMPACT_LABEL: Record<IntelImpact, string> = {
  low: "低影响",
  medium: "中影响",
  high: "高影响",
};

/** 方向色点：与全站的多空色一致（`--long` / `--short` / `--muted`）。 */
export function stanceColor(stance: IntelStance): string {
  if (stance === "bullish") return "var(--long)";
  if (stance === "bearish") return "var(--short)";
  return "var(--muted)";
}

/** 影响力权重（仅用于**前端排序**；服务端另有自己的一份，两者互不依赖）。 */
const IMPACT_W: Record<IntelImpact, number> = { low: 0.34, medium: 0.67, high: 1 };
const HALF_LIFE_DAYS: Record<IntelTier, number> = { ultra: 1, mid: 14, long: 120 };

/**
 * 排序打分：置信度 × 影响力 × 时间衰减。
 *
 * 未来事件不衰减——一件还没发生的事不会因为「我们早就知道了」而变得不重要。
 */
export function itemScore(item: IntelItem, nowMs: number): number {
  const ref = item.eventTs > 0 ? item.eventTs : item.capturedTs;
  const ageDays = Math.max(0, (nowMs / 1000 - ref) / 86400);
  const decay = Math.exp(-ageDays / (HALF_LIFE_DAYS[item.tier] ?? 1));
  return item.confidence * (IMPACT_W[item.impact] ?? 0.5) * decay;
}

/** 层内排序：置顶恒在最前，其余按 score 降序（与后端注入端同序，便于对照）。 */
export function sortItems(items: IntelItem[], nowMs: number): IntelItem[] {
  return [...items].sort((a, b) => {
    if (a.pinned !== b.pinned) return a.pinned ? -1 : 1;
    return itemScore(b, nowMs) - itemScore(a, nowMs);
  });
}

/**
 * 相对时间的中文口径（未来事件说「N 天后」，无时间说「时间未知」）。
 *
 * 绝对时间由调用方挂在 `title` 上——相对时间好读，但用户在核对一条情报时需要的是准确时刻。
 */
export function relativeTime(ts: number, nowMs: number): string {
  if (!ts) return "时间未知";
  const delta = Math.round(nowMs / 1000) - ts;
  const ahead = delta < 0;
  const span = Math.abs(delta);
  let text: string;
  if (span < 3600) text = `${Math.max(1, Math.floor(span / 60))} 分钟`;
  else if (span < 86400) text = `${Math.floor(span / 3600)} 小时`;
  else text = `${Math.floor(span / 86400)} 天`;
  return ahead ? `${text}后` : `${text}前`;
}

/** 绝对时间（本地时区 + UTC 双写），供 `title` 属性——跨时区核对新闻时这一行是刚需。 */
export function absoluteTime(ts: number): string {
  if (!ts) return "时间未知";
  const d = new Date(ts * 1000);
  const pad = (n: number) => String(n).padStart(2, "0");
  const local = `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
  const utc = `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())} ${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}`;
  return `本地 ${local} ／ UTC ${utc}`;
}

/** 置信度档位（数字之外再给一个词，使非量化用户也能读懂）。 */
export function confidenceBand(confidence: number): string {
  if (confidence >= 0.7) return "较高";
  if (confidence >= 0.45) return "中等";
  return "偏低";
}

/** 未读徽标文案：>9 显示 `9+`（徽标只有一个字符的宽度）。 */
export function unreadBadge(unread: number): string {
  if (unread <= 0) return "";
  return unread > 9 ? "9+" : String(unread);
}

/**
 * 按钮的无障碍标签。**未读数必须进 aria-label**：一个只有视觉红点的徽标对读屏用户等于不存在。
 */
export function intelButtonLabel(instId: string, summary: AiTradeIntelSummary | null): string {
  const unread = summary?.unread ?? 0;
  const base = `${instId} 的市场情报`;
  return unread > 0 ? `${base}，${unread} 条未读` : base;
}

/**
 * 按钮的 `title`：常态一句说明，**依次**追加绑定态与降级文案。
 *
 * 用户不必打开弹窗就该知道两件事：这些情报此刻有没有真的在喂给 Agent（绑定态），以及情报
 * 流水线是不是出问题了（降级）——一个假装没有新闻的面板会被读成「市场很平静」。
 */
export function intelButtonTitle(summary: AiTradeIntelSummary | null): string {
  const base = "查看该标的的市场情报（超短/中期/长期）";
  const binding = intelBindingText(summary);
  const degrade = summary?.degrade;
  const parts = [base];
  if (binding) parts.push(binding);
  if (degrade) parts.push(degradeText(degrade));
  return parts.join("｜");
}

/** 采信档的中文名（三处共用：按钮 title、弹窗顶部条、启动弹窗）。 */
export const BINDING_MODE_LABEL: Record<IntelBindingMode, string> = {
  off: "未采信",
  index: "索引档",
  full: "全文档",
};

/**
 * 绑定原因码 → 一句中文（**只翻译码，不重新判定**）。
 *
 * 判定全部在后端 `intel/binding.py::_not_injecting_reason` 里做完；前端再判一次就是第二个
 * 真源，而两处判定漂移了不会有任何东西报错。
 */
export const BINDING_REASON_LABEL: Record<IntelBindingReason, string> = {
  "": "",
  not_bound: "该标的没有在跑的 AI 自动交易",
  session_off: "本次 AI 自动交易未采信情报（启动时未开启）",
  not_armed: "情报流水线未装配，暂时没有可注入的内容",
  ops_inject_off: "运维已关闭情报注入，情报只采集不喂给 Agent",
  no_items: "已采信，但库里还没有可注入的条目",
};

/** 三态：`"injecting"`（正在喂）/ `"bound"`（绑了但没喂）/ `"unbound"`（没绑）。 */
export type IntelBindingState = "injecting" | "bound" | "unbound";

/**
 * 绑定三态。**靠形状而非仅靠颜色区分**（色觉无障碍）——调用方据它选实心点 / 空心点 / 无叠加。
 */
export function intelBindingState(summary: AiTradeIntelSummary | null): IntelBindingState {
  const binding = summary?.binding ?? null;
  if (!binding?.bound) return "unbound";
  return binding.injecting ? "injecting" : "bound";
}

/**
 * 绑定态的一句话（`title` / `aria-label` / 弹窗顶部条共用的**唯一**文案装配点）。
 *
 * 未绑定 ⇒ **空串** ⇒ 调用方整段不渲染：对一个没在自动交易的标的说「未采信情报」是噪音。
 */
export function intelBindingText(summary: AiTradeIntelSummary | null): string {
  const binding = summary?.binding ?? null;
  if (!binding?.bound) return "";
  if (binding.injecting) {
    return `本次 AI 自动交易正在采信这些情报 · ${BINDING_MODE_LABEL[binding.mode] ?? binding.mode}`;
  }
  return BINDING_REASON_LABEL[binding.reason] || "本次 AI 自动交易未采信这些情报";
}

/**
 * 弹窗顶部条第二行的「上一轮进入上下文」读数；从未渲染过 ⇒ **空串** ⇒ 整行不渲染。
 *
 * 措辞必须区分两个数（后端页脚也是分两句写的）：`same` 是「见过所以折叠」的，`hidden` 是
 * 「预算装不下」的——并成一个数，用户与模型都会读错。
 */
export function intelLastRoundText(binding: AiTradeIntelBinding | null): string {
  const last = binding?.lastRound ?? null;
  if (!last) return "";
  const extra: string[] = [];
  if (last.same > 0) extra.push(`另有 ${last.same} 条与上一轮相同、未重复列出`);
  if (last.hidden > 0) extra.push(`另有 ${last.hidden} 条因篇幅未展示`);
  const head = `上一轮进入上下文 ${last.shown} 条`;
  return extra.length > 0 ? `${head}（${extra.join("；")}）` : head;
}

/**
 * 「服务端替我决定了什么」的一句中文（`group_status().intel.source`）。
 *
 * `request` ⇒ **空串**：用户自己选的，没有任何需要披露的东西。其余三格都是服务端**改写了**
 * 用户的选择，而改写在这个系统里必须出声。
 */
export const INTEL_RECEIPT_SOURCE_LABEL: Record<AiTradeIntelReceipt["source"], string> = {
  request: "",
  legacy_fallback: "本次会话启动于该开关上线之前，是否采信由服务端设置决定",
  ops_off: "会话级情报开关已被运维关闭，是否采信由服务端设置决定",
  resolve_failed: "启动时未能解析你的选择，已按「不采信」处理",
};

/**
 * 分组卡上的「情报研判」一行（intel-session-binding 轴 A-4 的**唯一**读点）。
 *
 * 老后端不下发 `intel` ⇒ **空串** ⇒ 整段不渲染（绝不在没有读数时编一句「未采信」）。
 * `source !== "request"` 时把服务端的改写如实附在后面——`legacy_fallback` 那一格尤其重要：
 * 它对应的是「本会话启动于该开关上线之前」的冷续跑，而那条路径只有刷新页面后重发现才走得到。
 */
export function intelReceiptText(receipt: AiTradeIntelReceipt | null): string {
  if (!receipt) return "";
  const mode = BINDING_MODE_LABEL[receipt.mode] ?? receipt.mode;
  const note = INTEL_RECEIPT_SOURCE_LABEL[receipt.source] ?? "";
  return note ? `${mode} —— ${note}` : mode;
}

/**
 * 降级文案：**后端下发的 `message` 优先**，只有它缺席时才回落到前端兜底。
 *
 * 前端不二次编文案是一条纪律：两处各写一份必漂移，而这块面板的全部价值就在于「失败要出声」。
 */
export function degradeText(degrade: IntelDegrade | null): string {
  if (!degrade) return "";
  const base = degrade.message || `情报流水线降级（${degrade.code}）`;
  if (degrade.retryInS == null || degrade.retryInS <= 0) return base;
  return `${base}，${degrade.retryInS}s 后自动重试`;
}

/**
 * 降级是否值得把按钮染成警示色（弱提示的四种不上色，避免「常亮的警告」失去意义）。
 *
 * `verify_no_corpus` 是 `search_empty` 在核查轮上的同义码（intel-closed-loop 轴 C）：
 * 「这一轮没检索到可核对的新材料」是一个**正常且频繁**的结论，相关条目已被诚实地记成待定。
 * 漏登记它，一次例行的空核查轮会把标的卡片上的情报按钮一直染成警示色，直到下一轮把它冲掉
 * ——而一个常亮的警告等于没有警告。另外三个 `verify_*` 码与它们的采集轮同义码同为强提示。
 */
const WEAK_DEGRADE = new Set([
  "search_empty",
  "no_tool_call",
  "all_rejected",
  "verify_no_corpus",
  // ai-trade-intel-arm-state：两个**装配**码同样是弱提示。`not_running` 在自动交易没开的
  // 整段时间里恒成立，染色等于把警示色常亮；`arm_pending` 最多持续一轮。按同一把尺子，
  // 它们比既有的 `search_empty` 更没有资格染色。
  // 反过来 `arm_failed` / `provider_unset` / `key_missing` / `provider_unknown` 是**强提示**，
  // 保持染色——那四种是「用户不动手就永远不会有情报」。
  "not_running",
  "arm_pending",
]);

export function isSevereDegrade(degrade: IntelDegrade | null): boolean {
  return !!degrade && !WEAK_DEGRADE.has(degrade.code);
}

/** 降级 banner 上那个按钮该做什么（**单一真源**，设置卡与弹窗共用）。 */
export type IntelDegradeAction = "settings" | "rearm" | "retry" | "none";

/** 「去设置」能解决的降级码：它们全都是用户在设置中心动一下手就能修好的。 */
const SETTINGS_CODES = new Set([
  "no_provider",
  "disabled",
  "provider_unset",
  "key_missing",
  "provider_unknown",
]);

/**
 * 「重新装配」能解决的降级码。**只有 `arm_failed` 一个**。
 *
 * `not_running`（要用户去开自动交易）与 `arm_pending`（只要等一轮跑完）刻意不在表里：给一个
 * 点了什么也不发生的按钮，比不给按钮更坏。
 */
export const REARM_CODES = new Set(["arm_failed"]);

/** 给不出任何按钮的两个码：一个要用户去开自动交易，一个只要等一轮跑完。 */
const NO_ACTION_CODES = new Set(["not_running", "arm_pending"]);

/**
 * 降级码 → 该给哪一个按钮。
 *
 * 取代了此前那句 `code === "no_provider" || code === "disabled" ? 去设置 : 立即重试` 的二分：
 * 两个语义完全不同的码共用同一个按钮，正是本轮要修的那类「把不同的事压成同一句话」。
 *
 * `"retry"` 这一支**刻意保留**：`search_failed` / `circuit_open` / `budget_exhausted` 这些
 * **轮内**降级码在本轮之前就归它管，砍掉它等于顺手拿走一个正常工作的重试入口。
 */
export function degradeAction(degrade: IntelDegrade | null): IntelDegradeAction {
  if (!degrade) return "none";
  if (SETTINGS_CODES.has(degrade.code)) return "settings";
  if (REARM_CODES.has(degrade.code)) return "rearm";
  if (NO_ACTION_CODES.has(degrade.code)) return "none";
  return "retry";
}

/** 下一次采集的倒计时文案；无排期 ⇒ 空串 ⇒ 该行不渲染。 */
export function nextDueText(summary: AiTradeIntelSummary | null, nowMs: number): string {
  if (!summary?.nextDueTs) return "";
  const seconds = summary.nextDueTs - Math.round(nowMs / 1000);
  if (seconds <= 0) return "下一次采集：即将开始";
  if (seconds < 60) return `下一次采集：约 ${seconds} 秒后`;
  return `下一次采集：约 ${Math.round(seconds / 60)} 分钟后`;
}

/** 该条目在列表里是否算「新」（未读高亮的判据，与徽标同源）。 */
export function isUnread(item: IntelItem, summary: AiTradeIntelSummary | null): boolean {
  return !!summary && item.capturedTs > summary.lastSeenTs;
}

/** 显示层的外链协议白名单（纵深防御：后端入库时已拒过一次非 http(s)）。 */
export function isSafeUrl(url: string): boolean {
  return url.startsWith("https://") || url.startsWith("http://");
}
