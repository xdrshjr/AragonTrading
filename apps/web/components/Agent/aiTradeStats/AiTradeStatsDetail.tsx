"use client";

import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { PeriodReturnBars, UnderwaterChart } from "@/components/Chart/aiBacktest/charts";
import AutoTradeRoundTripRow from "@/components/Chart/AutoTradeRoundTripRow";
import AnnualizedProjectionCard from "./AnnualizedProjectionCard";
import ExitSourceCard from "./ExitSourceCard";
import HorizonAcceptanceCard from "./HorizonAcceptanceCard";
import WakeAttributionCard from "./WakeAttributionCard";
import { Badge, Button, Skeleton } from "@/components/ui";
import { buildLedger, buildRoundTrips, type RoundTrip } from "@/lib/autoTradePnl";
import { formatProfitCapture, PROFIT_CAPTURE_HINT } from "@/lib/aiTradeCapture";
import {
  COST_BASIS_HINT,
  costShareOfGross,
  describeCostBasis,
  describeFeeRates,
  describeIdentityCheck,
  describeReconcile,
} from "@/lib/aiTradeCost";
import {
  horizonEstimatedNote,
  horizonKpis,
  horizonReleaseHint,
  horizonVetoLine,
  turnoverVetoLine,
} from "@/lib/aiTradeHorizon";
import {
  type AiTradeRealizedStats,
  realizedCoverageText,
  realizedRows,
} from "@/lib/aiTradeProfitability";
import { aiTradeHistoryApi } from "@/lib/aiTradeHistoryApi";
import type { AiTradeHistoryDetailOut, AiTradeHistoryMember } from "@/lib/aiTradeHistoryTypes";
import type { AiTradeHorizonStats } from "@/lib/aiTradeTypes";
import { type AiObservability, aggregateAiObservability } from "@/lib/aiTradeObservability";
import { colorForReturn, fmtNum, fmtPct, fmtTime, signed } from "@/lib/format";
import type { EquityPoint } from "@/lib/types";

const ACTION_LABELS: Record<string, string> = {
  open_long: "开多",
  open_short: "开空",
  close: "平仓",
  hold: "观望",
};

// AI 自动交易统计「历史·单会话详情」（ai-trade-stats-dashboard §3.10.3）：KPI 概览 + 权益 / 回撤图
// （equityDigest 合并口径 P1-5；缺失优雅降级）+ 逐成员完整交易往返 + AI 决策概况 + 会话总结。
export default function AiTradeStatsDetail({ groupId, onBack }: { groupId: string; onBack: () => void }) {
  const query = useQuery({
    queryKey: ["ai-trade-history-detail", groupId],
    queryFn: () => aiTradeHistoryApi.detail(groupId),
  });

  if (query.isLoading) {
    return (
      <div className="flex flex-col gap-3">
        <BackButton onBack={onBack} />
        <Skeleton className="h-24 w-full rounded-[12px]" />
        <Skeleton className="h-40 w-full rounded-[12px]" />
      </div>
    );
  }
  if (query.isError || !query.data) {
    return (
      <div className="flex flex-col gap-3">
        <BackButton onBack={onBack} />
        <p role="alert" className="rounded-md border border-[var(--short)] bg-[var(--surface-2)] px-3 py-6 text-center text-xs text-[var(--short)]">
          加载会话详情失败，请稍后重试。
        </p>
      </div>
    );
  }

  return <DetailBody detail={query.data} onBack={onBack} />;
}

