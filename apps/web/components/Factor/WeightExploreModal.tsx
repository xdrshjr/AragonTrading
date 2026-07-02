"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState } from "react";
import { Badge, Button } from "@/components/ui";
import { ApiRequestError } from "@/lib/api";
import type { FactorLibraryItem, FactorMember, FactorThresholds } from "@/lib/factorTypes";
import { useAppStore } from "@/lib/store";
import {
  allSettled,
  applyCandidateEvent,
  bestRunId,
  emptyCandidate,
  rankCandidates,
} from "@/lib/weightBatch";
import { weightBatchApi } from "@/lib/weightBatchApi";
import { weightExploreApi } from "@/lib/weightExploreApi";
import { weightBatchWS } from "@/lib/weightBatchWs";
import type { BatchStreamEvent, CandidateState } from "@/lib/weightBatchTypes";
import {
  applyBestTarget,
  assembleMatrix,
  buildExploreBody,
  localPlan,
  planLocalCombos,
  type ExplorePlan,
  type LocalCombo,
} from "@/lib/weightExplore";
import { buildTrainResult, codeLabel } from "@/lib/weightModel";
import ExploreConfigPanel, { type ExploreConfig } from "./ExploreConfigPanel";
import ExploreResultPanel from "./ExploreResultPanel";
import ExploreRunningPanel from "./ExploreRunningPanel";

const STALE_STREAM_MS = 20_000 * 2; // 进入订阅后超此无任何事件（含 ping）→ 提示「等待数据」（对齐竞技场）
const EXPLORE_HORIZON = 6; // 与 FactorSetBuilder goTrain/goBatch 同口径

interface ExploreCtx {
  library: FactorLibraryItem[];
  members: FactorMember[];
  thresholds: FactorThresholds;
  name: string;
  instId: string;
  bar: string;
  startTs: number;
  endTs: number;
  factorSetId?: number;
}

/**
 * 因子组合探索训练弹窗（factor-combo-exploration §2.7）：FactorSetBuilder 内本地 modal（非 arena 的
 * view.kind/store 交接）。外壳 = 阶段路由（配置 / 运行 / 完成）+ WS 订阅生命周期 + 回流；三阶段拆子面板。
 *
 * WS 复用 weightBatchWS + weightBatch.ts reducers（探索批次事件流与批次完全一致）。回流经共享 buildTrainResult
 * 单写入器 + modelId=null（全新草稿，不绑定探索父模型，P0-1）；关闭后由 FactorSetBuilder 既有 weightTrainResult
 * 消费 effect（consumedResultRef 防重入）自动水合。
 */
