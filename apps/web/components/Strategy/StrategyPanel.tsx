"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import { equityToCsv, downloadCsv, tradesToCsv } from "@/lib/export";
import { useAppStore } from "@/lib/store";
import type { Metrics, Strategy } from "@/lib/types";
import { Button, Skeleton, Tabs } from "@/components/ui";
import StrategyList from "./StrategyList";
import StrategyDetail from "./StrategyDetail";
import MetricsCards from "./MetricsCards";
import TradesTable from "./TradesTable";
import CompareView from "./CompareView";
import RunHistory from "./RunHistory";

type Params = Record<string, unknown>;
type PaperOpts = { source?: "live" | "replay"; speed?: number; replayBars?: number };

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

  // 默认选中第一个；F3：持久化恢复的 strategyId 若已不在列表中（被删的自定义策略）也回落第一个，
  // 避免空详情 / 自动回测打到不存在的 id。
  useEffect(() => {
    if (!strategies || strategies.length === 0) return;
    const exists = strategyId !== null && strategies.some((s) => s.id === strategyId);
    if (!exists) {
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

  // 净值（F4 导出用）：与 page 同键查询共享 RQ 缓存，不重复请求
  const equityQ = useQuery({
    queryKey: ["equity", runId],
    queryFn: () => api.equity(runId as number),
    enabled: !!runId,
  });

  const paperStart = useMutation({
    mutationFn: (vars: { params: Params; opts?: PaperOpts }) =>
      applyThenStartPaper(strategyId as number, vars.params, instId, bar, vars.opts),
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
        {mode === "compare" ? (
          <CompareView />
        ) : (
          <>
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
                  mode={mode === "paper" ? "paper" : "backtest"}
                  busy={busy}
                  paperActive={!!paperSessionId}
                  onApplyAndBacktest={(params) => backtest.mutate({ id: selected.id, params })}
                  onStartPaper={(params, opts) => paperStart.mutate({ params, opts })}
                  onStopPaper={() => paperStop.mutate()}
                  onSaveAs={(name, params) => saveAs.mutate({ name, params })}
                />

                {(backtest.isError || paperStart.isError) && (
                  <div className="mt-2 rounded-[8px] border border-[var(--short)] bg-[var(--surface)] px-3 py-2 text-[11px] text-[var(--short)]">
                    操作失败：{(backtest.error as Error)?.message || (paperStart.error as Error)?.message}
                  </div>
                )}
              </>
            )}

            {/* 历史 Tab 挂在 selected 外层：运行历史是全局的，不因未选中策略而隐藏（C8）*/}
            <div className="my-3 border-t border-[var(--border)]" />
            <div className="mb-2">
              <Tabs
                tabs={[
                  { key: "metrics", label: "绩效指标" },
                  { key: "trades", label: "成交明细" },
                  { key: "history", label: "历史" },
                ]}
                active={tab}
                onChange={setTab}
              />
            </div>
            {tab === "metrics" ? (
              <>
                <div className="mb-2 flex justify-end">
                  <Button
                    variant="ghost"
                    size="sm"
                    disabled={!equityQ.data?.length}
                    onClick={() =>
                      runId &&
                      equityQ.data &&
                      downloadCsv(`aragon-equity-run${runId}.csv`, equityToCsv(equityQ.data))
                    }
                  >
                    ⬇ 导出净值 CSV
                  </Button>
                </div>
                <MetricsCards metrics={metrics} loading={backtest.isPending} />
              </>
            ) : tab === "trades" ? (
              <>
                <div className="mb-2 flex justify-end">
                  <Button
                    variant="ghost"
                    size="sm"
                    disabled={!trades?.length}
                    onClick={() =>
                      runId &&
                      trades &&
                      downloadCsv(`aragon-trades-run${runId}.csv`, tradesToCsv(trades))
                    }
                  >
                    ⬇ 导出 CSV
                  </Button>
                </div>
                <TradesTable trades={trades} />
              </>
            ) : (
              <RunHistory />
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

async function applyThenStartPaper(
  id: number,
  params: Params,
  instId: string,
  bar: string,
  opts?: PaperOpts,
) {
  await api.updateStrategy(id, { params });
  return api.paperStart({ strategyId: id, instId, bar, ...opts });
}
