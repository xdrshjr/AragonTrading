"use client";

import { Badge } from "@/components/ui";
import {
  type AiExecTone,
  describeAiAction,
  describeConsensus,
  describeExecuted,
  describeRegime,
  describeStance,
} from "@/lib/aiTrade";
import type { AiTradeDecision, Consensus, StrategySignalView } from "@/lib/aiTradeTypes";
import { fmtMoney, fmtNum, fmtTime } from "@/lib/format";
import { useAppStore } from "@/lib/store";

const EXEC_TONE_VAR: Record<AiExecTone, string> = {
  long: "var(--long)",
  short: "var(--short)",
  muted: "var(--muted)",
};

/**
 * AI 自动交易运行期面板（ai-auto-trading §5.4，P1-7 挂 WatchlistTab 左栏）：仅当存在活跃 AI 会话时呈现。
 * 展示会话概览（标的/预算/持仓/累计盈亏）、策略信号板（各策略立场 + 本根边沿）、最近决策与滚动摘要。
 */
export default function AiTradePanel() {
  const session = useAppStore((s) => s.aiTradeSession);
  const decisions = useAppStore((s) => s.aiTradeDecisions);
  const summary = useAppStore((s) => s.aiTradeSummary);

  if (!session) return null;

  const pnl = session.sessionPnlUsd ?? 0;
  const board = session.lastDecision?.signalBoard ?? [];
  const stopped = session.state === "stopped";
  const regime = describeRegime(session.lastDecision?.marketContext);   // E1
  const consensus = session.lastDecision?.consensus ?? null;            // E2
  const roundsToSummary = Math.max(
    0,
    (session.contextRefreshRounds ?? 0) - (session.roundsSinceSummary ?? 0),
  );

  return (
    <div className="flex flex-col gap-2 border-b border-[var(--border)] px-3 py-2.5">
      <div className="flex items-center justify-between gap-2">
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="text-xs font-semibold text-[var(--text)]">AI 自动交易</span>
          <Badge tone={stopped ? "neutral" : "long"}>{stopped ? "已停止" : "运行中"}</Badge>
          {regime && <Badge tone="neutral">{regime}</Badge>}
        </div>
        <span
          className={
            pnl >= 0
              ? "tabular text-xs font-medium text-[var(--long)]"
              : "tabular text-xs font-medium text-[var(--short)]"
          }
        >
          {pnl >= 0 ? "+" : ""}
          {fmtMoney(pnl)}
        </span>
      </div>

      <div className="grid grid-cols-2 gap-x-3 gap-y-1 text-[11px] text-[var(--muted)]">
        <Cell label="标的" value={`${session.instId} · ${session.bar}`} />
        <Cell label="持仓" value={describePosition(session.positionSide)} />
        <Cell label="预算" value={`${fmtNum(session.budgetUsd)} USDT · ${session.leverage}×`} />
        <Cell
          label="上下文"
          value={`第 ${session.roundsCompleted} 轮 · 距下次摘要 ${roundsToSummary} 轮`}
        />
      </div>

      {session.degraded && (
        <p className="rounded border border-[var(--border)] bg-[var(--surface-2)] px-2 py-1 text-[10px] text-[var(--short)]">
          决策降级中（LLM 超时 / 未产出有效决策 → 本轮回落观望，资金安全优先）
        </p>
      )}

      {board.length > 0 && <SignalBoard board={board} consensus={consensus} />}

      {session.lastDecision && <LastDecision decision={session.lastDecision} />}

      {decisions.length > 1 && <DecisionLog decisions={decisions} />}

      {summary && (
        <details className="text-[11px] text-[var(--muted)]">
          <summary className="cursor-pointer select-none text-[var(--text)]">上下文摘要</summary>
          <p className="mt-1 whitespace-pre-wrap leading-relaxed">{summary}</p>
        </details>
      )}
    </div>
  );
}

function Cell({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between gap-2">
      <span>{label}</span>
      <span className="tabular truncate text-right font-medium text-[var(--text)]">{value}</span>
    </div>
  );
}