function DetailBody({ detail, onBack }: { detail: AiTradeHistoryDetailOut; onBack: () => void }) {
  const reconciled = detail.members.some((m) => m.metrics.reconciled === true);
  const combinedEquity = useMemo(() => mergeMemberEquity(detail.members), [detail.members]);
  const summary = detail.summary;
  const finalSummary = detail.members.map((m) => m.metrics.finalSummary).find((s) => s) ?? null;
  const aiStats = detail.members.map((m) => m.metrics.aiStats).find((s) => s) ?? null;
  // O4（hold-inertia-observability）：观测行是**全成员合计**（与上方旧五键卡的「首成员」口径并存，
  // pre-existing 口径本轮不动）；老记录全缺新键 → null → 整行隐藏（不显示一排误导性的 0）。
  const observability = useMemo(() => aggregateAiObservability(detail.members), [detail.members]);
  // 轴 D 需要**组级**扁平往返，而本页原本只在成员子组件里各自 buildRoundTrips（组级拿不到 trips）。
  // `buildLedger` 已是「跨成员合并、逐成员配对、绝不跨 instId」的现成真源——不在子组件里各建一份
  // 再拼（那就是又一次「同一个量两处各算一次」）。
  const ledger = useMemo(
    () =>
      buildLedger(
        detail.members.map((m) => ({ instId: m.instId, trades: m.trades, leverage: m.leverage })),
      ),
    [detail.members],
  );

  // §3.6 双栏：左栏 = KPI + 权益/回撤 + AI 决策概况 + 预计年化；右栏 = 逐成员完整交易（可展开行内 K 线）。
  // 「当前」页与「历史·详情」页同构。窄屏（<md）上下堆叠；左右各自 `overflow-y-auto`（避免嵌套双滚动 P2-3）。
  return (
    <div className="flex min-h-0 flex-1 flex-col gap-3">
      <div className="flex items-center justify-between">
        <BackButton onBack={onBack} />
        {reconciled && <Badge tone="accent">已对账</Badge>}
      </div>

      <div className="flex min-h-0 flex-1 gap-4 max-md:flex-col">
        <aside className="flex w-[40%] shrink-0 flex-col gap-3 overflow-y-auto pr-1 max-md:w-full max-md:overflow-visible">
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1 rounded-[12px] border border-[var(--border)] bg-[var(--surface-2)] px-3.5 py-2.5 text-[11px] text-[var(--muted)]">
            <span className="tabular text-[var(--text)]">
              {fmtTime(detail.startedTs)} → {fmtTime(detail.endedTs)}
            </span>
            <span>· {detail.bar}</span>
            {detail.strategyName && <span>· {detail.strategyName}</span>}
            <span>· 投入预算 <span className="tabular text-[var(--text)]">{fmtNum(detail.totalBudgetUsd, 0)}</span> USDT</span>
            <span>· {detail.status === "error" ? "异常结束" : "已停止"}</span>
          </div>

          <KpiGrid
            summary={summary}
            costBasis={resolveCostBasis(detail.members)}
            costBasisAssumed={detail.costBasisAssumed === true}
          />

          <ReconcileCard members={detail.members} />

          <Section title="权益 / 回撤">
            {combinedEquity.length >= 2 ? (
              <div className="flex flex-col gap-3">
                <div className="h-24">
                  <PeriodReturnBars equity={combinedEquity} />
                </div>
                <div className="h-12">
                  <UnderwaterChart equity={combinedEquity} />
                </div>
              </div>
            ) : (
              <p className="px-2 py-6 text-center text-[11px] leading-relaxed text-[var(--muted)]">
                本次会话未落权益快照（崩溃 / 对账会话），仅按成交汇总上方 KPI 与右侧往返明细。
              </p>
            )}
          </Section>

          {aiStats && (
            <AiStatsCard aiStats={aiStats} observability={observability} finalSummary={finalSummary} />
          )}

          {/* 周期验收闭环（horizon-acceptance [P1-9]）：组级卡由**后端**合并各成员 run 的原始
              计数器后判定一次（比例类指标不可在前端平均）；任一成员缺 acceptanceInput ⇒ 整键
              缺席 ⇒ 不渲染。两卡同样是两个独立入参（[P0-4]）。 */}
          <HorizonAcceptanceCard acceptance={summary.horizonAcceptance} />
          <ExitSourceCard exits={summary.exits} />

          <WakeAttributionCard trips={ledger} />

          {/* P2-5：年化口径 = 会话已实现收益 + 真实经历时长（endedTs-startedTs），与筛选无关。 */}
          <AnnualizedProjectionCard
            returnPct={summary.returnPct}
            elapsedSec={detail.endedTs - detail.startedTs}
            tradeCount={summary.tradeCount}
          />
        </aside>

        <section className="flex min-w-0 flex-1 flex-col gap-2 overflow-y-auto max-md:overflow-visible">
          <h3 className="text-[10px] font-medium uppercase tracking-wide text-[var(--muted)]">逐成员完整交易</h3>
          <div className="flex flex-col gap-3">
            {detail.members.map((m) => (
              <MemberTrades key={m.runId} member={m} bar={detail.bar} />
            ))}
          </div>
        </section>
      </div>
    </div>
  );
}

