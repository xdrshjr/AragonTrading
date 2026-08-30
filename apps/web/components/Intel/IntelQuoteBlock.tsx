"use client";

import { isSafeUrl, relativeTime } from "@/lib/intel";
import type { IntelQuote } from "@/lib/intelTypes";

/**
 * 单段原文引证（ai-trade-intel-evidence-grounding §3.8）。
 *
 * 四条设计纪律：
 * - **等宽字体 + 左侧强调竖线**：这是**别人写的字**，不是我们写的字。与摘要（正文字体）在视觉上
 *   明确分层，用户一眼就能分清「系统的转述」与「原文」。
 * - `<blockquote cite>` 是语义正确的元素，读屏用户据此知道它是一段引用。
 * - 外链 `target="_blank"` 必须配 `rel="noopener noreferrer"`；域名被用户拉黑时该链接**变灰
 *   不可点**并说明原因——一个仍然可点的「已屏蔽来源」是自相矛盾的。
 * - **「此源不再采纳」的入口就在这里**：用户识破一条烂证据的那一刻，正是他最想拉黑这个源的
 *   那一刻，让他为此再去信源页签是设计失败。
 *
 * 引文文本来自不可信网页，一律以**纯文本**渲染（React 默认转义），源码扫描门禁
 * `intelNoDangerousHtml.source.test.ts` 另有一道。
 */
export default function IntelQuoteBlock({
  quote,
  stale,
  blocked,
  nowMs,
  onBlockSource,
}: {
  quote: IntelQuote;
  /** 原文快照已被缓存淘汰（引文本身仍是当时逐字留存的那一段）。 */
  stale: boolean;
  /** 该来源域名已被用户拉黑 ⇒ 外链变灰不可点。 */
  blocked: boolean;
  nowMs: number;
  /** 缺省 ⇒ 不渲染拉黑入口（只读场景）。 */
  onBlockSource?: (domain: string) => void;
}) {
  const linkable = isSafeUrl(quote.docUrl) && !blocked;
  return (
    <figure className="m-0 flex flex-col gap-0.5" data-testid="intel-quote-block">
      <blockquote
        cite={quote.docUrl}
        className="m-0 border-l-2 border-[var(--accent)] bg-[var(--surface-2)]/40 py-1 pl-2 pr-1 font-mono text-[10px] leading-relaxed text-[var(--text)]"
      >
        {quote.text}
      </blockquote>
      {quote.hasImperative && (
        <span
          data-testid="intel-quote-imperative"
          className="text-[10px] text-[var(--muted)]"
        >
          原文含劝告性措辞，本系统不作任何建议
        </span>
      )}
      <figcaption className="flex flex-wrap items-center gap-1.5 text-[10px] text-[var(--muted)]">
        <span>{quote.docDomain || "未知来源"}</span>
        {quote.docPublishedTs > 0 && (
          <span>发布 {relativeTime(quote.docPublishedTs, nowMs)}</span>
        )}
        {quote.verifiedTs > 0 && <span>取证 {relativeTime(quote.verifiedTs, nowMs)}</span>}
        {linkable ? (
          <a
            href={quote.docUrl}
            target="_blank"
            rel="noopener noreferrer"
            className="rounded px-1 text-[var(--accent-ink)] hover:bg-[var(--accent-soft)]"
          >
            原文 ↗
          </a>
        ) : (
          <span
            data-testid="intel-quote-link-disabled"
            title={blocked ? "此源已被你屏蔽" : "该来源链接协议不受支持，已停用跳转"}
            className="rounded px-1 text-[var(--muted)]"
          >
            原文 ↗
          </span>
        )}
        {stale && <span data-testid="intel-quote-stale">原文快照已过期</span>}
        {onBlockSource && quote.docDomain && (
          <button
            type="button"
            aria-label={`不再采纳 ${quote.docDomain}`}
            title={`不再采纳 ${quote.docDomain}`}
            onClick={() => onBlockSource(quote.docDomain)}
            className="ml-auto rounded px-1 text-[10px] text-[var(--muted)] outline-none hover:text-[var(--short-ink)] focus-visible:ring-2 focus-visible:ring-[var(--accent)]"
          >
            此源不再采纳
          </button>
        )}
      </figcaption>
    </figure>
  );
}
