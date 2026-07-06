"use client";

import { useEffect, useRef, useState } from "react";
import { Badge } from "@/components/ui";
import { describeConsensus, describeRegime, describeStance } from "@/lib/aiTrade";
import type { AiTradeRoundTrace, AiTradeSessionRow } from "@/lib/aiTradeTypes";
import { fmtMoney, fmtNum } from "@/lib/format";
import AiTradeTraceRound from "./AiTradeTraceRound";

// 距底阈值（px）：镜像 AgentMessageList 的贴底跟随 pin 逻辑。
const PIN_THRESHOLD = 48;

/** 会话概览条：标的·周期、状态、体制、累计盈亏、持仓/预算/轮次/共识、降级提示。 */
function TraceOverview({ session }: { session: AiTradeSessionRow }) {
  const pnl = session.sessionPnlUsd ?? 0;
  const stopped = session.state === "stopped";
  const regime = describeRegime(session.lastDecision?.marketContext);
  const consensus = describeConsensus(session.lastDecision?.consensus ?? null);
  const roundsToSummary = Math.max(
    0,
    (session.contextRefreshRounds ?? 0) - (session.roundsSinceSummary ?? 0),
  );
  return (
    <div className="flex flex-col gap-1.5 border-b border-[var(--border)] px-3 py-2">
      <div className="flex items-center justify-between gap-2">
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="text-xs font-semibold text-[var(--text)]">
            {session.instId} · {session.bar}
          </span>
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
      <div className="flex flex-wrap items-center gap-x-3 gap-y-0.5 text-[11px] text-[var(--muted)]">
        <span>持仓 {describeStance(session.positionSide)}</span>
        <span>
          预算 {fmtNum(session.budgetUsd)} · {session.leverage}×
        </span>
        <span>
          第 {session.roundsCompleted} 轮 · 距下次摘要 {roundsToSummary} 轮
        </span>
        {consensus && <span>{consensus}</span>}
      </div>
      {session.degraded && (
        <p className="rounded border border-[var(--border)] bg-[var(--surface-2)] px-2 py-1 text-[10px] text-[var(--short)]">
          决策降级中（LLM 超时 / 未产出有效决策 → 本轮回落观望，资金安全优先）
        </p>
      )}
    </div>
  );
}

/** 空态：会话运行中但尚无轨迹（等待首个确认 K 线）/ 已停止无轨迹。 */
function TraceEmpty({ stopped }: { stopped: boolean }) {
  return (
    <div className="flex flex-col items-center gap-1.5 px-4 py-10 text-center text-[11px] text-[var(--muted)]">
      <span className="text-lg">🤖</span>
      {stopped ? (
        <span>会话已停止，本次运行没有产生决策轨迹。</span>
      ) : (
        <span>AI 正在等待首个确认 K 线，随后每根 K 线一次决策，轨迹将实时呈现在此。</span>
      )}
    </div>
  );
}

/**
 * AI 自动交易「决策执行轨迹」视图（ai-auto-trading-agent-trace §3.7）：右侧 AI 助手面板的分段视图之一。
 * 概览条 + 轮次时间线（贴底跟随 + aria-live 播报流式增量 + 「↓ 回到最新」，尊重用户上滚）。
 */
export default function AiTradeAgentTrace({
  session,
  traces,
}: {
  session: AiTradeSessionRow | null;
  traces: AiTradeRoundTrace[];
}) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const endRef = useRef<HTMLDivElement | null>(null);
  const [pinned, setPinned] = useState(true);

  // 流式增量到达即变化（累积文本长度 / 状态 / 工具卡到位）。
  const signature = traces
    .map((t) => `${t.round}:${t.thinking.length}:${t.analysis.length}:${t.state}:${t.toolCall ? 1 : 0}`)
    .join("|");

  useEffect(() => {
    if (pinned) endRef.current?.scrollIntoView({ block: "end" });
  }, [signature, pinned]);

  if (!session) return null;
  const stopped = session.state === "stopped";

  const onScroll = () => {
    const el = containerRef.current;
    if (!el) return;
    setPinned(el.scrollHeight - el.scrollTop - el.clientHeight < PIN_THRESHOLD);
  };

  const jumpToLatest = () => {
    setPinned(true);
    endRef.current?.scrollIntoView({ block: "end", behavior: "smooth" });
  };

  return (
    <div className="relative flex h-full min-h-0 flex-col">
      <TraceOverview session={session} />
      <div ref={containerRef} onScroll={onScroll} className="min-h-0 flex-1 overflow-y-auto">
        <div
          role="log"
          aria-live="polite"
          aria-relevant="additions text"
          className="flex flex-col gap-2.5 px-3 py-3"
        >
          {traces.length === 0 ? (
            <TraceEmpty stopped={stopped} />
          ) : (
            traces.map((t) => <AiTradeTraceRound key={`${t.round}-${t.ts}`} trace={t} />)
          )}
          <div ref={endRef} />
        </div>
      </div>
      {!pinned && (
        <button
          type="button"
          onClick={jumpToLatest}
          aria-label="回到最新"
          className="absolute bottom-3 left-1/2 z-10 -translate-x-1/2 rounded-full border border-[var(--border)] bg-[var(--surface)] px-3 py-1 text-[11px] text-[var(--text)] shadow-soft transition-colors duration-150 ease-smooth hover:bg-[var(--surface-2)]"
        >
          ↓ 回到最新
        </button>
      )}
    </div>
  );
}