// 组级口径：成员一致即取该值；跨 run 出现两种 ⇒ undefined ⇒ 文案退回「口径未知」并由 costBasisMixed
// 徽标说明成因。**绝不硬编码 "net"**——老会话整组都是毛额，硬编码会让它谎报自己已扣过手续费。
function resolveCostBasis(members: AiTradeHistoryMember[]): "net" | "gross" | undefined {
  const seen = new Set(members.map((m) => m.costBasis).filter(Boolean));
  return seen.size === 1 ? ([...seen][0] as "net" | "gross") : undefined;
}

function KpiGrid({
  summary,
  costBasis,
  costBasisAssumed,
}: {
  summary: AiTradeHistoryDetailOut["summary"];
  costBasis: "net" | "gross" | undefined;
  costBasisAssumed: boolean;
}) {
  // 成本三键：老后端不下发 ⇒ 三者皆 undefined ⇒ 整段隐藏（绝不显示一排误导性的 0）。
  const hasCost = summary.feeUsd != null || summary.grossPnlUsd != null;
  const share = costShareOfGross(summary.feeUsd, summary.grossPnlUsd);
  return (
    <div className="flex flex-col gap-2">
      <div className="grid grid-cols-2 gap-2">
        <PrimaryKpi label="已实现总收益（USDT）" value={signed(summary.realizedPnlUsd)} color={colorForReturn(summary.realizedPnlUsd)} />
        <PrimaryKpi label="总收益率（基于投入预算）" value={fmtPct(summary.returnPct)} color={colorForReturn(summary.returnPct)} />
      </div>
      {/* P2-3：左栏偏窄，次级 KPI 收敛为两列（本 KpiGrid 为 Detail 局部组件，非共享，收敛不影响其它面板）。 */}
      <div className="grid grid-cols-2 gap-2">
        <Kpi label="完整交易" value={`${summary.tradeCount} 笔`} />
        <Kpi label="胜率" value={fmtPct(summary.winRate)} />
        <Kpi label="最大单笔盈" value={signed(summary.bestUsd)} color={colorForReturn(summary.bestUsd)} />
        <Kpi label="最大单笔亏" value={signed(summary.worstUsd)} color={colorForReturn(summary.worstUsd)} />
      </div>
      {hasCost && (
        <div className="flex flex-col gap-1" data-testid="ai-cost-kpis">
          <div className="grid grid-cols-2 gap-2">
            <Kpi label="已付手续费（USDT）" value={fmtNum(summary.feeUsd ?? 0)} />
            <Kpi label="毛额盈亏（未扣费）" value={signed(summary.grossPnlUsd ?? 0)} color={colorForReturn(summary.grossPnlUsd ?? 0)} />
            <Kpi label="成本占毛利比" value={share == null ? "—" : fmtPct(share)} />
            <Kpi label="盈亏口径" value={describeCostBasis(summary.costBasisMixed ? undefined : costBasis, costBasisAssumed)} />
          </div>
          <p className="text-[10px] leading-relaxed text-[var(--muted)]" title={COST_BASIS_HINT}>
            {summary.costBasisMixed
              ? "该会话跨 run 出现两种盈亏口径（净额 / 毛额），上方已实现总收益按各 run 各自口径还原为净额后相加。"
              : COST_BASIS_HINT}
          </p>
        </div>
      )}
      {/* F4 利润捕获率：**取后端 summary 的聚合值，不在前端重算历史**（两份实现必然漂移；实时页
          才用前端纯函数）。老会话 / 老库缺列时后端下发全 0 ⇒ 走同一条「样本不足」降级路径。 */}
      <div className="flex flex-col gap-1 rounded-md border border-[var(--border)] bg-[var(--surface)] px-3 py-2">
        <div className="flex items-center justify-between gap-2 text-xs">
          <span className="text-[var(--muted)]">利润捕获率</span>
          <span className="tabular font-semibold text-[var(--text)]" data-testid="ai-profit-capture">
            {formatProfitCapture({
              peakPnlUsd: summary.peakPnlUsd ?? 0,
              capturedPnlUsd: summary.capturedPnlUsd ?? 0,
              captureRatio: summary.captureRatio ?? 0,
              capturePairs: summary.capturePairs ?? 0,
              noPeakCount: summary.noPeakCount ?? 0,
            })}
          </span>
        </div>
        <p className="text-[10px] leading-relaxed text-[var(--muted)]">{PROFIT_CAPTURE_HINT}</p>
      </div>
    </div>
  );
}

/**
 * 交易所对账卡（cost-truth-and-net-pnl §3.5 F-3）。
 *
 * **老记录缺键 ⇒ 整段隐藏**：显示一张全 0 的对账卡等于宣称系统验证过一件它没验证过的事。
 * 卡片只呈现后端算好的差额与自检结论，前端不重算任何一项（C-5：本层只观测、不回写盈亏）。
 */
