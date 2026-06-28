"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ApiRequestError, api } from "@/lib/api";
import { isCombo } from "@/lib/combo";
import {
  colorForPaperStrategy,
  resolveMaxPaperSessions,
  sessionsFromBatch,
  sessionsFromReconcile,
} from "@/lib/paperMulti";
import {
  STRATEGY_LIST_TABS,
  resolveVisibleStrategyId,
  splitStrategyCatalog,
  visibleStrategiesForStrategyPanel,
} from "@/lib/strategyCatalog";
import { useAppStore } from "@/lib/store";
import { downloadCsv, equityToCsv } from "@/lib/export";
import type {
  ComboPreviewResult,
  Metrics,
  PaperSessionRow,
  PaperStrategySession,
  Strategy,
  StrategyListTab,
  WalkForwardResult,
} from "@/lib/types";
import { Button, Skeleton, Tabs } from "@/components/ui";
import AutoTradeStatusBar from "./AutoTradeStatusBar";
import AutoTradeToggle from "./AutoTradeToggle";
import BacktestModeSwitch from "./BacktestModeSwitch";
import ComboResult from "./ComboResult";
import ComboStrategyDetail from "./ComboStrategyDetail";
import CompareView from "./CompareView";
import MetricsCards from "./MetricsCards";
import RunHistory from "./RunHistory";
import StrategyDetail from "./StrategyDetail";
import StrategyList from "./StrategyList";
import { useComboPanel } from "./useComboPanel";
import WalkForwardResults from "./WalkForwardResults";

type CoverageMeta = { effectiveCandleCount?: number; requestedBars?: number; truncated?: boolean };

type Params = Record<string, unknown>;
type PaperOpts = { source?: "live" | "replay"; speed?: number; replayBars?: number };

