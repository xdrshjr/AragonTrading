"use client";

import dynamic from "next/dynamic";
import { useState } from "react";
import { Badge, Button, Modal, Tabs } from "@/components/ui";
import MetricsCards from "@/components/Strategy/MetricsCards";
import TradesTable from "@/components/Strategy/TradesTable";
import {
  AcceptanceBacktestSection,
  BenchmarkCompareCard,
  ExtendedMetricsGrid,
  KPIHero,
  ProfitGuardBacktestCard,
} from "./aiBacktest/panels";
import { PeriodReturnBars, UnderwaterChart } from "./aiBacktest/charts";
import {
  actionDistribution,
  describeBacktestPhase,
  describeBacktestState,
  progressPercent,
} from "@/lib/aiBacktest";
import { aiBacktestApi } from "@/lib/aiBacktestApi";
import { maxDrawdownWindow } from "@/lib/backtestStats";
import {
  describeAiAction,
  describeConsensus,
  describeExecuted,
  describeRegime,
} from "@/lib/aiTrade";
import type {
  AiBacktestBenchmark,
  AiBacktestExtendedMetrics,
  AiBacktestStatus,
  AiBacktestSummary,
} from "@/lib/aiBacktestTypes";
import type { AiTradeDecision } from "@/lib/aiTradeTypes";
import { downloadCsv, downloadJson, equityToCsv, resultToJson, tradesToCsv } from "@/lib/export";
import { fmtMoney, fmtNum, fmtPct, fmtTime } from "@/lib/format";
import { useAppStore } from "@/lib/store";
import { useAiBacktestJob } from "@/lib/useAiBacktestJob";

const EquityChart = dynamic(() => import("./EquityChart"), { ssr: false });

/**
 * AI 自动交易回测结果面板（ai-auto-trading-backtest §2.10）：活跃 job 时挂载。
 * 运行态：进度头（进度条 / 当前根·时间 / 滚动权益迷你图 / 阶段）+ 逐轮决策日志流 + 上下文摘要 + 取消。
 * 完成态：指标卡 + AI 统计卡 + 权益曲线 + 成交表 + AI 决策时间线 / 动作分布 + 上下文摘要时间线 + 复看。
 */
export default function AiBacktestModal({ jobId, onClose }: { jobId: string; onClose: () => void }) {
  const theme = useAppStore((s) => s.theme);
  const view = useAiBacktestJob(jobId);
  if (!view) return null;

  const { status, phase, progress, decisions, summaries, result, state, connected, error } = view;
  const terminal = state !== "running";
  const running = !terminal;
  const pct = progressPercent(progress?.pct ?? status?.progress ?? (result ? 1 : 0));
  const barLabel =
    progress != null
      ? `第 ${progress.barIndex}/${progress.totalBars} 根 · ${fmtTime(progress.ts)}`
      : status != null && status.totalBars > 0
        ? `第 ${status.barIndex}/${status.totalBars} 根`
        : "准备中…";

  return (
    <Modal open onClose={onClose} labelledBy="ai-backtest-title" className="max-w-4xl">
      <div className="flex items-center justify-between border-b border-[var(--border)] px-5 py-3.5">
        <div className="flex items-center gap-2">
          <h2 id="ai-backtest-title" className="text-sm font-semibold text-[var(--text)]">
            AI 自动交易 · 回测
          </h2>
          <Badge tone="accent">模拟回测</Badge>
          <Badge tone={state === "completed" ? "long" : state === "failed" ? "short" : "neutral"}>
            {describeBacktestState(state)}
          </Badge>
          {status && (
            <span className="text-[11px] text-[var(--muted)]">
              {status.instId} · {status.bar}
            </span>
          )}
        </div>
        <button
          type="button"
          onClick={onClose}
          aria-label="关闭回测面板"
          className="text-[var(--muted)] hover:text-[var(--text)]"
        >
          ✕
        </button>
      </div>

      <div className="flex max-h-[74vh] flex-col gap-4 overflow-auto px-5 py-4">
        {/* 进度头（运行态与完成态都显示，完成态定格 100%）。 */}
        <ProgressHead
          pct={pct}
          phase={running ? describeBacktestPhase(phase) : "已完成"}
          barLabel={barLabel}
          progress={progress}
          equity={view.equity}
          running={running}
          connected={connected}
        />

        {error && (
          <p role="alert" className="rounded-md border border-[var(--short)]/40 bg-[var(--short)]/10 px-3 py-2 text-[11px] font-medium text-[var(--short-ink)]">
            回测失败（{error.code}）：{error.message}
          </p>
        )}

        {running ? (
          <RunningView decisions={decisions} summary={summaries.at(-1)?.summary ?? null} jobId={jobId} />
        ) : (
          result && <CompleteView result={result} theme={theme} decisions={decisions} status={status} />
        )}
      </div>

      <div className="flex items-center justify-between gap-2 border-t border-[var(--border)] px-5 py-3">
        <span className="text-[11px] text-[var(--muted)]">
          {result?.runId != null ? `运行历史 ID：${result.runId}（可在运行历史中复看）` : ""}
        </span>
        <Button variant="ghost" size="sm" onClick={onClose}>
          {terminal ? "关闭" : "后台运行并关闭"}
        </Button>
      </div>
    </Modal>
  );
}