function ReconcileCard({ members }: { members: AiTradeHistoryMember[] }) {
  const rec = members.map((m) => m.metrics.aiStats?.reconcile).find((r) => r) ?? null;
  const rates = members.map((m) => m.metrics.aiStats?.feeRates).find((r) => r) ?? null;
  const view = describeReconcile(rec);
  if (!view) return null;
  const identity = describeIdentityCheck(rec);
  const feeLine = describeFeeRates(rates);
  return (
    <Section title="交易所对账">
      <div className="flex flex-col gap-1.5 rounded-md border border-[var(--border)] bg-[var(--surface)] px-3 py-2" data-testid="ai-reconcile-card">
        <div className="flex items-center justify-between gap-2 text-xs">
          <span className="text-[var(--muted)]">本地净额 vs 交易所结算</span>
          <Badge tone={view.tone === "ok" ? "long" : view.tone === "warn" ? "short" : "neutral"}>{view.label}</Badge>
        </div>
        <p className="text-[10px] leading-relaxed text-[var(--muted)]">{view.detail}</p>
        <div className="grid grid-cols-2 gap-2">
          <Kpi label="交易所手续费" value={fmtNum(rec?.exchangeFeeUsd ?? 0)} />
          <Kpi label="交易所资金费" value={fmtNum(rec?.exchangeFundingFeeUsd ?? 0)} />
          <Kpi label="交易所样本数" value={`${rec?.samples ?? 0} 笔`} />
          <Kpi label="本地往返数" value={`${rec?.localTrips ?? 0} 笔`} />
        </div>
        {identity && <p className="text-[10px] text-[var(--muted)]">{identity}</p>}
        {feeLine && <p className="text-[10px] text-[var(--muted)]">{feeLine}</p>}
      </div>
    </Section>
  );
}

/**
 * 「持仓周期」三格 KPI + 拦截计数行（ai-trade-holding-horizon-modes §2.11 展示落点之三）。
 *
 * 未装配本层的历史记录 ⇒ `horizon` 键缺席 ⇒ 整段不渲染（绝不显示一排误导性的 0，与本页
 * 「老记录缺键即整段隐藏」的既有纪律同款）。零样本时三格呈现「—」（[P2-3]）。
 */
function HorizonKpiGroup({ stats }: { stats: AiTradeHorizonStats | null }) {
  if (!stats) return null;
  const note = horizonEstimatedNote(stats);
  const turnoverLine = turnoverVetoLine(stats);
  return (
    <div className="mt-2" data-testid="ai-horizon-kpis">
      <div className="grid grid-cols-3 gap-2">
        {horizonKpis(stats).map((kpi) => (
          <Kpi key={kpi.label} label={kpi.label} value={kpi.value} />
        ))}
      </div>
      <p
        className="mt-1 text-[10px] leading-relaxed text-[var(--muted)]"
        title={horizonReleaseHint(stats)}
      >
        {stats.title}（{stats.profileId}）· {horizonVetoLine(stats)}
        {note ? ` · ${note}` : ""}
      </p>
      {/* 开仓侧换手纪律的三个计数（turnover-budget §2.4.4）；老记录缺键 ⇒ 整行不渲染。
          历史页**刻意不渲染兜底态**：`backstop` 是实时态、不落库（D-6），一个 3 天前的
          「已核实」在这里只会是个骗人的绿勾。 */}
      {turnoverLine ? (
        <p className="mt-0.5 text-[10px] leading-relaxed text-[var(--muted)]">
          {turnoverLine}
        </p>
      ) : null}
    </div>
  );
}

