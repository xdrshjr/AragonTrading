"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { Badge, Button } from "@/components/ui";
import { ApiRequestError } from "@/lib/api";
import { formatIc } from "@/lib/factor";
import {
  buildTrainResult,
  formatLoss,
  methodLabel,
  phaseLabel,
  trainStartBody,
} from "@/lib/weightModel";
import { weightModelApi } from "@/lib/weightModelApi";
import type {
  TrainCurvePoint,
  TrainPhase,
  TrainResultEvent,
  TrainStreamEvent,
  WeightTrainTarget,
} from "@/lib/weightModelTypes";
import { weightTrainWS } from "@/lib/weightTrainWs";
import { useAppStore } from "@/lib/store";
import TrainingCurveChart from "./TrainingCurveChart";
import WeightBars from "./WeightBars";

type LiveStatus = "starting" | "streaming" | "cancelling" | "succeeded" | "failed" | "cancelled";

interface Handles {
  jobId: string;
  runId: number;
  modelId: number;
}

/**
 * 实时训练视图：挂载即 POST /weight-models/train，开 WS 流式渲染阶段进度、loss/IC 曲线、收敛中的权重条；
 * 训练完成可「应用到组合并返回」(写回流载荷 + 跳 builder) 或「查看模型详情」。取消走协作式 DELETE（作业续跑落库）。
 */
