"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import { useAppStore } from "@/lib/store";
import type { Metrics, Strategy } from "@/lib/types";
import { Skeleton, Tabs } from "@/components/ui";
import StrategyList from "./StrategyList";
import StrategyDetail from "./StrategyDetail";
import MetricsCards from "./MetricsCards";
import TradesTable from "./TradesTable";

type Params = Record<string, unknown>;

export default function StrategyPanel() {
  const qc = useQueryClient();
  const { instId, bar, mode, strategyId, runId, paperSessionId, setStrategyId, setRunId, setPaperSessionId } =
    useAppStore();

  const [metrics, setMetrics] = useState<Metrics | undefined>();
  const [returnsById, setReturnsById] = useState<Record<number, number | undefined>>({});
  const [tab, setTab] = useState("metrics");
  const lastAutoKey = useRef<string>("");

  const { data: strategies, isLoading } = useQuery({
    queryKey: ["strategies"],
    queryFn: api.strategies,
  });

  // 默认选中第一个
  useEffect(() => {
    if (strategies && strategies.length > 0 && strategyId === null) {
      setStrategyId(strategies[0].id);
    }
  }, [strategies, strategyId, setStrategyId]);

  const selected: Strategy | undefined = strategies?.find((s) => s.id === strategyId);

  const backtest = useMutation({
    mutationFn: (vars: { id: number; params?: Params }) =>
      runBacktest(vars.id, vars.params, instId, bar),
    onSuccess: (data, vars) => {
      setRunId(data.runId);
      setMetrics(data.metrics);
      setReturnsById((prev) => ({ ...prev, [vars.id]: data.metrics.totalReturn }));
    },
  });

  // 选择策略 / 切换标的周期 → 自动回测（acceptance #3）
  useEffect(() => {
    if (mode !== "backtest" || !strategyId || !strategies) return;
    const key = `${strategyId}|${instId}|${bar}`;
    if (lastAutoKey.current === key) return;
    lastAutoKey.current = key;
    backtest.mutate({ id: strategyId });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [strategyId, instId, bar, mode, strategies]);

  // 成交明细（回测拉一次；实时模拟轮询）
  const { data: trades } = useQuery({
    queryKey: ["trades", runId],
    queryFn: () => api.trades(runId as number),
    enabled: !!runId,
    refetchInterval: mode === "paper" && paperSessionId ? 3000 : false,
  });

  const paperStart = useMutation({
    mutationFn: (params: Params) =>
      applyThenStartPaper(strategyId as number, params, instId, bar),
    onSuccess: (data) => {
      setPaperSessionId(data.sessionId);
      setRunId(data.runId);
      setMetrics(undefined);
    },
  });

  const paperStop = useMutation({
    mutationFn: () => api.paperStop(paperSessionId as string),
    onSuccess: (run) => {
      setPaperSessionId(null);
      setMetrics(run.metrics);
    },
  });

  const saveAs = useMutation({
    mutationFn: (vars: { name: string; params: Params }) =>
      api.createStrategy({ templateKey: selected!.templateKey, name: vars.name, params: vars.params }),
    onSuccess: (created) => {
      qc.invalidateQueries({ queryKey: ["strategies"] });
      setStrategyId(created.id);
    },
  });

  if (isLoading || !strategies) {
    return (
      <div className="flex flex-col gap-2 p-4">
        {Array.from({ length: 5 }).map((_, i) => (
          <Skeleton key={i} className="h-16" />
        ))}
      </div>
    );
  }

  const busy = backtest.isPending || paperStart.isPending || paperStop.isPending;

  return (
    <div className="flex h-full flex-col">
      <div className="border-b border-[var(--border)] px-4 py-3">
        <h2 className="text-sm font-semibold text-[var(--text)]">交易策略</h2>
        <p className="text-[11px] text-[var(--muted)]">
          {strategies.filter((s) => s.builtin).length} 个内置 · 共 {strategies.length} 个
        </p>
      </div>

      <div className="flex-1 overflow-auto px-4 py-3">
        <StrategyList
          strategies={strategies}
          activeId={strategyId}
          returnsById={returnsById}
          onSelect={(id) => setStrategyId(id)}
        />

        {selected && (
          <>
            <div className="my-3 border-t border-[var(--border)]" />
            <StrategyDetail
              strategy={selected}
              mode={mode}
              busy={busy}
              paperActive={!!paperSessionId}
              onApplyAndBacktest={(params) => backtest.mutate({ id: selected.id, params })}
              onStartPaper={(params) => paperStart.mutate(params)}
              onStopPaper={() => paperStop.mutate()}
              onSaveAs={(name, params) => saveAs.mutate({ name, params })}
            />

            {(backtest.isError || paperStart.isError) && (
              <div className="mt-2 rounded-[8px] border border-[var(--short)] bg-[var(--surface)] px-3 py-2 text-[11px] text-[var(--short)]">
                操作失败：{(backtest.error as Error)?.message || (paperStart.error as Error)?.message}
              </div>
            )}

            <div className="my-3 border-t border-[var(--border)]" />
            <div className="mb-2">
              <Tabs
                tabs={[
                  { key: "metrics", label: "绩效指标" },
                  { key: "trades", label: "成交明细" },
                ]}
                active={tab}
                onChange={setTab}
              />
            </div>
            {tab === "metrics" ? (
              <MetricsCards metrics={metrics} loading={backtest.isPending} />
            ) : (
              <TradesTable trades={trades} />
            )}
          </>
        )}
      </div>
    </div>
  );
}

async function runBacktest(id: number, params: Params | undefined, instId: string, bar: string) {
  if (params) {
    await api.updateStrategy(id, { params });
  }
  return api.backtest(id, { instId, bar, limit: 500 });
}

async function applyThenStartPaper(id: number, params: Params, instId: string, bar: string) {
  await api.updateStrategy(id, { params });
  return api.paperStart({ strategyId: id, instId, bar });
}
