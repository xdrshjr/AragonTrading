"use client";

import { Skeleton } from "@/components/ui";
import { docStale, hasStaleSnapshot, splitQuotes } from "@/lib/intelEvidence";
import type { IntelEvidence } from "@/lib/intelTypes";
import IntelQuoteBlock from "./IntelQuoteBlock";

/**
 * 证据抽屉（ai-trade-intel-evidence-grounding §3.8）。
 *
 * **在同一张卡片内向下推开**，不遮挡任何东西——证据是**按需**的信息，不是首屏信息，也不该是
 * 一个弹窗里的弹窗。
 *
 * 两组引文分开渲染（「准入时的原文依据」/「核查时的原文依据」）：混在一起会让用户读不出
 * 「这条被推翻，是因为后来出现了这段字」。
 *
 * 缓存已被淘汰时那句「原文快照已过期，上方引文仍为当时逐字留存」**必须出现**——否则用户会把
 * 「没有 doc 行」误读成「这条情报没有来源」。
 */
export default function IntelEvidenceDrawer({
  id,
  evidence,
  loading,
  error,
  blockedDomains,
  nowMs,
  onBlockSource,
}: {
  id: string;
  /** `null` = 还没取回来（配合 `loading` 决定骨架屏还是错误行）。 */
  evidence: IntelEvidence | null;
  loading: boolean;
  error: string;
  blockedDomains: string[];
  nowMs: number;
  onBlockSource?: (domain: string) => void;
}) {
  if (loading && !evidence) {
    return (
      <div id={id} role="region" aria-label="原文引证" className="flex flex-col gap-1 pt-1">
        <Skeleton className="h-8 w-full rounded-[8px]" />
      </div>
    );
  }
  if (error) {
    return (
      <div id={id} role="region" aria-label="原文引证" className="pt-1">
        <span className="text-[10px] text-[var(--short-ink)]">{error}</span>
      </div>
    );
  }
  const quotes = evidence?.quotes ?? [];
  const { admit, verify } = splitQuotes(quotes);
  const blocked = new Set(blockedDomains);
  return (
    <div
      id={id}
      role="region"
      aria-label="原文引证"
      data-testid="intel-evidence-drawer"
      className="flex flex-col gap-1.5 pt-1"
    >
      {!quotes.length && (
        <span className="text-[10px] text-[var(--muted)]">这一条没有留存任何原文引证。</span>
      )}
      <QuoteGroup
        title="准入时的原文依据"
        quotes={admit}
        evidence={evidence}
        blocked={blocked}
        nowMs={nowMs}
        onBlockSource={onBlockSource}
      />
      <QuoteGroup
        title="核查时的原文依据"
        quotes={verify}
        evidence={evidence}
        blocked={blocked}
        nowMs={nowMs}
        onBlockSource={onBlockSource}
      />
      {hasStaleSnapshot(evidence) && (
        <span data-testid="intel-evidence-stale" className="text-[10px] text-[var(--muted)]">
          原文快照已过期，上方引文仍为当时逐字留存
        </span>
      )}
    </div>
  );
}

function QuoteGroup({
  title,
  quotes,
  evidence,
  blocked,
  nowMs,
  onBlockSource,
}: {
  title: string;
  quotes: IntelEvidence["quotes"];
  evidence: IntelEvidence | null;
  blocked: Set<string>;
  nowMs: number;
  onBlockSource?: (domain: string) => void;
}) {
  if (!quotes.length) return null;
  return (
    <div className="flex flex-col gap-1">
      <span className="text-[10px] font-medium text-[var(--muted)]">{title}</span>
      {quotes.map((quote) => (
        <IntelQuoteBlock
          key={quote.id}
          quote={quote}
          stale={docStale(quote, evidence?.docs ?? [])}
          blocked={blocked.has(quote.docDomain)}
          nowMs={nowMs}
          onBlockSource={onBlockSource}
        />
      ))}
    </div>
  );
}