export default function StrategyPanel() {
  const qc = useQueryClient();
  const {
    instId,
    bar,
    mode,
    strategyListTab,
    backtestKind,
    wfParams,
    walkForwardResult,
    strategyId,
    runId,
    simBalance,
    paperSessionId,
    paperSelectionIds,
    paperBatchGroupId,
    paperSessionsByStrategyId,
    backtestStartTs,
    backtestEndTs,
    comboEditingId,
    setComboEditingId,
    setMode,
    setStrategyListTab,
    setStrategyId,
    setRunId,
    setChartView,
    setWalkForwardResult,
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
  const [coverage, setCoverage] = useState<CoverageMeta | undefined>();
  const [returnsById, setReturnsById] = useState<Record<number, number | undefined>>({});
  const [tab, setTab] = useState("metrics");
  const [stopError, setStopError] = useState<string | null>(null);
  // 组合回测结果（年化 / 轮动时间线 / 分体制贡献）：选中组合回测后接入绩效页签；原子回测回填 null。
  const [comboResult, setComboResult] = useState<ComboPreviewResult["combo"] | null>(null);
  const reconciledRef = useRef(false);
  const seededPaperSelectionRef = useRef(false);

  const clearBacktestResult = useCallback(() => {
    setRunId(null);
    setMetrics(undefined);
    setCoverage(undefined);
    setComboResult(null);
    setWalkForwardResult(null);
    setChartView("live-follow");
  }, [setRunId, setWalkForwardResult, setChartView]);

  const selectStrategyPassively = useCallback(
    (id: number | null) => {
      const changed = id !== strategyId;
      setStrategyId(id);
      if (mode === "backtest" && changed) clearBacktestResult();
    },
    [mode, strategyId, setStrategyId, clearBacktestResult],
  );

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

  // 选中合法性守门按「当前模式 + 当前分类页签」校验。空组合页签会清空旧结果，
  // 避免显示隐藏策略的历史指标或 Walk-forward 输出。
  useEffect(() => {
    if (!strategies) return;
    const resolvedId = resolveVisibleStrategyId({
      strategies,
      currentId: strategyId,
      mode,
      strategyListTab,
    });
    if (resolvedId === strategyId) return;
    setStrategyId(resolvedId);
    if (mode === "backtest") clearBacktestResult();
  }, [
    strategies,
    mode,
    strategyListTab,
    strategyId,
    setStrategyId,
    clearBacktestResult,
  ]);

  useEffect(() => {
    if (mode !== "paper") {
      seededPaperSelectionRef.current = false;
      return;
    }
    if (paperSelectionIds.length > 0) {
      seededPaperSelectionRef.current = true;
      return;
    }
    if (seededPaperSelectionRef.current || !strategyId) return;
    seededPaperSelectionRef.current = true;
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

  const baseStrategies = strategies ?? [];
  const catalog = useMemo(() => splitStrategyCatalog(baseStrategies), [baseStrategies]);
  const listStrategies = useMemo(
    () =>
      visibleStrategiesForStrategyPanel({
        strategies: baseStrategies,
        mode,
        strategyListTab,
      }),
    [baseStrategies, mode, strategyListTab],
  );
  const selected = listStrategies.find((s) => s.id === strategyId);
  const visibleSelectableStrategies = useMemo(
    () => listStrategies.filter((s) => !s.unavailable && s.enabled),
    [listStrategies],
  );
  const allSelectableStrategies = useMemo(
    () => [...catalog.regular, ...catalog.combos].filter((s) => !s.unavailable && s.enabled),
    [catalog],
  );
  const allSelectableIds = useMemo(
    () => new Set(allSelectableStrategies.map((s) => s.id)),
    [allSelectableStrategies],
  );
  const validPaperSelectionIds = useMemo(
    () => paperSelectionIds.filter((id) => allSelectableIds.has(id)),
    [paperSelectionIds, allSelectableIds],
  );
  const selectedCombo = selected && isCombo(selected) ? selected : null;
  const showEmptyComboTab =
    (mode === "backtest" || mode === "paper") &&
    strategyListTab === "combo" &&
    listStrategies.length === 0;
  const runningSessions = Object.values(paperSessionsByStrategyId);
  const selectedCount = validPaperSelectionIds.length;
  const capReached = selectedCount >= maxPaperSessions;
  const busy = false;

  useEffect(() => {
    if (mode !== "paper") return;
    if (validPaperSelectionIds.length === paperSelectionIds.length) return;
    setPaperSelectionIds(validPaperSelectionIds);
  }, [mode, paperSelectionIds.length, validPaperSelectionIds, setPaperSelectionIds]);

  const backtest = useMutation({
    mutationFn: (vars: { id: number; params?: Params; showRunRange?: boolean }) =>
      runBacktest(vars.id, vars.params, instId, bar, backtestStartTs, backtestEndTs),
    onSuccess: (data, vars) => {
      setRunId(data.runId);
      if (vars.showRunRange) setChartView("run-range");
      setMetrics(data.metrics);
      // 组合行后端追加 combo（年化 / 时间线 / 分体制）；原子行 combo 为 undefined → 回填 null 清陈旧。
      setComboResult(data.combo ?? null);
      // 全区间覆盖透明度（v2·P1-1）：记录覆盖元信息，truncated 时在绩效区上方显示横幅。
      setCoverage({
        effectiveCandleCount: data.effectiveCandleCount,
        requestedBars: data.requestedBars,
        truncated: data.truncated,
      });
      setReturnsById((prev) => ({ ...prev, [vars.id]: data.metrics.totalReturn }));
    },
  });

  // 高级回测：显式触发（不随参自动跑），运行后把结果写入 store.walkForwardResult 供右侧渲染。
  const walkForward = useMutation({
    mutationFn: (vars: { id: number; params?: Params }) =>
      runWalkForward(vars.id, vars.params, instId, bar, backtestStartTs, backtestEndTs, wfParams),
    onSuccess: (data) => {
      setRunId(data.runId);
      setWalkForwardResult(data);
    },
  });

  useEffect(() => {
    if (mode !== "backtest" || runId !== null) return;
    setMetrics(undefined);
    setCoverage(undefined);
    setComboResult(null);
    setWalkForwardResult(null);
  }, [mode, runId, setWalkForwardResult]);

  const equityQ = useQuery({
    queryKey: ["equity", runId],
    queryFn: () => api.equity(runId as number),
    enabled: !!runId,
  });

  const paperStart = useMutation({
    mutationFn: (vars: { params?: Params; skipUpdate?: boolean; opts?: PaperOpts }) =>
      startPaperSelection({
        activeStrategyId: strategyId as number,
        selectionIds: validPaperSelectionIds.length ? validPaperSelectionIds : [strategyId as number],
        namesById: Object.fromEntries(strategies?.map((item) => [item.id, item.name]) ?? []),
        params: vars.params,
        skipUpdate: vars.skipUpdate,
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
      selectStrategyPassively(created.id);
    },
  });

  // 组合三弹窗（编辑器 / 详情 / 删除）开关态、删除 mutation 与渲染抽到 useComboPanel（守 ≤750 行）。
  const { openNewCombo, openEditCombo, openComboDetail, openDeleteCombo, comboModals } =
    useComboPanel({
      strategies: strategies ?? [],
      strategyId,
      comboEditingId,
      setComboEditingId,
      setStrategyId: selectStrategyPassively,
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
    backtest.isPending ||
    walkForward.isPending ||
    paperStart.isPending ||
    paperStop.isPending ||
    paperStopAll.isPending ||
    busy;
  const startDisabledReason =
    mode === "paper" && selectedCount === 0
      ? "请选择至少一个策略"
      : mode === "paper" && selectedCount > maxPaperSessions
        ? `最多同时运行 ${maxPaperSessions} 个策略`
        : null;
  const disabledIds =
    runningSessions.length > 0
      ? listStrategies.map((s) => s.id)
      : capReached
        ? visibleSelectableStrategies
            .filter((s) => !validPaperSelectionIds.includes(s.id))
            .map((s) => s.id)
        : [];

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-start justify-between gap-2 border-b border-[var(--border)] px-4 py-3">
        <div className="min-w-0">
          <h2 className="text-sm font-semibold text-[var(--text)]">交易策略</h2>
          <p className="text-[11px] text-[var(--muted)]">
            {strategies.filter((s) => s.builtin).length} 个内置 · 共 {strategies.length} 个
          </p>
        </div>
        {mode === "backtest" && (
          <Button variant="default" size="sm" onClick={openNewCombo}>
            + 新建组合
          </Button>
        )}
        {/* 实时模式：标题栏右上「自动交易」开关（accountMode 等守门由 AutoTradeToggle 自读全局 store）。 */}
        {mode === "paper" && <AutoTradeToggle />}
      </div>

      {mode === "paper" && <AutoTradeStatusBar />}

      <div className="flex-1 overflow-auto px-4 py-3">
        {mode === "compare" ? (
          <CompareView />
        ) : (
          <>
            {mode === "paper" && (
              <PaperSelectionBar
                selectedCount={selectedCount}
                totalCount={allSelectableStrategies.length}
                maxPaperSessions={maxPaperSessions}
                disabled={runningSessions.length > 0}
                onSelectAll={() => {
                  const next = [...validPaperSelectionIds];
                  for (const strategy of visibleSelectableStrategies) {
                    if (next.length >= maxPaperSessions) break;
                    if (!next.includes(strategy.id)) next.push(strategy.id);
                  }
                  setPaperSelectionIds(next);
                }}
                onClear={() => setPaperSelectionIds([])}
              />
            )}

            {(mode === "backtest" || mode === "paper") && (
              <div className="mb-3">
                <Tabs
                  tabs={STRATEGY_LIST_TABS}
                  active={strategyListTab}
                  onChange={(key) => setStrategyListTab(key as StrategyListTab)}
                  variant="underline"
                />
              </div>
            )}

            {showEmptyComboTab ? (
              <div className="flex flex-col gap-2 rounded-[8px] border border-[var(--border)] bg-[var(--surface)] px-3 py-4 text-xs text-[var(--muted)]">
                <span>暂无组合策略</span>
                <div>
                  <Button size="sm" variant="default" onClick={openNewCombo}>
                    新建组合
                  </Button>
                </div>
              </div>
            ) : (
              <StrategyList
                strategies={listStrategies}
                activeId={strategyId}
                returnsById={returnsById}
                onSelect={selectStrategyPassively}
                multiSelectIds={mode === "paper" ? paperSelectionIds : undefined}
                multiSelectDisabledIds={mode === "paper" ? disabledIds : undefined}
                onToggleMultiSelect={mode === "paper" ? togglePaperSelectionId : undefined}
              />
            )}

            {selected && (
              <>
                <div className="my-3 border-t border-[var(--border)]" />
                {mode === "backtest" && (
                  <div className="mb-3">
                    <BacktestModeSwitch />
                  </div>
                )}
                {selectedCombo ? (
                  <>
                    <ComboStrategyDetail
                      combo={selectedCombo}
                      mode={mode === "paper" ? "paper" : "backtest"}
                      backtestKind={backtestKind}
                      busy={actionBusy}
                      paperActive={runningSessions.length > 0 || !!paperSessionId}
                      onRunBacktest={(c) => backtest.mutate({ id: c.id, showRunRange: true })}
                      onRunWalkForward={(c) => walkForward.mutate({ id: c.id })}
                      onStartPaper={(_combo, opts) => {
                        if (!startDisabledReason) paperStart.mutate({ skipUpdate: true, opts });
                      }}
                      onStopPaper={() => {
                        setPaperStreamsEnabled(false);
                        paperStop.mutate();
                      }}
                      onView={openComboDetail}
                      onEdit={openEditCombo}
                      onDelete={openDeleteCombo}
                    />
                    {startDisabledReason && (
                      <div className="mt-2 text-[11px] text-[var(--muted)]">{startDisabledReason}</div>
                    )}
                    {runningSessions.length > 0 && <PaperRunningList sessions={runningSessions} />}
                    {backtest.isError && (
                      <div className="mt-2 rounded-[8px] border border-[var(--short)] bg-[var(--surface)] px-3 py-2 text-[11px] text-[var(--short)]">
                        回测失败：{(backtest.error as Error)?.message}
                      </div>
                    )}
                    {(paperStart.isError || stopError) && (
                      <div className="mt-2 rounded-[8px] border border-[var(--short)] bg-[var(--surface)] px-3 py-2 text-[11px] text-[var(--short)]">
                        <div>
                          操作失败：
                          {stopError || (paperStart.error as Error)?.message}
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
                ) : (
                  <>
                    <StrategyDetail
                      strategy={selected}
                      mode={mode === "paper" ? "paper" : "backtest"}
                      backtestKind={backtestKind}
                      busy={actionBusy}
                      paperActive={runningSessions.length > 0 || !!paperSessionId}
                      simBalance={simBalance}
                      onApplyAndBacktest={(params) =>
                        backtest.mutate({ id: selected.id, params, showRunRange: true })
                      }
                      onRunWalkForward={(params) => walkForward.mutate({ id: selected.id, params })}
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
              </>
            )}

            {selected && (
              mode === "backtest" && backtestKind === "walkforward" ? (
                <>
                  <div className="my-3 border-t border-[var(--border)]" />
                  <WalkForwardResults
                    result={walkForwardResult}
                    loading={walkForward.isPending}
                    error={walkForward.isError ? (walkForward.error as Error)?.message : null}
                  />
                </>
              ) : (
                <>
                  <div className="my-3 border-t border-[var(--border)]" />
                  <div className="mb-2">
                    <Tabs
                      tabs={[
                        { key: "metrics", label: "绩效指标" },
                        { key: "history", label: "历史" },
                      ]}
                      active={tab}
                      onChange={setTab}
                    />
                  </div>
                  {tab === "metrics" ? (
                    <>
                      {coverage?.truncated && (
                        <div className="mb-2 rounded-[8px] border border-[var(--border)] bg-[var(--surface-2)] px-3 py-2 text-[11px] text-[var(--muted)]">
                          ⚠ 区间过长 / 历史不足，仅覆盖最近 {coverage.effectiveCandleCount} /{" "}
                          {coverage.requestedBars} 根，收益率为部分区间结果。
                        </div>
                      )}
                      <div className="mb-2 flex justify-end">
                        <Button
                          variant="ghost"
                          size="sm"
                          disabled={!equityQ.data?.length}
                          onClick={() =>
                            runId &&
                            equityQ.data &&
                            downloadCsv(
                              `aragon-equity-run${runId}.csv`,
                              equityToCsv(equityQ.data),
                            )
                          }
                        >
                          导出净值 CSV
                        </Button>
                      </div>
                      {selectedCombo && comboResult && metrics ? (
                        <ComboResult
                          result={{ metrics, combo: comboResult }}
                          onGoCompare={() => setMode("compare")}
                        />
                      ) : (
                        <MetricsCards metrics={metrics} loading={backtest.isPending} />
                      )}
                    </>
                  ) : (
                    <RunHistory />
                  )}
                </>
              )
            )}
          </>
        )}
      </div>

      {/* 组合弹窗（Portal 居中，复用 Modal 原语）：新建 / 编辑 · 查看 · 删除二次确认。
          开关态与渲染在 useComboPanel 内，由列表选中态 / 头部「+ 新建组合」入口驱动。 */}
      {comboModals}
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
          全选当前页
        </Button>
        <Button variant="ghost" size="sm" disabled={disabled} onClick={onClear}>
          清空全部
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
  // 去掉硬编码 limit（v2·P1-1）：后端全区间分页保证 [startTs,endTs] 全覆盖，不再截断到最近 1000 根。
  return api.backtest(id, { instId, bar, startTs, endTs });
}

async function runWalkForward(
  id: number,
  params: Params | undefined,
  instId: string,
  bar: string,
  startTs: number,
  endTs: number,
  wfParams: { windowBars: number; stepBars: number; resetCapital: boolean },
): Promise<WalkForwardResult> {
  if (params) await api.updateStrategy(id, { params });
  return api.walkForward(id, {
    instId,
    bar,
    startTs,
    endTs,
    windowBars: wfParams.windowBars,
    stepBars: wfParams.stepBars,
    resetCapital: wfParams.resetCapital,
  });
}

async function startPaperSelection({
  activeStrategyId,
  selectionIds,
  namesById,
  params,
  skipUpdate,
  instId,
  bar,
  simBalance,
  opts,
}: {
  activeStrategyId: number;
  selectionIds: number[];
  namesById: Record<number, string>;
  params?: Params;
  skipUpdate?: boolean;
  instId: string;
  bar: string;
  simBalance: number;
  opts?: PaperOpts;
}): Promise<{ sessions: Record<number, PaperStrategySession>; groupId: string | null }> {
  if (params && !skipUpdate) await api.updateStrategy(activeStrategyId, { params });
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
          strategyKind: result.strategyKind,
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
