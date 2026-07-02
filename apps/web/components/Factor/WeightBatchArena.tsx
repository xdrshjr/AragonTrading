"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { Badge, Button } from "@/components/ui";
import ConfirmDialog from "@/components/ui/ConfirmDialog";
import { ApiRequestError } from "@/lib/api";
import { fmtTime } from "@/lib/format";
import {
  allSettled,
  applyCandidateEvent,
  batchDerivedStatus,
  bestRunId,
  emptyCandidate,
  rankCandidates,
} from "@/lib/weightBatch";
import { buildTrainResult, codeLabel } from "@/lib/weightModel";
import { weightBatchApi } from "@/lib/weightBatchApi";
import { weightBatchWS } from "@/lib/weightBatchWs";
import { weightModelApi } from "@/lib/weightModelApi";
import type { BatchStreamEvent, CandidateState, WeightBatchTarget } from "@/lib/weightBatchTypes";
import type { WeightTrainTarget } from "@/lib/weightModelTypes";
import { useAppStore } from "@/lib/store";
import CandidateCard from "./CandidateCard";
import WeightBatchCompare from "./WeightBatchCompare";

const STALE_STREAM_MS = 20_000 * 2;   // 进入订阅后超此无任何事件（含 ping）→ 提示「等待数据」（对齐 WeightTrainLive）

/**
 * 多模型训练竞技场：订阅批次 WS，一屏并行观测候选卡网格 + 实时排行榜 + 连接健康；全部终态后择优
 * （builder-origin 可回流组合 / 保留最佳并清理其余 / 查看任一候选完整结果）。
 *
 * WS 为候选态单一真源（连接即全量回放缓冲，含终态，R-06）；REST 详情提供 parentModelId / selectedRunId /
 * origin（择优后失效重取）。回流仅 builder-origin 渲染并生效（守 G-F1，杜绝劫持草稿）。
 */