function ProgressHead({
  pct,
  phase,
  barLabel,
  progress,
  equity,
  running,
  connected,
}: {
  pct: number;
  phase: string;
  barLabel: string;
  progress: { equity: number; sessionPnlUsd: number; positionSide: string } | null;
  equity: { ts: number; equity: number }[];
  running: boolean;
  connected: boolean;
}) {
  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-center justify-between text-[11px] text-[var(--muted)]">
        <span className="flex items-center gap-2">
          <span className="font-medium text-[var(--text)]">{phase}</span>
          <span>{barLabel}</span>
          {running && !connected && <span className="text-[var(--accent-ink)]">· 重连中…</span>}
        </span>
        <span className="tabular">{pct}%</span>
      </div>
      <div className="h-1.5 overflow-hidden rounded-full bg-[var(--surface-2)]">
        <div
          className="h-full rounded-full bg-[var(--accent)] transition-all duration-300"
          style={{ width: `${pct}%` }}
        />
      </div>
      {progress && (
        <div className="flex items-center justify-between gap-3">
          <Sparkline points={equity} />
          <div className="flex shrink-0 items-center gap-3 text-[11px] text-[var(--muted)]">
            <span>
              权益 <span className="tabular font-medium text-[var(--text)]">{fmtMoney(progress.equity)}</span>
            </span>
            <span
              className={progress.sessionPnlUsd >= 0 ? "text-[var(--long-ink)]" : "text-[var(--short-ink)]"}
            >
              {progress.sessionPnlUsd >= 0 ? "+" : ""}
              {fmtMoney(progress.sessionPnlUsd)}
            </span>
          </div>
        </div>
      )}
    </div>
  );
}

