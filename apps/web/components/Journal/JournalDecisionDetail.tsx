"use client";

import { useEffect, useState } from "react";
import { Badge, Skeleton } from "@/components/ui";
import {
  actionLabel,
  captureTierLabel,
  degradeLabel,
  formatLocal,
  formatNumber,
  formatUtc,
  triggerLabel,
} from "@/lib/journal";
import { journalApi } from "@/lib/journalApi";
import type { DecisionDetail } from "@/lib/journalTypes";
import JournalContextSections from "./JournalContextSections";
import JournalGateChain from "./JournalGateChain";
import JournalOutcomeBlock from "./JournalOutcomeBlock";
import JournalPromptBlock from "./JournalPromptBlock";

/**
 * 详情七段的壳与折叠状态（ai-trade-audit-journal §2.6.2）。
 *
 * 1 结论 / 2 门禁链 / 3 上下文 / 4 提示词 / 5 模型输出 / 6 执行与结果 / 7 遥测。
 *
 * 三态齐全：载入（Skeleton）/ 空 / 错误（含重试按钮与错误码）。空态说清**为什么空**。
 */
const SECTIONS = [
  { key: "conclusion", label: "结论" },
  { key: "gates", label: "门禁链" },
  { key: "context", label: "上下文" },
  { key: "prompt", label: "提示词" },
  { key: "output", label: "模型输出" },
  { key: "outcome", label: "执行与结果" },
  { key: "telemetry", label: "遥测" },
] as const;

function Row({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-3 py-0.5">
      <span className="whitespace-nowrap text-[11px] text-[var(--muted)]">{label}</span>
      <span className="tabular text-xs">{value}</span>
    </div>
  );
}