export default function WeightBatchArena({
  batchId,
  applyTarget,
  onBack,
  onViewModel,
}: {
  batchId: number;
  applyTarget: WeightBatchTarget | null;
  onBack: () => void;
  onViewModel: (modelId: number, runId?: number) => void;
}) {
  const qc = useQueryClient();
  const setWeightTrainResult = useAppStore((s) => s.setWeightTrainResult);
  const setBoundModelId = useAppStore((s) => s.setBoundModelId);
  const setSection = useAppStore((s) => s.setFactorLabSection);

  const detail = useQuery({
    queryKey: ["weight-batch", batchId],
    queryFn: () => weightBatchApi.get(batchId),
    refetchInterval: (q) => (q.state.data?.status === "running" ? 5000 : false),
  });

  const [candidates, setCandidates] = useState<Record<number, CandidateState>>({});
  const [order, setOrder] = useState<number[]>([]);
  const [parentModelId, setParentModelId] = useState<number | null>(null);
  const [connected, setConnected] = useState(true);
  const [stale, setStale] = useState(false);
  const [pendingPrune, setPendingPrune] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  const mountedRef = useRef(true);
  const wsRef = useRef<ReturnType<typeof weightBatchWS> | null>(null);
  const watchdogRef = useRef<ReturnType<typeof setTimeout> | null>(null);

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
      setParentModelId(event.data.parentModelId);
      setOrder((prev) => (prev.length > 0 ? prev : event.data.candidates.map((c) => c.runId)));
      setCandidates((prev) => {
        const next = { ...prev };
        for (const c of event.data.candidates) {
          if (!next[c.runId]) next[c.runId] = emptyCandidate(c);
        }
        return next;
      });
    } else if (event.type === "candidate") {
      const { runId, method, variantLabel, event: inner } = event.data;
      setCandidates((prev) => {
        const base = prev[runId] ?? emptyCandidate({ runId, method, variantLabel, status: "queued" });
        return { ...prev, [runId]: applyCandidateEvent(base, inner) };
      });
    } else if (event.type === "batchDone") {
      void qc.invalidateQueries({ queryKey: ["weight-batch", batchId] });
      void qc.invalidateQueries({ queryKey: ["weight-models"] });
      wsRef.current?.close();   // 终态即关（镜像 WeightTrainLive.finishStream）：服务端已在 batchDone 后关闭 socket，
    }                          // 不主动 close 则 ReconnectingWS 会误判断连并每 ≤15s 重连重放，误显「连接中断」横幅。
  };

  useEffect(() => {
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

  const states: CandidateState[] = order.map((id) => candidates[id]).filter(Boolean);
  const ranking = rankCandidates(states);
  const settled = allSettled(states);
  const bestId = bestRunId(states);
  const resolvedParentId = parentModelId ?? detail.data?.parentModelId ?? null;
  const canApply = (detail.data?.origin ?? applyTarget?.origin) === "builder" && applyTarget != null;
  const selectedRunId = detail.data?.selectedRunId ?? null;
  const succeededCount = states.filter((s) => s.status === "succeeded").length;

  const select = useMutation({
    mutationFn: (runId: number) => weightBatchApi.select(batchId, runId),
    onSuccess: () => {
      setNotice("已择优标记该候选为最佳");
      void qc.invalidateQueries({ queryKey: ["weight-batch", batchId] });
    },
    onError: (err) => setNotice(`择优失败：${codeLabel((err as ApiRequestError)?.code)}`),
  });

  const cancelCandidate = useMutation({
    mutationFn: (runId: number) => weightBatchApi.cancelCandidate(batchId, runId),
    onError: (err) => setNotice(`取消失败：${codeLabel((err as ApiRequestError)?.code)}`),
  });

  const pruneOthers = useMutation({
    mutationFn: async (keepRunId: number) => {
      if (resolvedParentId == null) return;
      const victims = states.filter((s) => s.runId !== keepRunId && s.status === "succeeded");
      for (const v of victims) await weightModelApi.removeRun(resolvedParentId, v.runId);
    },
    onSuccess: () => {
      setNotice("已删除其余候选，仅保留最佳");
      void qc.invalidateQueries({ queryKey: ["weight-batch", batchId] });
      void qc.invalidateQueries({ queryKey: ["weight-models"] });
    },
    onError: (err) => setNotice(`清理失败：${codeLabel((err as ApiRequestError)?.code)}`),
  });

  // G-F1：仅 builder-origin 且持有回流载荷才可「应用到组合」；杜绝劫持用户当前草稿。
  const applyToBuilder = (runId: number) => {
    if (!canApply || !applyTarget || resolvedParentId == null) return;
    const winner = candidates[runId];
    if (!winner?.result) return;
    // R12：合成按 winner 的 WeightTrainTarget（method=winner 方法），buildTrainResult 从 event 取权重/指标。
    const target: WeightTrainTarget = {
      members: applyTarget.members,
      method: winner.method,
      config: { normWindow: applyTarget.thresholds.normWindow },
      thresholds: applyTarget.thresholds,
      horizon: applyTarget.horizon,
      instId: applyTarget.instId,
      bar: applyTarget.bar,
      startTs: applyTarget.startTs,
      endTs: applyTarget.endTs,
      name: applyTarget.name,
      origin: "builder",
      ...(applyTarget.factorSetId != null ? { factorSetId: applyTarget.factorSetId } : {}),
    };
    setWeightTrainResult(buildTrainResult(winner.result, target, { modelId: resolvedParentId, runId }));
    setBoundModelId(resolvedParentId);
    setSection("builder");
  };

  const derivedStatus = states.length > 0 ? batchDerivedStatus(states) : detail.data?.status ?? "running";
  const keepRunId = selectedRunId ?? bestId;

  return (
    <div className="flex flex-col gap-4 pb-6">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <Button variant="ghost" size="sm" onClick={onBack}>← 返回列表</Button>
          <span className="text-sm font-semibold text-[var(--text)]">多模型训练竞技场</span>
          <Badge tone={derivedStatus === "succeeded" ? "long" : derivedStatus === "running" ? "accent" : "neutral"}>
            {derivedStatus === "running"
              ? "训练中"
              : derivedStatus === "succeeded"
                ? "已完成"
                : derivedStatus === "cancelled"
                  ? "已取消"
                  : "全部失败"}
          </Badge>
          {detail.data && (
            <span className="text-[11px] text-[var(--muted)]">
              {detail.data.instId} · {detail.data.bar} · 前瞻 {detail.data.horizon} 根 · 区间{" "}
              {fmtTime(detail.data.startTs)} – {fmtTime(detail.data.endTs)}
            </span>
          )}
        </div>
        <span className="text-[11px] text-[var(--muted)]">
          {succeededCount}/{states.length} 成功
        </span>
      </div>

      {!connected && derivedStatus === "running" && (
        <p aria-live="polite" className="rounded-[10px] border border-[var(--accent)]/40 bg-[var(--accent-soft)]/40 px-3 py-2 text-[11px] font-medium text-[var(--accent)]">
          连接中断，正在重连…（训练在后端继续，恢复后自动续显）
        </p>
      )}
      {connected && stale && derivedStatus === "running" && (
        <p aria-live="polite" className="rounded-[10px] border border-[var(--border)] bg-[var(--surface-2)] px-3 py-2 text-[11px] text-[var(--muted)]">
          等待训练数据…（网络或后端较慢，请稍候）
        </p>
      )}

      {/* 择优动作区（全部终态且有成功候选才解禁）。 */}
      {settled && succeededCount > 0 && (
        <div className="card flex flex-wrap items-center gap-2 p-3">
          <span className="text-[11px] font-medium text-[var(--text)]">全部完成 · 可择优</span>
          {canApply && bestId != null && (
            <Button variant="primary" size="sm" onClick={() => applyToBuilder(bestId)}>
              应用最佳到组合并返回
            </Button>
          )}
          {resolvedParentId != null && bestId != null && (
            <Button variant="default" size="sm" onClick={() => onViewModel(resolvedParentId, bestId)}>
              查看最佳候选结果
            </Button>
          )}
          {keepRunId != null && succeededCount > 1 && (
            <Button variant="ghost" size="sm" onClick={() => setPendingPrune(true)}>
              只保留最佳，删除其余候选
            </Button>
          )}
        </div>
      )}
      {settled && succeededCount === 0 && (
        <p role="alert" className="rounded-[10px] border border-[var(--short)]/40 bg-[var(--short)]/10 px-3 py-2 text-[11px] font-medium text-[var(--short)]">
          没有任何候选成功训练，无法择优；请检查成员 / 区间后重新发起。
        </p>
      )}
      {notice && <p role="status" aria-live="polite" className="text-[11px] font-medium text-[var(--accent)]">{notice}</p>}

      {/* 排行榜（按样本外 IC）+ 叠加曲线。 */}
      {states.length > 0 && (
        <WeightBatchCompare
          ranking={ranking}
          states={states}
          selectedRunId={selectedRunId}
          canApply={canApply}
          onView={(runId) => resolvedParentId != null && onViewModel(resolvedParentId, runId)}
          onSelect={(runId) => select.mutate(runId)}
          onApply={applyToBuilder}
        />
      )}

      {/* 候选卡网格（实时进度 / 曲线 / 权重 / 单候选取消）。 */}
      <div className="grid grid-cols-1 gap-3 xl:grid-cols-2">
        {states.map((s) => (
          <CandidateCard
            key={s.runId}
            state={s}
            isBest={s.runId === bestId}
            onCancel={
              s.status === "running" || s.status === "queued"
                ? () => cancelCandidate.mutate(s.runId)
                : undefined
            }
          />
        ))}
      </div>

      {states.length === 0 && (
        <p className="py-8 text-center text-[11px] text-[var(--muted)]">加载候选…</p>
      )}

      <ConfirmDialog
        open={pendingPrune}
        title="只保留最佳候选"
        message="将删除本批次其余成功候选的训练记录（仅保留最佳），且无法恢复，确定继续？"
        confirmText="删除其余"
        onConfirm={() => {
          if (keepRunId != null) pruneOthers.mutate(keepRunId);
          setPendingPrune(false);
        }}
        onCancel={() => setPendingPrune(false)}
      />
    </div>
  );
}
