"use client";

import { useQuery } from "@tanstack/react-query";
import dynamic from "next/dynamic";
import { useEffect, useState } from "react";
import MetricsCards from "@/components/Strategy/MetricsCards";
import TradesTable from "@/components/Strategy/TradesTable";
import { Button } from "@/components/ui";
import { ApiRequestError } from "@/lib/api";
import { fmtTime } from "@/lib/format";
import { useAppStore } from "@/lib/store";
import { codeLabel } from "@/lib/weightModel";
import { weightModelApi } from "@/lib/weightModelApi";
import type { WeightBacktestResult } from "@/lib/weightModelTypes";

// slim 蜡烛图动态引入（ssr:false，规避 lightweight-charts 的 SSR 问题，与 ChartWorkspace 引入 CandleChart 同范式）。
const WeightBacktestChart = dynamic(() => import("./WeightBacktestChart"), { ssr: false });

const STALE_MS = 5 * 60_000; // 本会话内切走再切回即时可见（回测不落库，靠 Query 缓存承接「记录」体验）
const GC_MS = 30 * 60_000;

/**
 * 「K 线回测」区块编排（嵌于详情「学习曲线回放」下方）：以 (modelId, runId) 为键的 TanStack Query 触发 /
 * 缓存一次性回测，走 idle / loading / error / success 四态。success 依次呈现统计信息卡片 + 带买卖 markers
 * 的蜡烛图（叠加净值曲线）+ 逐笔成交明细表。`autoRun`（来自卡片「回测」）挂载即发起、仅消费一次。
 */
export default function WeightBacktestPanel({
  modelId,
  runId,
  autoRun = false,
}: {
  modelId: number;
  runId: number;
  autoRun?: boolean;
}) {
  const theme = useAppStore((s) => s.theme);
  const [triggered, setTriggered] = useState(autoRun);

  // autoRun：列表→详情为全新挂载，故「挂载即发起」语义成立（一次性，不依赖 Panel 聚焦 effect）。
  useEffect(() => {
    if (autoRun) setTriggered(true);
  }, [autoRun]);

  const query = useQuery({
    queryKey: ["weight-models", modelId, "runs", runId, "backtest"],
    queryFn: () => weightModelApi.backtest(modelId, runId),
    enabled: triggered,
    staleTime: STALE_MS,
    gcTime: GC_MS,
    retry: false,
  });

  const result = query.data as WeightBacktestResult | undefined;
  const loading = triggered && query.isFetching && !result;
  const errored = triggered && query.isError && !query.isFetching;

  const runBacktest = () => {
    if (!triggered) {
      setTriggered(true);
      return;
    }
    void query.refetch(); // 「重新回测」：键稳定，强制重取。
  };

  return (
    <section className="card flex flex-col gap-3 p-3" aria-label="K 线回测">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-col">
          <span className="text-[11px] font-semibold text-[var(--text)]">K 线回测</span>
          <span className="text-[11px] text-[var(--muted)]" aria-live="polite">
            {result
              ? `回测区间 = 训练区间 ${fmtTime(result.meta.startTs)} – ${fmtTime(result.meta.endTs)}（样本内）`
              : "回测区间默认 = 该次训练区间（样本内），复用训练期同一因果打分"}
          </span>
        </div>
        <Button variant="default" size="sm" disabled={loading} onClick={runBacktest}>
          {loading ? "回测中…" : result || errored ? "重新回测" : "回测"}
        </Button>
      </div>

      {!triggered && (
        <p className="rounded-[10px] border border-[var(--border)] bg-[var(--surface-2)] px-3 py-2 text-[11px] text-[var(--muted)]">
          点「回测」在该次训练权重上跑一次样本内回测，查看 K 线级别的买卖回放、净值曲线与统计信息。
        </p>
      )}

      {loading && (
        <div className="flex flex-col gap-3">
          <MetricsCards loading />
          <div className="h-[320px] w-full animate-pulse rounded-[10px] border border-[var(--border)] bg-[var(--surface-2)]" />
        </div>
      )}

      {errored && (
        <p
          role="alert"
          title={(query.error as ApiRequestError)?.code ?? undefined}
          className="rounded-[10px] border border-[var(--short)]/40 bg-[var(--short)]/10 px-3 py-2 text-[11px] font-medium text-[var(--short-ink)]"
        >
          回测失败：{codeLabel((query.error as ApiRequestError)?.code)}
        </p>
      )}

      {result && !loading && (
        <div className="flex flex-col gap-3">
          <MetricsCards metrics={result.metrics} />
          <div className="rounded-[10px] border border-[var(--border)] bg-[var(--surface)] p-1">
            <WeightBacktestChart
              candles={result.candles}
              trades={result.trades}
              equity={result.equity}
              theme={theme}
            />
          </div>
          <div className="card p-3">
            <span className="mb-1.5 block text-[11px] font-medium text-[var(--muted)]">逐笔成交明细</span>
            <TradesTable trades={result.trades} />
          </div>
        </div>
      )}
    </section>
  );
}