export default function JournalDecisionDetail({ decisionId }: { decisionId: number | null }) {
  const [detail, setDetail] = useState<DecisionDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [open, setOpen] = useState<Record<string, boolean>>({
    conclusion: true,
    gates: true,
    outcome: true,
  });

  useEffect(() => {
    if (decisionId == null) {
      setDetail(null);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError("");
    journalApi
      .decision(decisionId)
      .then((data) => {
        if (!cancelled) setDetail(data);
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof Error ? err.message : String(err));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [decisionId]);

  if (decisionId == null) {
    return (
      <div className="flex h-full items-center justify-center p-6 text-center text-xs text-[var(--muted)]">
        在左侧选一条决策轮次，这里会显示它的完整留痕：模型看到了什么、说了什么、
        被哪一层门禁改写、最终成交成什么样。
      </div>
    );
  }

  if (loading && !detail) {
    return (
      <div className="space-y-2 p-3">
        {[0, 1, 2, 3, 4].map((i) => (
          <Skeleton key={i} className="h-10 w-full" />
        ))}
      </div>
    );
  }

  if (error) {
    return (
      <div className="p-6 text-center text-xs text-[var(--short-ink)]">
        载入详情失败：{error}
      </div>
    );
  }

  if (!detail) return null;

  const brief = detail.brief;
  const rewritten = brief.finalAction !== brief.action;
  const telemetry = detail.telemetry ?? {};

  return (
    <div className="h-full min-h-0 overflow-y-auto p-3">
      <header className="mb-2">
        <h3 className="text-sm font-medium">
          第 {brief.roundNo} 轮 · {brief.instId} · {brief.bar}
        </h3>
        <p className="text-[11px] text-[var(--muted)]">
          {formatLocal(brief.wallTs || brief.ts)}（本地） · {formatUtc(brief.ts)}（UTC）
        </p>
      </header>

      {SECTIONS.map((section) => {
        const expanded = open[section.key] ?? false;
        return (
          <section key={section.key} className="mb-1.5 rounded-[10px] border border-[var(--border)]">
            <button
              type="button"
              aria-expanded={expanded}
              onClick={() => setOpen((prev) => ({ ...prev, [section.key]: !expanded }))}
              className="flex w-full items-center justify-between px-2 py-1.5 text-left text-xs font-medium"
            >
              <span>{section.label}</span>
              <span aria-hidden="true" className="text-[var(--muted)]">
                {expanded ? "−" : "+"}
              </span>
            </button>
            {expanded && (
              <div className="border-t border-[var(--border)] p-2">
                {section.key === "conclusion" && (
                  <div>
                    <Row label="模型动作" value={actionLabel(brief.action)} />
                    <Row
                      label="最终动作"
                      value={
                        <span>
                          {actionLabel(brief.finalAction)}
                          {rewritten && detail.vetoedBy && (
                            <Badge tone="accent" className="ml-1.5">
                              被门禁改写
                            </Badge>
                          )}
                        </span>
                      }
                    />
                    <Row
                      label="仓位比例"
                      value={brief.sizeFraction == null ? "—" : formatNumber(brief.sizeFraction, 3)}
                    />
                    <Row
                      label="置信度"
                      value={brief.confidence == null ? "—" : formatNumber(brief.confidence, 3)}
                    />
                    <Row label="委托方式" value={detail.ordType ?? "—"} />
                    <Row
                      label="限价"
                      value={detail.limitPx == null ? "—" : formatNumber(detail.limitPx, 4)}
                    />
                    <Row label="唤醒原因" value={triggerLabel(brief.trigger) || "—"} />
                    <Row label="降级原因" value={degradeLabel(brief.degradeReason) || "—"} />
                    <Row label="反思裁决" value={brief.reflectionVerdict ?? "—"} />
                    <Row label="决策理由" value={detail.reason || "—"} />
                  </div>
                )}
                {section.key === "gates" && (
                  <JournalGateChain gates={detail.gates} vetoedBy={detail.vetoedBy} />
                )}
                {section.key === "context" && (
                  <JournalContextSections
                    payload={detail.payload}
                    truncated={detail.payloadTruncated}
                    captureTier={captureTierLabel(detail.captureTier)}
                  />
                )}
                {section.key === "prompt" && (
                  <JournalPromptBlock decisionId={brief.id} block={detail.prompt} />
                )}
                {section.key === "output" && (
                  <div className="space-y-2">
                    <div className="min-w-0 overflow-x-auto rounded-[10px] border border-[var(--border)] p-2">
                      <pre className="whitespace-pre text-[11px] leading-4">
                        {detail.output ? JSON.stringify(detail.output, null, 2) : "该档未留存模型输出"}
                      </pre>
                    </div>
                    {detail.analysis && (
                      <details>
                        <summary className="cursor-pointer text-[11px] text-[var(--muted)]">
                          研判思维链
                        </summary>
                        <pre className="mt-1 whitespace-pre-wrap break-words text-[11px] leading-4">
                          {detail.analysis}
                        </pre>
                      </details>
                    )}
                    {detail.reflection && (
                      <details>
                        <summary className="cursor-pointer text-[11px] text-[var(--muted)]">
                          下单前反思复核
                        </summary>
                        <pre className="mt-1 whitespace-pre-wrap break-words text-[11px] leading-4">
                          {detail.reflection}
                        </pre>
                      </details>
                    )}
                    {detail.thinking && (
                      <details>
                        <summary className="cursor-pointer text-[11px] text-[var(--muted)]">
                          深度思考
                        </summary>
                        <pre className="mt-1 whitespace-pre-wrap break-words text-[11px] leading-4">
                          {detail.thinking}
                        </pre>
                      </details>
                    )}
                  </div>
                )}
                {section.key === "outcome" && (
                  <JournalOutcomeBlock outcome={detail.outcome} fillRef={detail.fillRef} />
                )}
                {section.key === "telemetry" && (
                  <div>
                    <Row label="provider" value={telemetry.provider ?? "—"} />
                    <Row label="模型" value={telemetry.model ?? "—"} />
                    <Row label="输入 token" value={telemetry.inputTokens ?? "—"} />
                    <Row label="输出 token" value={telemetry.outputTokens ?? "—"} />
                    <Row
                      label="决策耗时 (ms)"
                      value={telemetry.latencyMs === undefined ? "—" : formatNumber(telemetry.latencyMs, 0)}
                    />
                    <Row label="重试次数" value={telemetry.retries ?? 0} />
                    <Row label="研判深度档" value={telemetry.reasoningDepth ?? "—"} />
                    <Row label="采集档" value={captureTierLabel(detail.captureTier)} />
                    <Row label="上下文被截断" value={detail.payloadTruncated ? "是" : "否"} />
                  </div>
                )}
              </div>
            )}
          </section>
        );
      })}
    </div>
  );
}