export default function WeightTrainLive({
  target,
  onBack,
  onViewModel,
}: {
  target: WeightTrainTarget;
  onBack: () => void;
  onViewModel: (modelId: number) => void;
}) {
  const qc = useQueryClient();
  const setWeightTrainResult = useAppStore((s) => s.setWeightTrainResult);
  const setBoundModelId = useAppStore((s) => s.setBoundModelId);
  const setSection = useAppStore((s) => s.setFactorLabSection);

  const [status, setStatus] = useState<LiveStatus>("starting");
  const [phase, setPhase] = useState<{ phase: TrainPhase; pct: number } | null>(null);
  const [curve, setCurve] = useState<TrainCurvePoint[]>([]);
  const [liveWeights, setLiveWeights] = useState<Record<string, number> | null>(null);
  const [result, setResult] = useState<TrainResultEvent | null>(null);
  const [error, setError] = useState<{ code: string; message: string } | null>(null);
  const handlesRef = useRef<Handles | null>(null);
  const wsRef = useRef<ReturnType<typeof weightTrainWS> | null>(null);
  const startedRef = useRef(false);

  const start = useMutation({
    mutationFn: () => weightModelApi.train(trainStartBody(target)),
    onSuccess: (ids) => {
      handlesRef.current = ids;
      setBoundModelId(ids.modelId); // R-02：同会话反复训练复用同一模型
      setStatus("streaming");
      openStream(ids.jobId);
    },
    onError: (err) => {
      setStatus("failed");
      setError({ code: (err as ApiRequestError)?.code ?? "ERROR", message: (err as Error)?.message ?? "训练启动失败" });
    },
  });

  // 挂载即启动一次（防重入，模式同 builder 消费交接态）。
  useEffect(() => {
    if (startedRef.current) return;
    startedRef.current = true;
    start.mutate();
    return () => wsRef.current?.close();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const onEvent = (event: TrainStreamEvent) => {
    if (event.type === "phase") setPhase(event.data);
    else if (event.type === "curve") {
      const { weights, ...point } = event.data;
      setCurve((prev) => upsertCurvePoint(prev, point));
      if (weights) setLiveWeights(weights);
    } else if (event.type === "result") {
      setResult(event.data);
      setLiveWeights(event.data.weights);
    } else if (event.type === "done") finishStream("succeeded");
    else if (event.type === "cancelled") finishStream("cancelled");
    else if (event.type === "error") {
      setError({ code: event.data.code, message: event.data.message });
      finishStream("failed");
    }
  };

  const finishStream = (next: LiveStatus) => {
    setStatus(next);
    wsRef.current?.close();
    void qc.invalidateQueries({ queryKey: ["weight-models"] });
  };

  const openStream = (jobId: string) => {
    const ws = weightTrainWS(jobId, onEvent);
    wsRef.current = ws;
    ws.connect();
  };

  const cancel = useMutation({
    mutationFn: () => weightModelApi.cancel(handlesRef.current?.jobId ?? ""),
    onMutate: () => setStatus("cancelling"),
  });

  const applyToBuilder = () => {
    const ids = handlesRef.current;
    if (!result || !ids) return;
    setWeightTrainResult(buildTrainResult(result, target, ids));
    setSection("builder");
  };

  const pct = phase?.pct ?? (status === "succeeded" ? 100 : 0);
  const running = status === "starting" || status === "streaming" || status === "cancelling";

  return (
    <div className="flex flex-col gap-4 pb-6">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <Button variant="ghost" size="sm" onClick={onBack}>← 返回列表</Button>
          <span className="text-sm font-semibold text-[var(--text)]">实时训练</span>
          <Badge tone="accent">{methodLabel(target.method)}</Badge>
          <span className="text-[11px] text-[var(--muted)]">{target.instId} · {target.bar} · {target.members.length} 成员</span>
        </div>
        {running && handlesRef.current && (
          <Button variant="ghost" size="sm" disabled={status === "cancelling"} onClick={() => cancel.mutate()}>
            {status === "cancelling" ? "取消中..." : "取消训练"}
          </Button>
        )}
      </div>

      <ProgressBar status={status} phase={phase?.phase ?? null} pct={pct} />

      {error && (
        <p className="rounded-[10px] border border-[var(--short)]/40 bg-[var(--short)]/10 px-3 py-2 text-[11px] font-medium text-[var(--short)]">
          训练{status === "failed" ? "失败" : "异常"}（{error.code}）：{error.message}
        </p>
      )}

      <div className="card flex flex-col gap-2 p-3">
        <span className="text-[11px] font-medium text-[var(--muted)]">扩样本学习曲线（真实留出验证段损失 / IC，绝不伪造）</span>
        <TrainingCurveChart curve={curve} />
      </div>

      {liveWeights && Object.keys(liveWeights).length > 0 && (
        <div className="card flex flex-col gap-2 p-3">
          <div className="flex items-center gap-2">
            <span className="text-[11px] font-medium text-[var(--muted)]">{status === "succeeded" ? "最终权重" : "收敛中的权重"}</span>
            {result?.trainerVersion && <Badge tone="neutral">{result.trainerVersion}</Badge>}
          </div>
          <WeightBars weights={liveWeights} />
        </div>
      )}

      {result && status === "succeeded" && (
        <ResultSummary result={result} />
      )}

      <div className="flex flex-wrap items-center gap-2">
        <Button
          variant="primary"
          disabled={status !== "succeeded" || !result}
          title={status === "succeeded" ? "把训练权重应用到组合并返回" : "训练完成后可用"}
          onClick={applyToBuilder}
        >
          应用到组合并返回
        </Button>
        {handlesRef.current && (
          <Button variant="default" disabled={running} onClick={() => onViewModel(handlesRef.current!.modelId)}>
            查看模型详情
          </Button>
        )}
      </div>
    </div>
  );
}

function ProgressBar({ status, phase, pct }: { status: LiveStatus; phase: TrainPhase | null; pct: number }) {
  const label =
    status === "succeeded" ? "训练完成"
    : status === "failed" ? "训练失败"
    : status === "cancelled" ? "已取消"
    : phase ? phaseLabel(phase) : "启动中...";
  const tone = status === "failed" ? "var(--short)" : status === "succeeded" ? "var(--long)" : "var(--accent)";
  return (
    <div className="flex flex-col gap-1">
      <div className="flex items-center justify-between text-[11px] text-[var(--muted)]">
        <span>{label}</span>
        <span className="tabular">{Math.round(pct)}%</span>
      </div>
      <div className="h-1.5 overflow-hidden rounded-full bg-[var(--surface-2)]">
        <div className="h-full rounded-full transition-all duration-300 ease-smooth" style={{ width: `${pct}%`, background: tone }} />
      </div>
    </div>
  );
}

function ResultSummary({ result }: { result: TrainResultEvent }) {
  const m = result.metrics;
  return (
    <div className="card flex flex-wrap items-center gap-2 p-3">
      <span className="text-sm font-semibold text-[var(--text)]">训练结果</span>
      <Badge tone="neutral">样本内 IC {formatIc(m.icIn)}</Badge>
      <Badge tone="neutral">样本外 IC {formatIc(m.icOut)}</Badge>
      <span className="text-[11px] text-[var(--muted)]">n_train {m.nTrain} · n_valid {m.nValid}</span>
      {result.note && <span className="text-[11px] text-[var(--muted)]">{result.note}</span>}
    </div>
  );
}

// 曲线点按 step upsert（重连回放时同 step 覆盖，避免重复堆叠）。
function upsertCurvePoint(prev: TrainCurvePoint[], point: TrainCurvePoint): TrainCurvePoint[] {
  const idx = prev.findIndex((p) => p.step === point.step);
  if (idx === -1) return [...prev, point].sort((a, b) => a.step - b.step);
  const next = [...prev];
  next[idx] = point;
  return next;
}

export { formatLoss };
