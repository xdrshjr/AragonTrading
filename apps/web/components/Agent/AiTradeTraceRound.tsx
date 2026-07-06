"use client";

import { useState } from "react";
import {
  type AiTraceTone,
  describeAiAction,
  describeConsensus,
  describeExecuted,
  describeRegime,
  describeStance,
  describeTraceState,
} from "@/lib/aiTrade";
import type { AiTradeRoundTrace } from "@/lib/aiTradeTypes";
import { fmtNum, fmtPrice, fmtTime } from "@/lib/format";
import { renderMarkdown } from "@/lib/markdown";

const TONE_VAR: Record<AiTraceTone, string> = {
  long: "var(--long)",
  short: "var(--short)",
  muted: "var(--muted)",
  accent: "var(--accent)",
};

function toneSoft(tone: AiTraceTone): string {
  return `color-mix(in srgb, ${TONE_VAR[tone]} 14%, transparent)`;
}

/** 轮头：第 N 轮 · 时间 · 最新价 + 右侧状态 chip（deciding 附脉冲点）。 */
function RoundHeader({ trace }: { trace: AiTradeRoundTrace }) {
  const chip = describeTraceState(trace);
  return (
    <div className="flex items-center justify-between gap-2">
      <span className="flex items-center gap-1.5 text-[11px] text-[var(--muted)]">
        <span className="font-semibold text-[var(--text)]">第 {trace.round} 轮</span>
        <span className="tabular">{fmtTime(trace.ts, false)}</span>
        {trace.lastPx != null && <span className="tabular">· {fmtPrice(trace.lastPx)}</span>}
      </span>
      <span
        className="inline-flex items-center gap-1 rounded-full px-1.5 py-0.5 text-[10px] font-medium"
        style={{ color: TONE_VAR[chip.tone], backgroundColor: toneSoft(chip.tone) }}
      >
        {chip.tone === "accent" && (
          <span
            className="inline-block h-1.5 w-1.5 animate-pulse rounded-full"
            style={{ backgroundColor: TONE_VAR[chip.tone] }}
          />
        )}
        {chip.label}
      </span>
    </div>
  );
}

/** 思考/分析：深度思考折叠置于其上；分析流式走 pre-wrap + 光标，轮结束走安全 markdown。 */
function AnalysisBlock({ trace }: { trace: AiTradeRoundTrace }) {
  const streaming = trace.state === "deciding";
  const hasAnalysis = trace.analysis.length > 0;
  if (!hasAnalysis && !streaming && !trace.thinking) return null;
  return (
    <div className="flex flex-col gap-1.5">
      {trace.thinking && (
        <details className="text-[11px] text-[var(--muted)]">
          <summary className="cursor-pointer select-none text-[var(--text)]">深度思考</summary>
          <p className="mt-1 whitespace-pre-wrap leading-relaxed">{trace.thinking}</p>
        </details>
      )}
      {(hasAnalysis || streaming) && (
        <div className="rounded-[10px] bg-[var(--surface-2)] px-2.5 py-2 text-[12px] leading-relaxed text-[var(--text)]">
          {!hasAnalysis ? (
            <span className="text-[var(--muted)]">正在研判…</span>
          ) : streaming ? (
            <span className="whitespace-pre-wrap">
              {trace.analysis}
              <span className="ml-0.5 inline-block h-3.5 w-[2px] translate-y-0.5 animate-pulse bg-current align-middle" />
            </span>
          ) : (
            <div className="flex flex-col">{renderMarkdown(trace.analysis)}</div>
          )}
        </div>
      )}
    </div>
  );
}

/** 决策工具卡：动作 + 仓位/置信 + reason，可展开原始 input JSON；pending 时占位。 */
function DecisionToolCard({ trace }: { trace: AiTradeRoundTrace }) {
  const [open, setOpen] = useState(false);
  const call = trace.toolCall;
  if (!call) {
    if (!trace.toolCallPending) return null;
    return (
      <div className="flex items-center gap-2 rounded-[10px] border border-[var(--border)] bg-[var(--surface)] px-2.5 py-1.5 text-[11px] text-[var(--muted)]">
        <span className="text-sm leading-none">🛠️</span>
        <span>调用决策工具…</span>
        <span className="ml-auto inline-block h-1.5 w-1.5 animate-pulse rounded-full bg-[var(--accent)]" />
      </div>
    );
  }
  const isOpen = call.action === "open_long" || call.action === "open_short";
  return (
    <div className="overflow-hidden rounded-[10px] border border-[var(--border)] bg-[var(--surface)] text-xs">
      <button
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-2 px-2.5 py-1.5 text-left transition-colors duration-150 ease-smooth hover:bg-[var(--surface-2)]"
      >
        <span className="text-sm leading-none">🛠️</span>
        <span className="font-medium text-[var(--text)]">决策工具 · {describeAiAction(call.action)}</span>
        <span className="ml-auto tabular text-[10px] text-[var(--muted)]">
          {isOpen && call.sizeFraction != null ? `仓位 ${fmtNum(call.sizeFraction * 100, 0)}% · ` : ""}
          {call.confidence != null ? `置信 ${fmtNum(call.confidence * 100, 0)}%` : ""}
        </span>
        <span className="text-[var(--muted)]">{open ? "▾" : "▸"}</span>
      </button>
      {call.reason && (
        <p className="border-t border-[var(--border)] px-2.5 py-1.5 text-[11px] leading-relaxed text-[var(--muted)]">
          {call.reason}
        </p>
      )}
      {open && (
        <pre className="overflow-x-auto whitespace-pre-wrap break-all border-t border-[var(--border)] bg-[var(--surface-2)] px-2.5 py-2 text-[10px] leading-relaxed text-[var(--muted)]">
          {JSON.stringify(call, null, 2)}
        </pre>
      )}
    </div>
  );
}