export default function WeightExploreModal({ onClose, ctx }: { onClose: () => void; ctx: ExploreCtx }) {
  const qc = useQueryClient();
  const setWeightTrainResult = useAppStore((s) => s.setWeightTrainResult);

  const [batchId, setBatchId] = useState<number | null>(null);
  const [localCombos, setLocalCombos] = useState<LocalCombo[]>([]);
  const [plan, setPlan] = useState<ExplorePlan | null>(null);
  const [candidates, setCandidates] = useState<Record<number, CandidateState>>({});
  const [order, setOrder] = useState<number[]>([]);
  const [connected, setConnected] = useState(true);
  const [stale, setStale] = useState(false);

  const mountedRef = useRef(true);
  const wsRef = useRef<ReturnType<typeof weightBatchWS> | null>(null);
  const watchdogRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const labelOf = (m: FactorMember) => ctx.library.find((f) => f.key === m.factorKey)?.cn ?? m.factorKey;

  const armWatchdog = () => {
    if (watchdogRef.current) clearTimeout(watchdogRef.current);
    watchdogRef.current = setTimeout(() => {
      if (mountedRef.current) setStale(true);
    }, STALE_STREAM_MS);
  };

  const onEvent = (event: BatchStreamEvent) => {
    if (!mountedRef.current) return;
    setStale(false);
    armWatchdog();
    if (event.type === "batch") {
      setOrder((prev) => (prev.length > 0 ? prev : event.data.candidates.map((c) => c.runId)));
      setCandidates((prev) => {
        const next = { ...prev };
        for (const c of event.data.candidates) if (!next[c.runId]) next[c.runId] = emptyCandidate(c);
        return next;
      });
    } else if (event.type === "candidate") {
      const { runId, method, variantLabel, event: inner } = event.data;
      setCandidates((prev) => {
        const base = prev[runId] ?? emptyCandidate({ runId, method, variantLabel, status: "queued" });
        return { ...prev, [runId]: applyCandidateEvent(base, inner) };
      });
    } else if (event.type === "batchDone") {
      void qc.invalidateQueries({ queryKey: ["weight-batches"] });
      wsRef.current?.close(); // 终态即关（镜像竞技场）：服务端已在 batchDone 后关闭 socket，勿误判断连重连。
    }
  };

  // WS 订阅生命周期（batchId 就绪后）：连接即全量回放缓冲（含终态，R-06），断线由 ReconnectingWS 续连重放。
  useEffect(() => {
    if (batchId == null) return;
    mountedRef.current = true;
    const ws = weightBatchWS(batchId, onEvent, (open) => {
      if (mountedRef.current) setConnected(open);
    });
    wsRef.current = ws;
    ws.connect();
    armWatchdog();
    return () => {
      mountedRef.current = false;
      if (watchdogRef.current) clearTimeout(watchdogRef.current);
      ws.close();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [batchId]);

  // Esc 关闭（overlay 遮罩点击亦关闭，见下）。
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const launch = useMutation({
    mutationFn: async (config: ExploreConfig) => {
      const combos = planLocalCombos(config.pool, config.strategy, config.params, labelOf);
      const built = localPlan(combos, config.methods, config.pool.map((m) => m.factorKey), config.strategy, config.params);
      const body = buildExploreBody({
        pool: config.pool, strategy: config.strategy, params: config.params, methods: config.methods,
        thresholds: ctx.thresholds, horizon: EXPLORE_HORIZON, instId: ctx.instId, bar: ctx.bar,
        startTs: ctx.startTs, endTs: ctx.endTs, factorSetId: ctx.factorSetId,
      });
      const res = await weightExploreApi.start(body);
      return { res, combos, built };
    },
    onSuccess: ({ res, combos, built }) => {
      setLocalCombos(combos);
      setPlan(built);
      setBatchId(res.batchId);
    },
  });

  const cancelAll = useMutation({ mutationFn: () => weightBatchApi.cancel(batchId as number) });
  const del = useMutation({
    mutationFn: () => weightBatchApi.remove(batchId as number),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["weight-batches"] });
      onClose();
    },
  });

  const states: CandidateState[] = useMemo(() => order.map((id) => candidates[id]).filter(Boolean), [order, candidates]);
  const ranking = rankCandidates(states);
  const bestId = bestRunId(states);
  const settled = allSettled(states);
  const matrix = plan ? assembleMatrix(plan, states) : null;

  // 应用最佳组合（回流）：胜出子集成员 + 方法 + 权重经共享 buildTrainResult 单写入器 + modelId=null（P0-1/P1-1）。
  const applyBest = (runId: number) => {
    const winner = candidates[runId];
    const combo = localCombos.find((c) => c.id === winner?.variantLabel);
    if (!winner?.result || !combo) return;
    const target = applyBestTarget(
      { members: combo.members, method: winner.method },
      {
        thresholds: ctx.thresholds, name: ctx.name, instId: ctx.instId, bar: ctx.bar,
        startTs: ctx.startTs, endTs: ctx.endTs, horizon: EXPLORE_HORIZON,
        sharedNormWindow: ctx.thresholds.normWindow, factorSetId: ctx.factorSetId,
      },
    );
    setWeightTrainResult(buildTrainResult(winner.result, target, { modelId: null, runId }));
    onClose();
  };

  const phase = batchId == null ? "config" : settled && states.length > 0 ? "result" : "running";
  const launchError = launch.isError
    ? `启动失败（${codeLabel((launch.error as ApiRequestError)?.code)}）：${(launch.error as Error)?.message}`
    : null;

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="因子组合探索训练"
      className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-black/50 p-4 sm:p-8"
      onClick={onClose}
    >
      <div
        className="card my-auto flex w-full max-w-4xl flex-col gap-4 p-4"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between gap-2">
          <div className="flex items-center gap-2">
            <span className="text-sm font-semibold text-[var(--text)]">探索训练 · 因子组合探索</span>
            <PhaseBadge phase={phase} />
          </div>
          <Button variant="ghost" size="sm" onClick={onClose}>关闭</Button>
        </div>

        {phase === "config" && (
          <ExploreConfigPanel
            library={ctx.library}
            initialPool={ctx.members}
            onLaunch={(config) => launch.mutate(config)}
            launching={launch.isPending}
            error={launchError}
          />
        )}
        {phase === "running" && matrix && (
          <ExploreRunningPanel
            matrix={matrix}
            states={states}
            ranking={ranking}
            bestId={bestId}
            connected={connected}
            stale={stale}
            onCancelAll={() => cancelAll.mutate()}
            cancelling={cancelAll.isPending}
          />
        )}
        {phase === "result" && matrix && (
          <ExploreResultPanel
            matrix={matrix}
            states={states}
            ranking={ranking}
            bestId={bestId}
            onApplyBest={applyBest}
            onDelete={() => del.mutate()}
            deleting={del.isPending}
          />
        )}
      </div>
    </div>
  );
}

function PhaseBadge({ phase }: { phase: "config" | "running" | "result" }) {
  const map = { config: ["neutral", "配置"], running: ["accent", "训练中"], result: ["long", "已完成"] } as const;
  const [tone, label] = map[phase];
  return <Badge tone={tone}>{label}</Badge>;
}
