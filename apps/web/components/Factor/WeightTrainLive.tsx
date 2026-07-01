"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { Badge, Button } from "@/components/ui";
import { ApiRequestError } from "@/lib/api";
import { formatIc } from "@/lib/factor";
import { fmtTime } from "@/lib/format";
import {
  buildTrainResult,
  codeLabel,
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

const STALE_STREAM_MS = 20_000 * 2;   // 进入 streaming 后超此无任何事件（含 ping）→ 提示「等待训练数据」（H-F3）
const CANCEL_TIMEOUT_MS = 10_000;     // 发起取消后未达终态超此 → 解禁按钮 + 提示可重试（H-F3）

/**
 * 实时训练视图：挂载即 POST /weight-models/train，开 WS 流式渲染阶段进度、loss/IC 曲线、收敛中的权重条；
 * 训练完成可「应用到组合并返回」(写回流载荷 + 跳 builder) 或「查看模型详情」。取消走协作式 DELETE（作业续跑落库）。
 *
 * 加固：H-F2 仅 builder 发起才记忆 boundModelId（杜绝跨上下文污染）；H-F3 连接健康可见 + 取消超时/错误恢复；
 * H-F4 mountedRef 卸载守卫，杜绝卸载后 openStream / setState / 泄漏 socket 与定时器。
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
  const statusRef = useRef<LiveStatus>("starting");   // G-F9：取消超时守卫读最新 status（去闭包陈旧依赖）
  statusRef.current = status;
  const [phase, setPhase] = useState<{ phase: TrainPhase; pct: number } | null>(null);
  const [curve, setCurve] = useState<TrainCurvePoint[]>([]);
  const [liveWeights, setLiveWeights] = useState<Record<string, number> | null>(null);
  const [result, setResult] = useState<TrainResultEvent | null>(null);
  const [error, setError] = useState<{ code: string; message: string } | null>(null);
  const [connected, setConnected] = useState(true);   // H-F3：WS 连接健康
  const [stale, setStale] = useState(false);          // H-F3：陈旧流看门狗
  const handlesRef = useRef<Handles | null>(null);
  const wsRef = useRef<ReturnType<typeof weightTrainWS> | null>(null);
  const startedRef = useRef(false);
  const mountedRef = useRef(true);                     // H-F4：卸载守卫
  const watchdogRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const cancelTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const clearTimers = () => {
    if (watchdogRef.current) clearTimeout(watchdogRef.current);
    if (cancelTimerRef.current) clearTimeout(cancelTimerRef.current);
    watchdogRef.current = null;
    cancelTimerRef.current = null;
  };
  const armWatchdog = () => {
    if (watchdogRef.current) clearTimeout(watchdogRef.current);
    watchdogRef.current = setTimeout(() => {
      if (mountedRef.current) setStale(true);
    }, STALE_STREAM_MS);
  };

  const start = useMutation({
    mutationFn: () => weightModelApi.train(trainStartBody(target)),
    onSuccess: (ids) => {
      if (!mountedRef.current) return;   // H-F4：卸载后不再开流 / setState（杜绝泄漏 socket）
      handlesRef.current = ids;
      if (target.origin === "builder") setBoundModelId(ids.modelId);   // H-F2：仅 builder 会话内复用记忆
      setStatus("streaming");
      openStream(ids.jobId);
    },
    onError: (err) => {
      if (!mountedRef.current) return;
      setStatus("failed");
      setError({ code: (err as ApiRequestError)?.code ?? "ERROR", message: (err as Error)?.message ?? "训练启动失败" });
    },
  });

  // 挂载即启动一次（防重入，模式同 builder 消费交接态）；卸载置 mounted=false + 清 socket / 定时器（H-F4）。
  useEffect(() => {
    mountedRef.current = true;
    if (startedRef.current) return;
    startedRef.current = true;
    start.mutate();
    return () => {
      mountedRef.current = false;
      clearTimers();
      wsRef.current?.close();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const onEvent = (event: TrainStreamEvent) => {
    if (!mountedRef.current) return;   // H-F4
    setStale(false);
    armWatchdog();                      // 任一事件（含 ping）即重置看门狗
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
    if (!mountedRef.current) return;
    clearTimers();
    setStatus(next);
    setStale(false);
    wsRef.current?.close();
    void qc.invalidateQueries({ queryKey: ["weight-models"] });
  };

  const openStream = (jobId: string) => {
    const ws = weightTrainWS(jobId, onEvent, (open) => {
      if (mountedRef.current) setConnected(open);   // H-F3/H-F4：连接健康可见，卸载后不 setState
    });
    wsRef.current = ws;
    ws.connect();
    armWatchdog();
  };

  const cancel = useMutation({
    mutationFn: () => weightModelApi.cancel(handlesRef.current?.jobId ?? ""),
    onMutate: () => {
      setStatus("cancelling");
      setError(null);
      if (cancelTimerRef.current) clearTimeout(cancelTimerRef.current);
      cancelTimerRef.current = setTimeout(() => {   // H-F3/G-F9：超时未达终态 → 解禁可重试（读 statusRef 去闭包陈旧）
        const s = statusRef.current;
        if (mountedRef.current && s !== "succeeded" && s !== "failed" && s !== "cancelled") {
          setStatus("streaming");
          setError({ code: "CANCEL_TIMEOUT", message: "取消未确认，可重试或等待训练完成" });
        }
      }, CANCEL_TIMEOUT_MS);
    },
    onError: () => {   // H-F3：DELETE 失败 → 回 streaming + 提示可重试
      if (!mountedRef.current) return;
      if (cancelTimerRef.current) clearTimeout(cancelTimerRef.current);
      setStatus("streaming");
      setError({ code: "CANCEL_FAILED", message: "取消请求失败，可重试" });
    },
  });

  // G-F1：仅**组合搭建发起**的训练（origin==="builder"）可回流；model-origin（列表 / 详情发起）训练完成后
  // 绝不回流——否则会静默覆盖用户当前组合搭建草稿并把 builder 劫持进编辑一个用户从未打开过的组合。
  const canApply = target.origin === "builder";
  const applyToBuilder = () => {
    if (!canApply) return;                       // 防御：model-origin 绝不回流
    const ids = handlesRef.current;
    if (!result || !ids) return;
    setWeightTrainResult(buildTrainResult(result, target, ids));
    setSection("builder");
  };

  const pct = phase?.pct ?? (status === "succeeded" ? 100 : 0);
  const running = status === "starting" || status === "streaming" || status === "cancelling";
  const streamingLike = status === "streaming" || status === "cancelling";

  return (
    <div className="flex flex-col gap-4 pb-6">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <Button variant="ghost" size="sm" onClick={onBack}>← 返回列表</Button>
          <span className="text-sm font-semibold text-[var(--text)]">实时训练</span>
          <Badge tone="accent">{methodLabel(target.method)}</Badge>
          <span className="text-[11px] text-[var(--muted)]">{target.instId} · {target.bar} · {target.members.length} 成员</span>
          {/* G-F7：呈现训练时间窗，便于用户核对日期段（窗口取自工作台顶栏，原先静默不显）。 */}
          <span className="text-[11px] text-[var(--muted)]">区间 {fmtTime(target.startTs)} – {fmtTime(target.endTs)}</span>
        </div>
        {running && handlesRef.current && (
          <Button variant="ghost" size="sm" disabled={status === "cancelling"} onClick={() => cancel.mutate()}>
            {status === "cancelling" ? "取消中..." : "取消训练"}
          </Button>
        )}
      </div>

      <ProgressBar status={status} phase={phase?.phase ?? null} pct={pct} />

      {streamingLike && !connected && (
        <p aria-live="polite" className="rounded-[10px] border border-[var(--accent)]/40 bg-[var(--accent-soft)]/40 px-3 py-2 text-[11px] font-medium text-[var(--accent)]">
          连接中断，正在重连…（训练在后端继续，恢复后自动续显）
        </p>
      )}
      {streamingLike && connected && stale && (
        <p aria-live="polite" className="rounded-[10px] border border-[var(--border)] bg-[var(--surface-2)] px-3 py-2 text-[11px] text-[var(--muted)]">
          等待训练数据…（网络或后端较慢，请稍候）
        </p>
      )}

      {error && (
        // G-F5：role=alert 让读屏在失败 / 断连时即时播报；G-F6：错误码经 codeLabel 友好呈现，原始码作 title。
        <p role="alert" title={error.code} className="rounded-[10px] border border-[var(--short)]/40 bg-[var(--short)]/10 px-3 py-2 text-[11px] font-medium text-[var(--short)]">
          训练{status === "failed" ? "失败" : "异常"}（{codeLabel(error.code)}）：{error.message}
        </p>
      )}

      <div className="card flex flex-col gap-2 p-3">
        <span className="text-[11px] font-medium text-[var(--muted)]">扩样本学习曲线（真实留出验证段损失 / IC，绝不伪造）</span>
        <TrainingCurveChart curve={curve} />
      </div>

      {liveWeights && Object.keys(liveWeights).length > 0 && (
        <div className="card flex flex-col gap-2 p-3">
          <div className="flex items-center gap-2">
            {/* G-F9：失败后不再误标「收敛中的权重」（拟合并未收敛）；succeeded 才是最终权重。 */}
            <span className="text-[11px] font-medium text-[var(--muted)]">{weightsLabel(status)}</span>
            {result?.trainerVersion && <Badge tone="neutral">{result.trainerVersion}</Badge>}
          </div>
          <WeightBars weights={liveWeights} />
        </div>
      )}

      {result && status === "succeeded" && (
        <ResultSummary result={result} />
      )}

      <div className="flex flex-wrap items-center gap-2">
        {/* G-F1：仅 builder-origin 训练渲染「应用到组合并返回」；model-origin 只提供「查看模型详情」，杜绝劫持草稿。 */}
        {canApply && (
          <Button
            variant="primary"
            disabled={status !== "succeeded" || !result}
            title={status === "succeeded" ? "把训练权重应用到组合并返回" : "训练完成后可用"}
            onClick={applyToBuilder}
          >
            应用到组合并返回
          </Button>
        )}
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
    <div className="flex flex-col gap-1" role="status" aria-live="polite">
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
    <div className="card flex flex-wrap items-center gap-2 p-3" role="status" aria-live="polite">
      <span className="text-sm font-semibold text-[var(--text)]">训练结果</span>
      <Badge tone="neutral">样本内 IC {formatIc(m.icIn)}</Badge>
      <Badge tone="neutral">样本外 IC {formatIc(m.icOut)}</Badge>
      <span className="text-[11px] text-[var(--muted)]">训练样本 {m.nTrain} · 验证样本 {m.nValid}</span>
      {result.note && <span className="text-[11px] text-[var(--muted)]">{result.note}</span>}
    </div>
  );
}

// 权重卡标签（G-F9）：succeeded=最终权重；failed=失败前最后权重（绝不误称「收敛中」）；其余=收敛中的权重。
function weightsLabel(status: LiveStatus): string {
  if (status === "succeeded") return "最终权重";
  if (status === "failed") return "失败前最后权重";
  if (status === "cancelled") return "取消前最后权重";
  return "收敛中的权重";
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