function AiStatsCard({
  aiStats,
  observability,
  finalSummary,
}: {
  aiStats: NonNullable<AiTradeHistoryMember["metrics"]["aiStats"]>;
  observability: AiObservability | null;
  finalSummary: string | null;
}) {
  const [open, setOpen] = useState(false);
  const counts = aiStats.actionCounts ?? {};
  return (
    <Section title="AI 决策概况">
      {/* E-3（realized-edge-calibration D-8）：`decisionRounds` 是**全程**真实轮数，而 `holdRate`
          的分母是最近 50 轮的保留样本——两个分母并排渲染而不标口径，用户读到的就是错的。
          口径直接写进 label 而不是 `title` 提示：悬停才可见的提示等于没显示（触屏与用例都读不到）。 */}
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        <Kpi label="决策轮数（全程）" value={`${aiStats.decisionRounds}`} />
        <Kpi label="观望占比（近 50 轮）" value={fmtPct(aiStats.holdRate)} />
        <Kpi label="平均置信度（近 50 轮）" value={fmtPct(aiStats.avgConfidence)} />
        {/* `degradedRounds` 与 `holdRate` / `avgConfidence` 同源——都在 `_compute_ai_stats` 的
            ≤50 条决策样本上求（`session.py` 的 `_DECISIONS_MAX`），**不是**全程计数。 */}
        <Kpi label="共识降级（近 50 轮）" value={`${aiStats.degradedRounds} 轮`} />
      </div>
      <p className="mt-1 text-[10px] leading-relaxed text-[var(--muted)]">
        「全程」为会话完整轮数；「近 50 轮」取自决策保留样本（环形缓冲），两者在长会话上必然不等。
      </p>
      <RealizedKpiGroup stats={aiStats.realized ?? null} />
      <HorizonKpiGroup stats={aiStats.horizon ?? null} />
      {observability && (
        <>
          <div className="mt-2 grid grid-cols-3 gap-2 sm:grid-cols-6">
            <Kpi label="反思轮数" value={`${observability.reflectionRuns}`} />
            <Kpi label="反思放行" value={`${observability.reflectionConfirms}`} />
            <Kpi label="真实否决" value={`${observability.reflectionAborts}`} />
            <Kpi label="故障保守" value={`${observability.reflectionFailClosed}`} />
            <Kpi label="唤醒抑制" value={`${observability.wakeSkips}`} />
            <Kpi label="最长连续观望" value={`${observability.maxHoldStreak} 轮`} />
          </div>
          <p className="mt-1 text-[10px] leading-relaxed text-[var(--muted)]">
            反思与否决为近 50 轮保留样本口径；故障保守 = 反思子调用基础设施失败后的安全跳过，不代表模型判断。
          </p>
        </>
      )}
      <div className="mt-2 flex flex-wrap gap-1.5">
        {Object.entries(counts)
          .filter(([, n]) => n > 0)
          .map(([action, n]) => (
            <Badge key={action} tone="neutral">
              {ACTION_LABELS[action] ?? action} {n}
            </Badge>
          ))}
      </div>
      {finalSummary && (
        <div className="mt-2">
          <button
            type="button"
            aria-expanded={open}
            onClick={() => setOpen((v) => !v)}
            className="text-[11px] font-medium text-[var(--accent)] hover:underline"
          >
            {open ? "▾ 收起会话总结" : "▸ 查看会话总结"}
          </button>
          {open && (
            <p className="mt-1.5 whitespace-pre-wrap rounded-md border border-[var(--border)] bg-[var(--surface)] px-3 py-2 text-[11px] leading-relaxed text-[var(--muted)]">
              {finalSummary}
            </p>
          )}
        </div>
      )}
    </Section>
  );
}

// P2-6：`bar` 为**组级**（同组成员共享一个周期，源自 detail.bar），逐成员传同值正确。
function MemberTrades({ member, bar }: { member: AiTradeHistoryMember; bar: string }) {
  const [openRowKey, setOpenRowKey] = useState<string | null>(null);
  const roundTrips = useMemo(
    // 该成员 run 的口径由后端下发（老 run 为推定值）；缺键 ⇒ "gross" ⇒ 与本特性合入前逐字相同。
    () =>
      buildRoundTrips(member.instId, member.trades, member.leverage, {
        costBasis: member.costBasis ?? "gross",
      }).sort((a, b) => b.closedTs - a.closedTs),
    [member],
  );
  return (
    <div className="overflow-hidden rounded-[10px] border border-[var(--border)]">
      <div className="flex items-center justify-between bg-[var(--surface-2)] px-3 py-1.5 text-[11px]">
        <span className="font-medium text-[var(--text)]">{member.instId}</span>
        <span className="tabular text-[var(--muted)]">
          {member.strategyNames.join(" / ") || "—"} · 预算 {fmtNum(member.budgetUsd, 0)} · {member.allocationPct}%
        </span>
      </div>
      {roundTrips.length === 0 ? (
        <p className="px-3 py-4 text-center text-[11px] text-[var(--muted)]">该标的暂无完整交易往返。</p>
      ) : (
        <div className="bg-[var(--surface)]">
          {roundTrips.map((rt) => {
            const key = rowKey(rt);
            return (
              <AutoTradeRoundTripRow
                key={key}
                rt={rt}
                open={openRowKey === key}
                onToggle={() => setOpenRowKey((prev) => (prev === key ? null : key))}
                bar={bar}
                showR
              />
            );
          })}
        </div>
      )}
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="flex flex-col gap-2">
      <h3 className="text-[10px] font-medium uppercase tracking-wide text-[var(--muted)]">{title}</h3>
      {children}
    </section>
  );
}

