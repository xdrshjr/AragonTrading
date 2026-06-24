"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useRef, useState } from "react";
import { ApiRequestError, api } from "@/lib/api";
import {
  colorForPaperStrategy,
  resolveMaxPaperSessions,
  sessionsFromBatch,
  sessionsFromReconcile,
} from "@/lib/paperMulti";
import { useAppStore } from "@/lib/store";
import { downloadCsv, equityToCsv, tradesToCsv } from "@/lib/export";
import type { Metrics, PaperSessionRow, PaperStrategySession, Strategy } from "@/lib/types";
import { Button, Skeleton, Tabs } from "@/components/ui";
import ComboView from "./ComboView";
import CompareView from "./CompareView";
import MetricsCards from "./MetricsCards";
import RunHistory from "./RunHistory";
import StrategyDetail from "./StrategyDetail";
import StrategyList from "./StrategyList";
import TradesTable from "./TradesTable";

type Params = Record<string, unknown>;
type PaperOpts = { source?: "live" | "replay"; speed?: number; replayBars?: number };

export default function StrategyPanel() {
  const qc = useQueryClient();
  const {
    instId,
    bar,
    mode,
    strategyId,
    runId,
    simBalance,
    paperSessionId,
    paperSelectionIds,
    paperBatchGroupId,
    paperSessionsByStrategyId,
    backtestStartTs,
    backtestEndTs,
    setStrategyId,
    setRunId,
    setChartView,
    setPaperSessionId,
    setPaperSelectionIds,
    togglePaperSelectionId,
    setPaperVisibleStrategyIds,
    setPaperActiveStrategyId,
    setPaperBatchGroupId,
    setPaperSessionsByStrategyId,
    setPaperStreamsEnabled,
    clearPaperRuntime,
  } = useAppStore();

  const [metrics, setMetrics] = useState<Metrics | undefined>();
  const [returnsById, setReturnsById] = useState<Record<number, number | undefined>>({});
  const [tab, setTab] = useState("metrics");
  const [stopError, setStopError] = useState<string | null>(null);
  const lastAutoKey = useRef("");
  const reconciledRef = useRef(false);

  const { data: strategies, isLoading } = useQuery({
    queryKey: ["strategies"],
    queryFn: api.strategies,
  });
  const health = useQuery({ queryKey: ["health"], queryFn: api.health });
  const maxPaperSessions = resolveMaxPaperSessions(health.data?.maxPaperSessions);

  // 候选 A：挂载即对账。后端 PaperManager 是进程级单例，刷新/重开后会话变成孤儿仍占配额，
  // 而 store 不持久化运行期句柄 → UI 误以为无会话运行，再次启动即被 429「active … exceed limit」。
  // 拉取真实会话回填 store，让用户看得见、可停止、可在剩余配额内继续。
  const paperSessionsQ = useQuery({
    queryKey: ["paperSessions"],
    queryFn: api.paperSessions,
    enabled: mode === "paper",
    refetchOnWindowFocus: false,
  });

  useEffect(() => {
    if (!strategies?.length) return;
    const exists = strategyId !== null && strategies.some((s) => s.id === strategyId);
    if (!exists) setStrategyId(strategies[0].id);
  }, [strategies, strategyId, setStrategyId]);

  useEffect(() => {
    if (mode !== "paper" || !strategyId || paperSelectionIds.length > 0) return;
    setPaperSelectionIds([strategyId]);
  }, [mode, strategyId, paperSelectionIds.length, setPaperSelectionIds]);

  // 把对账行回填为运行期句柄；与 paperStart.onSuccess 同口径（含开启 ws 流以重放快照恢复实时数据）。
  const applyReconcile = useCallback(
    (rows: PaperSessionRow[]): boolean => {
      if (!strategies) return false;
      const namesById = Object.fromEntries(strategies.map((s) => [s.id, s.name]));
      const { sessions, groupId } = sessionsFromReconcile(rows, namesById);
      if (Object.keys(sessions).length === 0) return false;
      const first = Object.values(sessions)[0];
      setPaperSessionsByStrategyId(sessions);
      setPaperBatchGroupId(groupId);
      setPaperSessionId(first?.sessionId ?? null);
      setRunId(first?.runId ?? null);
      setPaperActiveStrategyId(first?.strategyId ?? null);
      setPaperVisibleStrategyIds(Object.keys(sessions).map(Number));
      setPaperStreamsEnabled(true);
      return true;
    },
    [
      strategies,
      setPaperSessionsByStrategyId,
      setPaperBatchGroupId,
      setPaperSessionId,
      setRunId,
      setPaperActiveStrategyId,
      setPaperVisibleStrategyIds,
      setPaperStreamsEnabled,
    ],
  );

  // 仅在挂载后对账一次；store 已有句柄（同会话内刚启动）则视为权威，不覆盖。
  useEffect(() => {
    if (mode !== "paper" || reconciledRef.current) return;
    if (!strategies || !paperSessionsQ.data) return;
    reconciledRef.current = true;
    if (Object.keys(paperSessionsByStrategyId).length > 0) return;
    applyReconcile(paperSessionsQ.data);
  }, [mode, strategies, paperSessionsQ.data, paperSessionsByStrategyId, applyReconcile]);

  const selected = strategies?.find((s) => s.id === strategyId);
  // 组合策略在「组合」页签构建/回测、在「对比」页签横向比较；回测/实时列表只列原子策略，
  // 既避免在实时模拟误启动组合（组合不接 paper），也保持各模式职责清晰。
  const atomicStrategies = strategies?.filter((s) => s.kind !== "combo") ?? [];
  const enabledStrategies = atomicStrategies.filter((s) => !s.unavailable && s.enabled);
  const runningSessions = Object.values(paperSessionsByStrategyId);
  const selectedCount = paperSelectionIds.length;
  const capReached = selectedCount >= maxPaperSessions;
  const busy = false;

  const backtest = useMutation({
    mutationFn: (vars: { id: number; params?: Params; showRunRange?: boolean }) =>
      runBacktest(vars.id, vars.params, instId, bar, backtestStartTs, backtestEndTs),
    onSuccess: (data, vars) => {
      setRunId(data.runId);
      if (vars.showRunRange) setChartView("run-range");
      setMetrics(data.metrics);
      setReturnsById((prev) => ({ ...prev, [vars.id]: data.metrics.totalReturn }));
    },
  });

  useEffect(() => {
    if (mode !== "backtest" || !strategyId || !strategies) return;
    const key = `${strategyId}|${instId}|${bar}|${backtestStartTs}|${backtestEndTs}`;
    if (lastAutoKey.current === key) return;
    lastAutoKey.current = key;
    backtest.mutate({ id: strategyId });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [strategyId, instId, bar, mode, strategies, backtestStartTs, backtestEndTs]);

  const { data: trades } = useQuery({
    queryKey: ["trades", runId],
    queryFn: () => api.trades(runId as number),
    enabled: !!runId,
    refetchInterval: mode === "paper" && paperSessionId ? 3000 : false,
  });

  const equityQ = useQuery({
    queryKey: ["equity", runId],
    queryFn: () => api.equity(runId as number),
    enabled: !!runId,
  });

  const paperStart = useMutation({
    mutationFn: (vars: { params: Params; opts?: PaperOpts }) =>
      startPaperSelection({
        activeStrategyId: strategyId as number,
        selectionIds: paperSelectionIds.length ? paperSelectionIds : [strategyId as number],
        namesById: Object.fromEntries(strategies?.map((item) => [item.id, item.name]) ?? []),
        params: vars.params,
        instId,
        bar,
        simBalance,
        opts: vars.opts,
      }),
    onSuccess: ({ sessions, groupId }) => {
      const first = Object.values(sessions)[0];
      setPaperSessionsByStrategyId(sessions);
      setPaperBatchGroupId(groupId);
      setPaperSessionId(first?.sessionId ?? null);
      setRunId(first?.runId ?? null);
      setPaperActiveStrategyId(first?.strategyId ?? null);
      setPaperVisibleStrategyIds(Object.keys(sessions).map(Number));
      setPaperStreamsEnabled(true);
      setMetrics(undefined);
      setStopError(null);
    },
  });

  const paperStop = useMutation({
    mutationFn: () => stopPaperSessions(paperBatchGroupId, runningSessions),
    onSuccess: (runs) => {
      clearPaperRuntime();
      const lastRun = runs.at(-1)?.run;
      if (lastRun) setMetrics(lastRun.metrics);
      setStopError(null);
    },
    onError: (error) => setStopError((error as Error).message),
  });

  // 「停止全部并重试」专用：调用后端 POST /api/paper/stop-all 停止注册表中**全部** running
  // 会话（含 store 因 dedup-by-strategyId 看不见、停不掉的孤儿），确定性回收配额；
  // 再拉回真实会话列表对齐 UI。这是 round-1 前端对账/停止之所以「没修改好」的根因修复。
  const paperStopAll = useMutation({
    mutationFn: () => api.paperStopAll(),
    onSuccess: () => {
      clearPaperRuntime();
      qc.invalidateQueries({ queryKey: ["paperSessions"] });
      setStopError(null);
    },
    onError: (error) => setStopError((error as Error).message),
  });

  const saveAs = useMutation({
    mutationFn: (vars: { name: string; params: Params }) =>
      api.createStrategy({ templateKey: selected!.templateKey, name: vars.name, params: vars.params }),
    onSuccess: (created) => {
      qc.invalidateQueries({ queryKey: ["strategies"] });
      setStrategyId(created.id);
    },
  });

  const isSessionLimitError =
    paperStart.error instanceof ApiRequestError && paperStart.error.code === "SESSION_LIMIT";

  // 候选 C（安全网）：启动撞上 SESSION_LIMIT（孤儿会话占满配额）时，把真实会话对账回 UI，
  // 用户即可看到是什么占着配额，并一键「停止全部并重试」，而非停在死胡同错误上。
  useEffect(() => {
    if (!isSessionLimitError) return;
    let cancelled = false;
    api
      .paperSessions()
      .then((rows) => {
        if (!cancelled) applyReconcile(rows);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [isSessionLimitError, applyReconcile]);

  const stopAllAndRetry = () => {
    const retryVars = paperStart.variables;
    setPaperStreamsEnabled(false);
    paperStopAll.mutate(undefined, {
      onSuccess: () => {
        if (retryVars) paperStart.mutate(retryVars);
      },
    });
  };

  if (isLoading || !strategies) {
    return (
      <div className="flex flex-col gap-2 p-4">
        {Array.from({ length: 5 }).map((_, i) => (
          <Skeleton key={i} className="h-16" />
        ))}
      </div>
    );
  }

  const actionBusy =
    backtest.isPending || paperStart.isPending || paperStop.isPending || paperStopAll.isPending || busy;
  const startDisabledReason =
    mode === "paper" && selectedCount === 0
      ? "请选择至少一个策略"
      : mode === "paper" && selectedCount > maxPaperSessions
        ? `最多同时运行 ${maxPaperSessions} 个策略`
        : null;
  const disabledIds =
    runningSessions.length > 0
      ? enabledStrategies.map((s) => s.id)
      : capReached
        ? enabledStrategies.filter((s) => !paperSelectionIds.includes(s.id)).map((s) => s.id)
        : [];

  return (
    <div className="flex h-full flex-col">
      <div className="border-b border-[var(--border)] px-4 py-3">
        <h2 className="text-sm font-semibold text-[var(--text)]">交易策略</h2>
        <p className="text-[11px] text-[var(--muted)]">
          {strategies.filter((s) => s.builtin).length} 个内置 · 共 {strategies.length} 个
        </p>
      </div>

      <div className="flex-1 overflow-auto px-4 py-3">
        {mode === "combo" ? (
          <ComboView />
        ) : mode === "compare" ? (
          <CompareView />
        ) : (
          <>
            {mode === "paper" && (
              <PaperSelectionBar
                selectedCount={selectedCount}
                totalCount={enabledStrategies.length}
                maxPaperSessions={maxPaperSessions}
                disabled={runningSessions.length > 0}
                onSelectAll={() =>
                  setPaperSelectionIds(enabledStrategies.slice(0, maxPaperSessions).map((s) => s.id))
                }
                onClear={() => setPaperSelectionIds([])}
              />
            )}

            <StrategyList
              strategies={atomicStrategies}
              activeId={strategyId}
              returnsById={returnsById}
              onSelect={(id) => setStrategyId(id)}
              multiSelectIds={mode === "paper" ? paperSelectionIds : undefined}
              multiSelectDisabledIds={mode === "paper" ? disabledIds : undefined}
              onToggleMultiSelect={mode === "paper" ? togglePaperSelectionId : undefined}
            />

            {selected && (
              <>
                <div className="my-3 border-t border-[var(--border)]" />
                <StrategyDetail
                  strategy={selected}
                  mode={mode === "paper" ? "paper" : "backtest"}
                  busy={actionBusy}
                  paperActive={runningSessions.length > 0 || !!paperSessionId}
                  simBalance={simBalance}
                  onApplyAndBacktest={(params) =>
                    backtest.mutate({ id: selected.id, params, showRunRange: true })
                  }
                  onStartPaper={(params, opts) => {
                    if (!startDisabledReason) paperStart.mutate({ params, opts });
                  }}
                  onStopPaper={() => {
                    setPaperStreamsEnabled(false);
                    paperStop.mutate();
                  }}
                  onSaveAs={(name, params) => saveAs.mutate({ name, params })}
                />
                {startDisabledReason && (
                  <div className="mt-2 text-[11px] text-[var(--muted)]">{startDisabledReason}</div>
                )}
                {runningSessions.length > 0 && <PaperRunningList sessions={runningSessions} />}
                {(backtest.isError || paperStart.isError || stopError) && (
                  <div className="mt-2 rounded-[8px] border border-[var(--short)] bg-[var(--surface)] px-3 py-2 text-[11px] text-[var(--short)]">
                    <div>
                      操作失败：
                      {stopError ||
                        (backtest.error as Error)?.message ||
                        (paperStart.error as Error)?.message}
                    </div>
                    {isSessionLimitError && (
                      <div className="mt-1.5">
                        <Button
                          variant="danger"
                          size="sm"
                          disabled={paperStopAll.isPending || paperStart.isPending}
                          onClick={stopAllAndRetry}
                        >
                          停止全部并重试
                        </Button>
                      </div>
                    )}
                  </div>
                )}
              </>
            )}

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
                    导出净值 CSV
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
                      runId && trades && downloadCsv(`aragon-trades-run${runId}.csv`, tradesToCsv(trades))
                    }
                  >
                    导出 CSV
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

function PaperSelectionBar({
  selectedCount,
  totalCount,
  maxPaperSessions,
  disabled,
  onSelectAll,
  onClear,
}: {
  selectedCount: number;
  totalCount: number;
  maxPaperSessions: number;
  disabled: boolean;
  onSelectAll: () => void;
  onClear: () => void;
}) {
  return (
    <div className="mb-2 flex items-center justify-between gap-2 rounded-[8px] border border-[var(--border)] bg-[var(--surface-2)] px-2 py-1.5">
      <span className="text-[11px] text-[var(--muted)]">
        已选 {selectedCount}/{Math.min(totalCount, maxPaperSessions)}
      </span>
      <div className="flex gap-1">
        <Button variant="ghost" size="sm" disabled={disabled} onClick={onSelectAll}>
          全选
        </Button>
        <Button variant="ghost" size="sm" disabled={disabled} onClick={onClear}>
          清空
        </Button>
      </div>
    </div>
  );
}

function PaperRunningList({ sessions }: { sessions: PaperStrategySession[] }) {
  return (
    <div className="mt-2 space-y-1 rounded-[8px] border border-[var(--border)] bg-[var(--surface-2)] p-2">
      {sessions.map((session) => (
        <div key={session.strategyId} className="flex items-center justify-between gap-2 text-[11px]">
          <span className="flex min-w-0 items-center gap-1.5">
            <span className="h-2 w-2 rounded-full" style={{ background: session.color }} />
            <span className="truncate">{session.strategyName}</span>
          </span>
          <span className="tabular text-[var(--muted)]">run #{session.runId}</span>
        </div>
      ))}
    </div>
  );
}

async function runBacktest(
  id: number,
  params: Params | undefined,
  instId: string,
  bar: string,
  startTs: number,
  endTs: number,
) {
  if (params) await api.updateStrategy(id, { params });
  return api.backtest(id, { instId, bar, limit: 1000, startTs, endTs });
}

async function startPaperSelection({
  activeStrategyId,
  selectionIds,
  namesById,
  params,
  instId,
  bar,
  simBalance,
  opts,
}: {
  activeStrategyId: number;
  selectionIds: number[];
  namesById: Record<number, string>;
  params: Params;
  instId: string;
  bar: string;
  simBalance: number;
  opts?: PaperOpts;
}): Promise<{ sessions: Record<number, PaperStrategySession>; groupId: string | null }> {
  await api.updateStrategy(activeStrategyId, { params });
  const uniqueIds = Array.from(new Set(selectionIds));
  if (uniqueIds.length <= 1) {
    const id = uniqueIds[0] ?? activeStrategyId;
    const result = await api.paperStart({
      strategyId: id,
      instId,
      bar,
      initialCapital: simBalance,
      priming: true,
      ...opts,
    });
    return {
      groupId: null,
      sessions: {
        [id]: {
          strategyId: id,
          strategyName: namesById[id] ?? `Strategy ${id}`,
          sessionId: result.sessionId,
          runId: result.runId,
          color: colorForPaperStrategy(0),
          state: "running",
        },
      },
    };
  }
  const result = await api.paperBatchStart({
    strategyIds: uniqueIds,
    instId,
    bar,
    initialCapital: simBalance,
    priming: true,
    ...opts,
  });
  return { groupId: result.groupId, sessions: sessionsFromBatch(result) };
}

async function stopPaperSessions(
  groupId: string | null,
  sessions: PaperStrategySession[],
): Promise<{ strategyId: number; sessionId: string; run: { metrics: Metrics } }[]> {
  if (groupId) {
    try {
      return (await api.paperBatchStop(groupId)).runs;
    } catch (error) {
      if (!(error instanceof ApiRequestError) || error.status !== 404) throw error;
    }
  }
  const runs = [];
  for (const session of sessions) {
    const run = await api.paperStop(session.sessionId);
    runs.push({ strategyId: session.strategyId, sessionId: session.sessionId, run });
  }
  return runs;
}