function SignalBoard({ board, consensus }: { board: StrategySignalView[]; consensus: Consensus | null }) {
  const consensusText = describeConsensus(consensus);   // E2 共识度计
  const netTone =
    consensus?.netStance === "long"
      ? "var(--long)"
      : consensus?.netStance === "short"
        ? "var(--short)"
        : "var(--muted)";
  return (
    <div className="flex flex-col gap-px overflow-hidden rounded border border-[var(--border)]">
      <div className="flex items-center justify-between gap-2 bg-[var(--surface-2)] px-2 py-1">
        <span className="text-[10px] font-semibold uppercase tracking-wide text-[var(--muted)]">
          策略信号板
        </span>
        {consensusText && (
          <span className="tabular text-[10px] font-medium" style={{ color: netTone }}>
            {consensusText}
          </span>
        )}
      </div>
      {board.map((s) => (
        <div
          key={s.strategyId}
          className="flex items-center justify-between gap-2 border-t border-[var(--border)] px-2 py-1 text-[11px]"
        >
          <span className="truncate text-[var(--text)]">{s.name}</span>
          <span className="flex shrink-0 items-center gap-1.5 text-[var(--muted)]">
            <span>立场 {describeStance(s.stance)}</span>
            {s.latestSignal !== "none" && (
              <span className="text-[var(--accent)]">· {describeAiAction(s.latestSignal)}</span>
            )}
          </span>
        </div>
      ))}
    </div>
  );
}

function LastDecision({ decision }: { decision: AiTradeDecision }) {
  const exec = describeExecuted(decision.action, decision.executed, decision.degradeReason);
  const isOpen = decision.action === "open_long" || decision.action === "open_short";
  return (
    <div className="rounded border border-[var(--border)] bg-[var(--bg)] px-2 py-1.5 text-[11px]">
      <div className="flex items-center justify-between gap-2">
        <span className="flex items-center gap-1.5 font-medium text-[var(--text)]">
          本轮决策：{describeAiAction(decision.action)}
          <ExecDot view={exec} />
        </span>
        <span className="tabular text-[var(--muted)]">
          {isOpen && decision.sizeFraction != null ? `仓位 ${fmtNum(decision.sizeFraction * 100, 0)}% · ` : ""}
          {decision.confidence != null ? `置信 ${fmtNum(decision.confidence * 100, 0)}%` : ""}
        </span>
      </div>
      <p className="mt-0.5 leading-relaxed text-[var(--muted)]">{decision.reason}</p>
    </div>
  );
}

/** 执行结果态状态点（E3）：小圆点 + 中文标签，按净立场着色。 */
function ExecDot({ view }: { view: { label: string; tone: AiExecTone } }) {
  const color = EXEC_TONE_VAR[view.tone];
  return (
    <span className="inline-flex items-center gap-1 text-[10px] font-normal" style={{ color }}>
      <span className="inline-block h-1.5 w-1.5 rounded-full" style={{ backgroundColor: color }} />
      {view.label}
    </span>
  );
}

function DecisionLog({ decisions }: { decisions: AiTradeDecision[] }) {
  const recent = decisions.slice(-6).reverse();
  return (
    <details className="text-[11px] text-[var(--muted)]">
      <summary className="cursor-pointer select-none text-[var(--text)]">
        决策日志（近 {recent.length} 条）
      </summary>
      <div className="mt-1 flex flex-col gap-1">
        {recent.map((d, i) => {
          const exec = describeExecuted(d.action, d.executed, d.degradeReason);
          return (
            <div key={`${d.round}-${d.ts}-${i}`} className="flex items-start gap-2">
              <span className="tabular shrink-0 text-[var(--muted)]">{fmtTime(d.ts, false)}</span>
              <span className="shrink-0 font-medium text-[var(--text)]">{describeAiAction(d.action)}</span>
              <span className="shrink-0" style={{ color: EXEC_TONE_VAR[exec.tone] }}>{exec.label}</span>
              <span className="truncate">{d.reason}</span>
            </div>
          );
        })}
      </div>
    </details>
  );
}

function describePosition(side: string): string {
  if (side === "long") return "多";
  if (side === "short") return "空";
  return "空仓";
}
