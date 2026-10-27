"use client";

import {
  EVIDENCE_SHADOW_TITLE,
  EVIDENCE_SNAPSHOT_ERROR_TEXT,
  evidenceRowTitle,
  sideSegments,
  type AiTradeEvidenceState,
  type EvidenceSidesSource,
} from "@/lib/aiTradeEvidence";
import type { AiTradeGroupMemberState } from "@/lib/aiTradeTypes";

// 持仓行的「实证准入」一行（ai-trade-evidence-gated-capital §3.5.1）。
//
// 门控：`member.session.evidence` 整键缺席 ⇒ 整行不渲染（未装配的会话上绝不挂一排「—」）。
// 与收益锁行不同，本行**不看持仓**：它回答的是「下一次开仓会不会下真单」，空仓时恰恰最需要。
// 单行不折行（工作台零折行契约），会话级合计放在悬停说明里。

/** 文字色只用 contrastTokens 门禁允许的墨色；圆点是面色（背景，不是文字）。 */
const STATE_INK: Record<AiTradeEvidenceState, string> = {
  proven: "text-[var(--long-ink)]",
  unproven: "text-[var(--muted)]",
  disproven: "text-[var(--short-ink)]",
};

const STATE_DOT: Record<AiTradeEvidenceState, string> = {
  proven: "bg-[var(--long)]",
  unproven: "bg-[var(--muted)]",
  disproven: "bg-[var(--short)]",
};

/** 多 / 空两段（成员行与向导预览共用同一个渲染，§3.5.3「同一个纯函数渲染」）。 */
export function EvidenceSegments({ source }: { source: EvidenceSidesSource }) {
  const segments = sideSegments(source);
  return (
    <>
      {segments.map((seg) => (
        <span
          key={seg.side}
          data-testid={`ai-evidence-side-${seg.side}`}
          data-state={seg.state}
          className="inline-flex shrink-0 items-center gap-1 whitespace-nowrap"
        >
          <span className="text-[var(--muted)]">· {seg.sideLabel}</span>
          <span className={`inline-block h-1.5 w-1.5 rounded-full ${STATE_DOT[seg.state]}`} />
          <span className={STATE_INK[seg.state]}>{seg.stateLabel}</span>
          <span className="tabular text-[var(--text)]">{seg.reading}</span>
          {seg.isShadow && (
            <span
              data-testid={`ai-evidence-shadow-${seg.side}`}
              title={EVIDENCE_SHADOW_TITLE}
              className="rounded border border-[var(--border)] px-1 text-[9px] text-[var(--accent-ink)]"
            >
              影子
            </span>
          )}
        </span>
      ))}
    </>
  );
}

export default function AiTradeEvidenceRow({ member }: { member: AiTradeGroupMemberState }) {
  const status = member.session?.evidence;
  if (!status) return null;
  return (
    <span
      data-testid="ai-trade-evidence"
      title={evidenceRowTitle(status)}
      className="flex min-w-0 items-center gap-1.5 overflow-hidden whitespace-nowrap text-[10px]"
    >
      <span className="shrink-0 text-[var(--muted)]">实证</span>
      {status.snapshotError && (
        // 排在两段之前：单行截断时它必须最后一个被裁掉——证据没读到是比任何读数都要紧的事实。
        <span className="shrink-0 text-[var(--warn)]" data-testid="ai-trade-evidence-snapshot-error">
          {EVIDENCE_SNAPSHOT_ERROR_TEXT}
        </span>
      )}
      <EvidenceSegments source={status} />
    </span>
  );
}
