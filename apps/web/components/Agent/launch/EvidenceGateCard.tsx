"use client";

import {
  EVIDENCE_POLICY_OPTIONS,
  backfillSampleCount,
  bucketsForInst,
  daysToMinSamples,
  describeBackfill,
  describeLiveCovered,
  evidencePolicyOption,
  hasProvenBucket,
  type AiTradeEvidenceResponse,
} from "@/lib/aiTradeEvidence";
import { EvidenceSegments } from "@/components/Chart/AiTradeEvidenceRow";
import type { EvidenceGateFieldsView } from "./useEvidenceGateFields";

/**
 * 第 5 步「实证准入」卡片（ai-trade-evidence-gated-capital §3.5.3）。排在收益锁**之前**：
 * 它决定「开不开真单」，比「怎么离场」更靠前。
 *
 * 受控组件：档位、预览、回填全部来自 `useEvidenceGateFields`，**本组件不写任何 effect**。
 * 预览读取失败只显示一行错误，不进步骤模型——证据读不到不是不能启动的理由。
 */
interface Props {
  view: EvidenceGateFieldsView;
  disabled: boolean;
}

function shortInst(instId: string): string {
  return instId.split("-")[0] || instId;
}

function PolicyTabs({ view, disabled }: Props) {
  return (
    <div
      role="radiogroup"
      aria-label="实证准入档位"
      className="flex gap-1 rounded bg-[var(--surface-2)] p-0.5"
    >
      {EVIDENCE_POLICY_OPTIONS.map((option) => {
        const active = view.policy === option.value;
        return (
          <button
            key={option.value}
            type="button"
            role="radio"
            aria-checked={active}
            disabled={disabled}
            onClick={() => view.setPolicy(option.value)}
            data-testid={`ai-evidence-policy-${option.value}`}
            className={`flex-1 truncate whitespace-nowrap rounded px-2 py-1 text-[11px] ${
              active
                ? "bg-[var(--surface)] font-medium text-[var(--text)] shadow-sm"
                : "text-[var(--muted)] hover:text-[var(--text)]"
            } disabled:opacity-50`}
          >
            {option.label}
            {option.recommended ? "（推荐）" : ""}
          </button>
        );
      })}
    </div>
  );
}

function StrictEstimate({ data, instIds }: {
  data: AiTradeEvidenceResponse | null;
  instIds: readonly string[];
}) {
  if (hasProvenBucket(data)) return null;
  const days = daysToMinSamples(data, instIds);
  const minSamples = data?.rule.minSamples ?? 10;
  return (
    <p className="text-[11px] leading-relaxed text-[var(--warn)]" data-testid="ai-evidence-estimate">
      启动后前几天大概率只记影子、不下真实委托。
      {days != null && (
        <span data-testid="ai-evidence-estimate-days">
          按最近的开仓频率，每个方向攒满 {minSamples} 笔约需 {days} 天。
        </span>
      )}
    </p>
  );
}

function PreviewRows({ view }: { view: EvidenceGateFieldsView }) {
  const { preview, instIds } = view;
  if (preview.isError) {
    return (
      <p className="text-[10px] text-[var(--warn)]" data-testid="ai-evidence-preview-error">
        证据预览读取失败：{preview.error?.message ?? "未知错误"}（不影响启动）
      </p>
    );
  }
  if (!preview.data) {
    return <p className="text-[10px] text-[var(--muted)]">正在读取当前证据…</p>;
  }
  const data = preview.data;
  const fromBackfill = backfillSampleCount(data);
  return (
    <div className="flex flex-col gap-0.5" data-testid="ai-evidence-preview">
      {instIds.map((instId) => {
        const source = bucketsForInst(data, instId);
        const empty = Object.keys(source.sides).length === 0;
        return (
          <div key={instId} className="flex min-w-0 items-center gap-1.5 text-[10px]">
            <span className="w-[44px] shrink-0 truncate text-[var(--text)]">
              {shortInst(instId)}
            </span>
            {empty ? (
              <span className="text-[var(--muted)]">还没有样本</span>
            ) : (
              <EvidenceSegments source={source} />
            )}
          </div>
        );
      })}
      {fromBackfill > 0 && (
        <span className="text-[10px] text-[var(--muted)]">
          其中 {fromBackfill} 笔来自历史成交回填（只含真实成交过的开仓，带选择偏差）
        </span>
      )}
    </div>
  );
}

function BackfillRow({ view, disabled }: Props) {
  const { backfill } = view;
  const result = backfill.result;
  const label = result?.truncated ? "继续回填" : "用历史成交回填";
  const covered = result ? describeLiveCovered(result) : null;
  return (
    <div className="flex flex-col gap-0.5 text-[10px]">
      <div className="flex items-center gap-2">
        <button
          type="button"
          disabled={disabled || backfill.pending || view.instIds.length === 0}
          onClick={backfill.run}
          data-testid="ai-evidence-backfill"
          className="flex shrink-0 items-center gap-1 rounded border border-[var(--border)] px-2 py-0.5 text-[var(--accent-ink)] hover:bg-[var(--bg)] disabled:opacity-40"
        >
          {backfill.pending && (
            <span className="h-2.5 w-2.5 animate-spin rounded-full border-2 border-[var(--border)] border-t-[var(--accent)]" />
          )}
          {label}
        </button>
        {backfill.pending && (
          <span className="text-[var(--muted)]">
            回填需要逐段拉取历史 K 线，可能要一两分钟
          </span>
        )}
      </div>
      {result && !backfill.pending && (
        <span
          className="text-[var(--text)]"
          data-testid="ai-evidence-backfill-result"
          title={result.notes.join("\n") || undefined}
        >
          {describeBackfill(result)}
          {covered ? `；${covered}` : ""}
        </span>
      )}
      {backfill.error && !backfill.pending && (
        <span className="text-[var(--short-ink)]" role="alert" data-testid="ai-evidence-backfill-error">
          回填失败：{backfill.error}
        </span>
      )}
    </div>
  );
}

export default function EvidenceGateCard({ view, disabled }: Props) {
  if (!view.active) {
    return (
      <p className="text-[11px] text-[var(--muted)]" data-testid="ai-evidence-card-backtest">
        实证准入只作用于实盘：AI 回测本身就在产生证据，不拦截、也不记影子。
      </p>
    );
  }
  const option = evidencePolicyOption(view.policy);
  return (
    <div
      className="flex flex-col gap-1.5 rounded-md border border-[var(--accent-soft)] bg-[color-mix(in_srgb,var(--accent)_5%,transparent)] px-3 py-2"
      data-testid="ai-evidence-card"
    >
      <span className="text-xs font-medium text-[var(--text)]">实证准入：先证后投</span>
      <PolicyTabs view={view} disabled={disabled} />
      <span className="text-[11px] leading-relaxed text-[var(--muted)]" data-testid="ai-evidence-hint">
        {option.hint}
      </span>
      {view.policy === "strict" && (
        <StrictEstimate data={view.preview.data ?? null} instIds={view.instIds} />
      )}
      <PreviewRows view={view} />
      <BackfillRow view={view} disabled={disabled} />
    </div>
  );
}