/** 执行结果行：按 AiExecTone 着色的状态点 + 中文标签 + 结果持仓方向；异常轮显示 errorMessage。 */
function ResultRow({ trace }: { trace: AiTradeRoundTrace }) {
  if (trace.errorMessage) {
    return (
      <p className="rounded border border-[var(--border)] bg-[var(--surface-2)] px-2 py-1 text-[11px] text-[var(--short)]">
        ⚠️ {trace.errorMessage}
      </p>
    );
  }
  if (!trace.executed) return null;
  const action = trace.toolCall?.action ?? "hold";
  const exec = describeExecuted(action, trace.executed, trace.degradeReason);
  const posAfter = trace.executed.positionAfter;
  return (
    <div className="flex items-center gap-1.5 text-[11px]" style={{ color: TONE_VAR[exec.tone] }}>
      <span
        className="inline-block h-1.5 w-1.5 rounded-full"
        style={{ backgroundColor: TONE_VAR[exec.tone] }}
      />
      <span className="font-medium">{exec.label}</span>
      {posAfter && (
        <span className="text-[var(--muted)]">· 结果持仓 {describeStance(posAfter)}</span>
      )}
    </div>
  );
}

/** 输入信号板 / 体制 / 共识（紧凑折叠，复用左栏同款 describe）。 */
function SignalContext({ trace }: { trace: AiTradeRoundTrace }) {
  const regime = describeRegime(trace.marketContext);
  const consensus = describeConsensus(trace.consensus ?? null);
  const board = trace.signalBoard ?? [];
  if (!board.length && !regime && !consensus) return null;
  return (
    <details className="text-[11px] text-[var(--muted)]">
      <summary className="cursor-pointer select-none text-[var(--text)]">
        输入信号板{board.length ? `（${board.length}）` : ""}
      </summary>
      <div className="mt-1 flex flex-col gap-1">
        {(regime || consensus) && (
          <div className="flex flex-wrap gap-x-2 gap-y-0.5">
            {regime && <span>{regime}</span>}
            {consensus && <span>{consensus}</span>}
          </div>
        )}
        {board.map((s) => (
          <div key={s.strategyId} className="flex items-center justify-between gap-2">
            <span className="truncate text-[var(--text)]">{s.name}</span>
            <span className="shrink-0">
              立场 {describeStance(s.stance)}
              {s.latestSignal !== "none" && (
                <span className="text-[var(--accent)]"> · {describeAiAction(s.latestSignal)}</span>
              )}
            </span>
          </div>
        ))}
      </div>
    </details>
  );
}

/** 上下文刷新节点（G2）：醒目分隔 + 可展开摘要全文，直观体现「上下文纯净」机制。 */
function SummaryNode({ round, summary }: { round: number; summary: string }) {
  return (
    <details className="rounded-[8px] border border-[var(--accent-soft)] bg-[var(--accent-soft)] px-2.5 py-1.5 text-[11px]">
      <summary className="cursor-pointer select-none font-medium text-[var(--accent)]">
        🔄 上下文已刷新（第 {round} 轮摘要）
      </summary>
      <p className="mt-1 whitespace-pre-wrap leading-relaxed text-[var(--text)]">{summary}</p>
    </details>
  );
}

/**
 * 单轮决策认知卡（ai-auto-trading-agent-trace §3.7）：轮头 → 思考/分析 → 工具调用 → 执行结果
 * → 信号板 →（到期）摘要，自上而下讲一个完整决策故事。
 */
export default function AiTradeTraceRound({ trace }: { trace: AiTradeRoundTrace }) {
  return (
    <div className="flex flex-col gap-2 rounded-[12px] border border-[var(--border)] bg-[var(--surface)] px-3 py-2.5">
      <RoundHeader trace={trace} />
      <AnalysisBlock trace={trace} />
      <DecisionToolCard trace={trace} />
      <ResultRow trace={trace} />
      <SignalContext trace={trace} />
      {trace.summary && <SummaryNode round={trace.round} summary={trace.summary} />}
    </div>
  );
}