function BackButton({ onBack }: { onBack: () => void }) {
  return (
    <Button variant="ghost" size="sm" onClick={onBack} aria-label="返回历史列表">
      ← 返回列表
    </Button>
  );
}

function PrimaryKpi({ label, value, color }: { label: string; value: string; color: string }) {
  return (
    <div className="flex flex-col gap-1 rounded-lg border border-[var(--border)] bg-[var(--surface)] px-3 py-2.5">
      <span className="text-[10px] uppercase tracking-wide text-[var(--muted)]">{label}</span>
      <span className="tabular text-xl font-semibold" style={{ color }}>
        {value}
      </span>
    </div>
  );
}

/**
 * 会话级「已实现」三项（realized-edge-calibration [P1-2] 的渲染落点之一）。
 *
 * 它与实时侧的同名读数**共用 `lib/aiTradeProfitability` 的同一套格式化器**——绝不在两处各写一份
 * 口径。样本 < 3 笔 ⇒ 整段不渲染（不显示一个 1 笔样本的中位数）。
 */
function RealizedKpiGroup({ stats }: { stats: AiTradeRealizedStats | null }) {
  const rows = realizedRows(stats);
  if (!rows || !stats) return null;
  return (
    <div className="mt-2 flex flex-col gap-1" data-testid="ai-realized-history">
      <div className="grid grid-cols-3 gap-2">
        {rows.map((row) => (
          <Kpi key={row.label} label={row.label} value={row.value} />
        ))}
      </div>
      <p className="text-[10px] leading-relaxed text-[var(--muted)]">
        本会话已实现 · {realizedCoverageText(stats)}
      </p>
    </div>
  );
}

function Kpi({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <div className="flex flex-col gap-0.5 rounded-md bg-[var(--surface-2)] px-2.5 py-1.5">
      <span className="text-[10px] text-[var(--muted)]">{label}</span>
      <span className="tabular text-sm font-medium" style={{ color: color ?? "var(--text)" }}>
        {value}
      </span>
    </div>
  );
}

// P1-5：多成员权益合并口径——并集时间轴 + 每成员「前值填充」求和（首样本前用该成员预算基线，全期一致口径），
// 组合回撤在**求和曲线上重算**（running peak，Min(equity/peak−1,0)），绝不朴素拼接、绝不相加各成员 drawdown。
// 仅纳入有 equityDigest 的成员；全部缺失 → 返回 []（详情页据此降级为「不画图」）。纯函数、可单测。
export function mergeMemberEquity(members: AiTradeHistoryMember[]): EquityPoint[] {
  const contributors = members
    .map((m) => ({ budget: m.budgetUsd, samples: [...(m.metrics.equityDigest ?? [])].sort((a, b) => a.ts - b.ts) }))
    .filter((c) => c.samples.length > 0);
  if (contributors.length === 0) return [];
  const tsSet = new Set<number>();
  for (const c of contributors) for (const s of c.samples) tsSet.add(s.ts);
  const timeline = Array.from(tsSet).sort((a, b) => a - b);
  let peak = -Infinity;
  return timeline.map((ts) => {
    let equity = 0;
    for (const c of contributors) equity += forwardFill(c.samples, ts, c.budget);
    peak = Math.max(peak, equity);
    const drawdown = peak > 0 ? Math.min(equity / peak - 1, 0) : 0;
    return { ts, equity, drawdown };
  });
}

// 前值填充：取 ≤ts 的最后一个 equity；成员首样本之前用其预算基线（保持组合曲线在 t0 ≈ Σ 预算，无锯齿）。
function forwardFill(samples: { ts: number; equity: number }[], ts: number, baseline: number): number {
  let value = baseline;
  for (const s of samples) {
    if (s.ts <= ts) value = s.equity;
    else break;
  }
  return value;
}

function rowKey(rt: RoundTrip): string {
  return `${rt.instId}-${rt.entry.id ?? rt.openedTs}-${rt.exit.id ?? rt.closedTs}`;
}