/** 轻量内联 SVG 滚动权益迷你图（无外部依赖，运行态进度头用）。 */
function Sparkline({ points }: { points: { ts: number; equity: number }[] }) {
  if (points.length < 2) return <div className="h-8 flex-1" />;
  const values = points.map((p) => p.equity);
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;
  const w = 100;
  const h = 28;
  const path = values
    .map((v, i) => {
      const x = (i / (values.length - 1)) * w;
      const y = h - ((v - min) / span) * h;
      return `${i === 0 ? "M" : "L"}${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");
  const up = values.at(-1)! >= values[0];
  return (
    <svg viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none" className="h-8 flex-1">
      <path d={path} fill="none" stroke={up ? "var(--long)" : "var(--short)"} strokeWidth={1.5} vectorEffect="non-scaling-stroke" />
    </svg>
  );
}

function RunningView({
  decisions,
  summary,
  jobId,
}: {
  decisions: AiTradeDecision[];
  summary: string | null;
  jobId: string;
}) {
  const recent = [...decisions].reverse();
  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center justify-between">
        <span className="text-[11px] font-semibold uppercase tracking-wide text-[var(--muted)]">
          决策日志（{decisions.length} 轮，新在上）
        </span>
        <Button variant="ghost" size="sm" onClick={() => aiBacktestApi.cancel(jobId).catch(() => {})}>
          取消回测
        </Button>
      </div>
      <div className="flex max-h-[38vh] flex-col gap-1.5 overflow-auto">
        {recent.length === 0 && (
          <p className="text-[11px] text-[var(--muted)]">AI 尚未产出决策，等待暖机 / 首个决策根…</p>
        )}
        {recent.map((d, i) => (
          <DecisionCard key={`${d.round}-${d.ts}-${i}`} d={d} />
        ))}
      </div>
      {summary && (
        <details className="text-[11px] text-[var(--muted)]" open>
          <summary className="cursor-pointer select-none text-[var(--text)]">最新上下文摘要</summary>
          <p className="mt-1 whitespace-pre-wrap leading-relaxed">{summary}</p>
        </details>
      )}
    </div>
  );
}

function DecisionCard({ d }: { d: AiTradeDecision }) {
  const exec = describeExecuted(
    d.action,
    { traded: Boolean((d.executed as { submitted?: boolean } | null)?.submitted) },
    d.degradeReason,
  );
  const regime = describeRegime(d.marketContext);
  const consensus = describeConsensus(d.consensus);
  return (
    <div className="rounded border border-[var(--border)] bg-[var(--bg)] px-2.5 py-1.5 text-[11px]">
      <div className="flex items-center justify-between gap-2">
        <span className="flex items-center gap-1.5">
          <span className="tabular text-[var(--muted)]">#{d.round}</span>
          <span className="font-medium text-[var(--text)]">{describeAiAction(d.action)}</span>
          <span className="inline-flex items-center gap-1" style={{ color: execColor(exec.tone) }}>
            <span className="inline-block h-1.5 w-1.5 rounded-full" style={{ backgroundColor: execColor(exec.tone) }} />
            {exec.label}
          </span>
        </span>
        <span className="tabular text-[var(--muted)]">
          {d.confidence != null ? `置信 ${fmtNum(d.confidence * 100, 0)}%` : ""}
        </span>
      </div>
      <p className="mt-0.5 leading-relaxed text-[var(--muted)]">{d.reason}</p>
      {(regime || consensus) && (
        <div className="mt-1 flex flex-wrap gap-1.5 text-[10px] text-[var(--muted)]">
          {regime && <span className="rounded bg-[var(--surface-2)] px-1.5 py-0.5">{regime}</span>}
          {consensus && <span className="rounded bg-[var(--surface-2)] px-1.5 py-0.5">{consensus}</span>}
        </div>
      )}
    </div>
  );
}

type BacktestResult = NonNullable<ReturnType<typeof useAiBacktestJob>>["result"];

const COMPLETE_TABS = [
  { key: "overview", label: "概览" },
  { key: "advanced", label: "进阶指标" },
  { key: "trades", label: "成交" },
  { key: "ai", label: "AI 决策" },
  { key: "context", label: "上下文" },
];

// 缺省/空 dict（旧版落库结果或派生失败回落 {}）→ undefined，供各分区优雅隐藏而非报错。
function present<T>(value: T | undefined): T | undefined {
  return value && Object.keys(value).length > 0 ? value : undefined;
}

function CompleteView({
  result,
  theme,
  decisions,
  status,
}: {
  result: BacktestResult;
  theme: "light" | "dark";
  decisions: AiTradeDecision[];
  status: AiBacktestStatus | null;
}) {
  const [tab, setTab] = useState("overview");
  if (!result) return null;
  const timeline = result.decisionsDigest.length > 0 ? result.decisionsDigest : decisions;
  const ext = present<AiBacktestExtendedMetrics>(result.extendedMetrics);
  const benchmark = present<AiBacktestBenchmark>(result.benchmark);
  const fileBase = `ai-backtest-${status?.instId ?? "run"}-${status?.bar ?? ""}-${result.startTs}-${result.endTs}`;
  return (
    <div className="flex flex-col gap-3">
      <Tabs tabs={COMPLETE_TABS} active={tab} onChange={setTab} variant="underline" />
      {tab === "overview" && <OverviewTab result={result} theme={theme} benchmark={benchmark} />}
      {tab === "advanced" && <AdvancedTab result={result} ext={ext} benchmark={benchmark} />}
      {tab === "trades" && <TradesTab result={result} fileBase={fileBase} />}
      {tab === "ai" && <AiDecisionTab result={result} timeline={timeline} />}
      {tab === "context" && <SummaryTimeline summaries={result.summaries} />}
    </div>
  );
}

function OverviewTab({
  result,
  theme,
  benchmark,
}: {
  result: NonNullable<BacktestResult>;
  theme: "light" | "dark";
  benchmark?: AiBacktestBenchmark;
}) {
  const ddWin = maxDrawdownWindow(result.equity);
  return (
    <div className="flex flex-col gap-4">
      <KPIHero metrics={result.metrics} benchmark={benchmark} />
      {result.notes.length > 0 && (
        <div className="rounded-md border border-[var(--border)] bg-[var(--surface-2)] px-3 py-2 text-[11px] text-[var(--muted)]">
          {result.notes.map((n, i) => (
            <p key={i}>· {n}</p>
          ))}
        </div>
      )}
      <section className="flex flex-col gap-1">
        <span className="text-[11px] font-semibold uppercase tracking-wide text-[var(--muted)]">
          权益曲线{benchmark ? "（含买入持有基准）" : ""}
        </span>
        <div className="h-56 rounded-md border border-[var(--border)]">
          <EquityChart equity={result.equity} benchmark={benchmark?.equity} theme={theme} emptyHint="无权益数据" />
        </div>
      </section>
      <section className="flex flex-col gap-1">
        <div className="flex items-center justify-between">
          <span className="text-[11px] font-semibold uppercase tracking-wide text-[var(--muted)]">水下回撤</span>
          {ddWin && (
            <span className="tabular text-[10px] text-[var(--muted)]">
              最深 {fmtPct(ddWin.depth)} · {fmtTime(ddWin.startTs, false)}→{fmtTime(ddWin.endTs, false)}
            </span>
          )}
        </div>
        <div className="h-20 rounded-md border border-[var(--border)] bg-[var(--surface-2)] p-1">
          <UnderwaterChart equity={result.equity} />
        </div>
      </section>
    </div>
  );
}

function AdvancedTab({
  result,
  ext,
  benchmark,
}: {
  result: NonNullable<BacktestResult>;
  ext?: AiBacktestExtendedMetrics;
  benchmark?: AiBacktestBenchmark;
}) {
  return (
    <div className="flex flex-col gap-4">
      <section className="flex flex-col gap-1">
        <span className="text-[11px] font-semibold uppercase tracking-wide text-[var(--muted)]">核心指标</span>
        <MetricsCards metrics={result.metrics} />
      </section>
      {ext && (
        <section className="flex flex-col gap-1">
          <span className="text-[11px] font-semibold uppercase tracking-wide text-[var(--muted)]">进阶风险 / 质量指标</span>
          <ExtendedMetricsGrid ext={ext} />
        </section>
      )}
      {benchmark && <BenchmarkCompareCard metrics={result.metrics} benchmark={benchmark} />}
      <section className="flex flex-col gap-1">
        <span className="text-[11px] font-semibold uppercase tracking-wide text-[var(--muted)]">周期收益分布</span>
        <PeriodReturnBars equity={result.equity} />
      </section>
    </div>
  );
}

function TradesTab({ result, fileBase }: { result: NonNullable<BacktestResult>; fileBase: string }) {
  const hasTrades = result.trades.length > 0;
  const hasEquity = result.equity.length > 0;
  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-2">
        <Button variant="ghost" size="sm" disabled={!hasTrades}
          onClick={() => downloadCsv(`${fileBase}-trades.csv`, tradesToCsv(result.trades))}>
          导出成交 CSV
        </Button>
        <Button variant="ghost" size="sm" disabled={!hasEquity}
          onClick={() => downloadCsv(`${fileBase}-equity.csv`, equityToCsv(result.equity))}>
          导出净值 CSV
        </Button>
        <Button variant="ghost" size="sm"
          onClick={() => downloadJson(`${fileBase}.json`, resultToJson(result))}>
          导出完整 JSON
        </Button>
      </div>
      <TradesTable trades={result.trades} />
    </div>
  );
}

function AiDecisionTab({ result, timeline }: { result: NonNullable<BacktestResult>; timeline: AiTradeDecision[] }) {
  return (
    <div className="flex flex-col gap-4">
      <AiStatsCard result={result} />
      <ProfitGuardBacktestCard
        enabled={result.aiStats.profitGuardEnabled}
        exits={result.aiStats.profitGuardExits}
      />
      <AcceptanceBacktestSection aiStats={result.aiStats} />
      <section className="flex flex-col gap-1">
        <span className="text-[11px] font-semibold uppercase tracking-wide text-[var(--muted)]">动作分布</span>
        <ActionBars counts={result.aiStats.actionCounts} />
      </section>
      <DecisionTimeline decisions={timeline} />
    </div>
  );
}

function AiStatsCard({ result }: { result: NonNullable<ReturnType<typeof useAiBacktestJob>>["result"] }) {
  if (!result) return null;
  const s = result.aiStats;
  const stats: { label: string; value: string }[] = [
    { label: "决策轮数", value: String(s.decisionRounds) },
    { label: "观望占比", value: `${fmtNum(s.holdRate * 100, 0)}%` },
    { label: "平均置信度", value: `${fmtNum(s.avgConfidence * 100, 0)}%` },
    { label: "共识降级", value: String(s.degradedRounds) },
    { label: "超时/失败", value: String(s.timeoutRounds) },
    { label: "上下文摘要", value: `${s.summaryCount} 次` },
  ];
  return (
    <div className="grid grid-cols-3 gap-2 sm:grid-cols-6">
      {stats.map((st) => (
        <div key={st.label} className="rounded-[10px] border border-[var(--border)] bg-[var(--surface-2)] px-3 py-2">
          <div className="text-[10px] text-[var(--muted)]">{st.label}</div>
          <div className="tabular text-sm font-semibold text-[var(--text)]">{st.value}</div>
        </div>
      ))}
    </div>
  );
}

function ActionBars({ counts }: { counts: Record<string, number> }) {
  const slices = actionDistribution(counts);
  const toneVar: Record<string, string> = {
    open_long: "var(--long)",
    open_short: "var(--short)",
    close: "var(--accent)",
    hold: "var(--muted)",
  };
  return (
    <div className="flex flex-col gap-1.5">
      {slices.map((s) => (
        <div key={s.action} className="flex items-center gap-2 text-[11px]">
          <span className="w-10 shrink-0 text-[var(--muted)]">{s.label}</span>
          <div className="h-2 flex-1 overflow-hidden rounded-full bg-[var(--surface-2)]">
            <div
              className="h-full rounded-full"
              style={{ width: `${Math.round(s.pct * 100)}%`, backgroundColor: toneVar[s.action] }}
            />
          </div>
          <span className="tabular w-16 shrink-0 text-right text-[var(--muted)]">
            {s.count} · {Math.round(s.pct * 100)}%
          </span>
        </div>
      ))}
    </div>
  );
}

function DecisionTimeline({ decisions }: { decisions: AiTradeDecision[] }) {
  if (decisions.length === 0) return null;
  return (
    <details className="text-[11px] text-[var(--muted)]">
      <summary className="cursor-pointer select-none text-[var(--text)]">
        AI 决策时间线（{decisions.length} 轮）
      </summary>
      <div className="mt-1 flex max-h-64 flex-col gap-1 overflow-auto">
        {decisions.map((d, i) => {
          const exec = describeExecuted(
            d.action,
            { traded: Boolean((d.executed as { submitted?: boolean } | null)?.submitted) },
            d.degradeReason,
          );
          return (
            <div key={`${d.round}-${d.ts}-${i}`} className="flex items-start gap-2">
              <span className="tabular shrink-0 text-[var(--muted)]">{fmtTime(d.ts, false)}</span>
              <span className="shrink-0 font-medium text-[var(--text)]">{describeAiAction(d.action)}</span>
              <span className="shrink-0" style={{ color: execColor(exec.tone) }}>{exec.label}</span>
              <span className="truncate">{d.reason}</span>
            </div>
          );
        })}
      </div>
    </details>
  );
}

function SummaryTimeline({ summaries }: { summaries: AiBacktestSummary[] }) {
  if (summaries.length === 0) return null;
  return (
    <details className="text-[11px] text-[var(--muted)]">
      <summary className="cursor-pointer select-none text-[var(--text)]">
        上下文摘要时间线（{summaries.length} 次 · AI 长期记忆演进）
      </summary>
      <div className="mt-1 flex flex-col gap-2">
        {summaries.map((s, i) => (
          <div key={`${s.round}-${i}`} className="rounded border border-[var(--border)] bg-[var(--bg)] px-2.5 py-1.5">
            <div className="tabular text-[10px] text-[var(--muted)]">第 {s.round} 轮 · {fmtTime(s.ts)}</div>
            <p className="mt-0.5 whitespace-pre-wrap leading-relaxed text-[var(--text)]">{s.summary}</p>
          </div>
        ))}
      </div>
    </details>
  );
}

function execColor(tone: "long" | "short" | "muted"): string {
  return tone === "long" ? "var(--long)" : tone === "short" ? "var(--short)" : "var(--muted)";
}
