"use client";

import { Badge } from "@/components/ui";
import { describeAiAction, describeStance } from "@/lib/aiTrade";
import type { AiTradeDecision, StrategySignalView } from "@/lib/aiTradeTypes";
import { fmtMoney, fmtNum, fmtTime } from "@/lib/format";
import { useAppStore } from "@/lib/store";

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

  return (
    <div className="flex flex-col gap-2 border-b border-[var(--border)] px-3 py-2.5">
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-1.5">
          <span className="text-xs font-semibold text-[var(--text)]">AI 自动交易</span>
          <Badge tone={stopped ? "neutral" : "long"}>{stopped ? "已停止" : "运行中"}</Badge>
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
          value={`第 ${session.roundsCompleted} 轮 · 摘要 ${session.roundsSinceSummary}/${session.contextRefreshRounds}`}
        />
      </div>

      {session.degraded && (
        <p className="rounded border border-[var(--border)] bg-[var(--surface-2)] px-2 py-1 text-[10px] text-[var(--short)]">
          决策降级中（LLM 超时 / 未产出有效决策 → 本轮回落观望，资金安全优先）
        </p>
      )}

      {board.length > 0 && <SignalBoard board={board} />}

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

function SignalBoard({ board }: { board: StrategySignalView[] }) {
  return (
    <div className="flex flex-col gap-px overflow-hidden rounded border border-[var(--border)]">
      <div className="bg-[var(--surface-2)] px-2 py-1 text-[10px] font-semibold uppercase tracking-wide text-[var(--muted)]">
        策略信号板
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
  return (
    <div className="rounded border border-[var(--border)] bg-[var(--bg)] px-2 py-1.5 text-[11px]">
      <div className="flex items-center justify-between gap-2">
        <span className="font-medium text-[var(--text)]">
          本轮决策：{describeAiAction(decision.action)}
        </span>
        <span className="tabular text-[var(--muted)]">
          {decision.confidence != null ? `置信 ${fmtNum(decision.confidence * 100, 0)}%` : ""}
        </span>
      </div>
      <p className="mt-0.5 leading-relaxed text-[var(--muted)]">{decision.reason}</p>
    </div>
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
        {recent.map((d, i) => (
          <div key={`${d.round}-${d.ts}-${i}`} className="flex items-start gap-2">
            <span className="tabular shrink-0 text-[var(--muted)]">{fmtTime(d.ts, false)}</span>
            <span className="shrink-0 font-medium text-[var(--text)]">{describeAiAction(d.action)}</span>
            <span className="truncate">{d.reason}</span>
          </div>
        ))}
      </div>
    </details>
  );
}

function describePosition(side: string): string {
  if (side === "long") return "多";
  if (side === "short") return "空";
  return "空仓";
}
