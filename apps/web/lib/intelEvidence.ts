// 证据接地的前端纯函数（ai-trade-intel-evidence-grounding §6.2 #28）。
// **零 React、零请求**——它们全部可单测，且被抽屉、卡片、设置卡三处共用。
import type {
  IntelDocState,
  IntelEvidence,
  IntelItem,
  IntelQuote,
  IntelQuoteState,
} from "./intelTypes";

/**
 * 「没有引证」的**五种原因各一句话**。
 *
 * 原因由后端下发（`item.quoteState`），前端**绝不从 `quoteCount === 0` 反推**：四种情况在
 * 界面上是四句不同的话，而前端手里没有任何能区分它们的信息。
 */
export const QUOTE_STATE_LABEL: Record<Exclude<IntelQuoteState, "">, string> = {
  legacy: "本条早于原文取证功能，没有留存原文",
  modeOff: "原文引证要求已在设置中关闭",
  noBody: "这几个来源都没能抓到正文",
  modelOmitted: "抓到了正文，但情报 Agent 本轮没有附引文",
  verifyFailed: "附了引文，但没有一段能在原文里逐字对上",
};

/** 触发器 / 无引证标记上那一行短文案。有引证时回空串（调用方据此渲染触发器）。 */
export function quoteStateLabel(state: IntelQuoteState): string {
  if (!state) return "";
  return QUOTE_STATE_LABEL[state] ?? "没有可核对的原文引证";
}

/**
 * 卡片上到底渲染触发器还是「无引证」标记。
 *
 * **没有引文时绝不渲染一个空的触发器**：一个点开永远是空的控件比没有这个控件更糟。
 */
export function hasEvidence(item: Pick<IntelItem, "quoted" | "quoteCount">): boolean {
  return !!item.quoted && item.quoteCount > 0;
}

/** 触发器文案：「原文引证 N」。 */
export function evidenceTriggerLabel(count: number): string {
  return `原文引证 ${Math.max(0, count)}`;
}

/** 引文按 `role` 分两组渲染——混在一起会让用户读不出「这条被推翻，是因为后来出现了这段字」。 */
export function splitQuotes(quotes: IntelQuote[]): {
  admit: IntelQuote[];
  verify: IntelQuote[];
} {
  return {
    admit: quotes.filter((q) => q.role !== "verify"),
    verify: quotes.filter((q) => q.role === "verify"),
  };
}

/**
 * 这条引文的原文快照还在不在缓存里。
 *
 * 不在 ⇒ 抽屉底部必须出现「原文快照已过期，上方引文仍为当时逐字留存」那一句；否则用户会把
 * 「没有 doc 行」误读成「这条情报没有来源」。
 */
export function docStale(quote: IntelQuote, docs: IntelDocState[]): boolean {
  return !docs.some((doc) => doc.url === quote.docUrl || doc.finalUrl === quote.docUrl);
}

/** 抽屉里是否要渲染那句「原文快照已过期」。 */
export function hasStaleSnapshot(evidence: IntelEvidence | null): boolean {
  if (!evidence || !evidence.quotes.length) return false;
  return evidence.quotes.some((quote) => docStale(quote, evidence.docs));
}

/** 命中率文案；`null`（没提过引文）⇒ 空串 ⇒ 该行不渲染，绝不显示一个假的 0%。 */
export function quoteHitRateLabel(rate: number | null): string {
  if (rate == null) return "";
  return `近 20 轮引文命中率 ${Math.round(rate * 100)}%`;
}

/** 把 `quote_mode=strict` 的翻档判据（≥70%）摆在用户眼前——只写在文档里的判据等于没有判据。 */
export function strictReadiness(rate: number | null): string {
  if (rate == null) return "还没有可用的命中率读数，先让它跑几轮再考虑收紧。";
  return rate >= 0.7
    ? "命中率已达到收紧的建议线（≥70%）。"
    : "命中率低于 70% 时不建议开启「必须附可核对引文」。";
}

/** 缓存体积的人类可读形态（后端下发的是**真实字节**，这里只做单位换算）。 */
export function bytesLabel(bytes: number): string {
  if (bytes <= 0) return "0 MB";
  const mb = bytes / (1024 * 1024);
  return mb < 0.1 ? "<0.1 MB" : `${mb.toFixed(1)} MB`;
}
