// 证据接地的前端纯函数（ai-trade-intel-evidence-grounding §6.2 #28）。
// **零 React、零请求**——它们全部可单测，且被抽屉、卡片、设置卡三处共用。
import type {
  IntelDocState,
  IntelDocumentHealth,
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

/** 抓取健康的三态（silent-degradation-repair 轴 B-2）。 */
export type ReaderHealthTone = "neutral" | "danger" | "ok";

export interface ReaderHealthView {
  tone: ReaderHealthTone;
  /** 一句话读数（「正文抓取 0/96 全部失败」）。 */
  text: string;
  /** 危险态才有的一句成因提示；其余为空串。 */
  hint: string;
}

/**
 * 「正文到底抓下来没有」的三态（轴 B-2）。
 *
 * 这两列（`reader_attempt` / `reader_ok`）**有逐轮读点**——轮次页那一行「正文 5/6」——
 * 但**从不聚合成结论、从不跨轮判趋势、从不告警**。一个人要发现「16 轮全零」，得自己翻 16 行
 * 然后自己做加法。本函数做的正是那三个「从不」里的第一个。
 *
 * 老回执（两个键都缺席）⇒ `null` ⇒ 调用方整行不渲染，**绝不**把 `undefined` 当成 0 去渲染
 * 一个假的「0/0 全部失败」。
 */
export function describeReaderHealth(
  health: Pick<IntelDocumentHealth, "fetchAttempt" | "fetchOk"> | null | undefined,
): ReaderHealthView | null {
  if (!health || health.fetchAttempt == null || health.fetchOk == null) return null;
  const attempt = Math.max(0, health.fetchAttempt);
  const ok = Math.max(0, health.fetchOk);
  if (attempt === 0) {
    return { tone: "neutral", text: "本轮未尝试抓取正文", hint: "" };
  }
  if (ok === 0) {
    return {
      tone: "danger",
      text: `正文抓取 0/${attempt} 全部失败`,
      hint: "常见成因：本机 DNS 把公网域名解析成了合成地址（198.18.0.0/15，"
        + "透明代理的 fake-IP 模式），可在上方切换解析策略后重试。",
    };
  }
  return { tone: "ok", text: `正文抓取 ${ok}/${attempt}`, hint: "" };
}

/**
 * 「缓存了 N 篇文档、正文合计 0 字节」——一个在健康安装上**定义上不可能**的组合（§一 E-14）。
 *
 * 这两个数三个星期来一直显示在设置页上，只是没有任何一处把它翻译成一句人能读懂的结论。
 * 不成立 ⇒ `null` ⇒ 不追加任何尾注。
 */
export function describeEmptyBodyCache(
  health: Pick<IntelDocumentHealth, "cachedDocs" | "bodyBytes"> | null | undefined,
): string | null {
  if (!health || health.cachedDocs <= 0 || health.bodyBytes !== 0) return null;
  return "—— 这些文档一篇正文都没抓下来";
}

/** 缓存体积的人类可读形态（后端下发的是**真实字节**，这里只做单位换算）。 */
export function bytesLabel(bytes: number): string {
  if (bytes <= 0) return "0 MB";
  const mb = bytes / (1024 * 1024);
  return mb < 0.1 ? "<0.1 MB" : `${mb.toFixed(1)} MB`;
}
