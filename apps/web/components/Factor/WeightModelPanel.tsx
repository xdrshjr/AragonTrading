"use client";

import { useEffect, useRef, useState } from "react";
import { modelToTrainTarget } from "@/lib/weightModel";
import type { WeightModel, WeightTrainTarget } from "@/lib/weightModelTypes";
import type { WeightBatchTarget } from "@/lib/weightBatchTypes";
import { useAppStore } from "@/lib/store";
import { useWorkbench } from "./FactorLab";
import WeightBatchArena from "./WeightBatchArena";
import WeightBatchLauncher from "./WeightBatchLauncher";
import WeightModelDetail from "./WeightModelDetail";
import WeightModelEditor from "./WeightModelEditor";
import WeightModelList from "./WeightModelList";
import WeightTrainLive from "./WeightTrainLive";

type View =
  | { kind: "list" }
  | { kind: "detail"; modelId: number; autoBacktestRunId?: number }   // autoBacktestRunId：卡片「回测」进入时自动回测该记录
  | { kind: "live"; target: WeightTrainTarget; seq: number }   // G-F10：seq 作 key 隔离每次实时训练实例
  | { kind: "editor"; editingId: number | null }
  | { kind: "batchLaunch"; target: WeightBatchTarget | null; seq: number }   // 多模型训练启动器（target=null 为引导态）
  | { kind: "batchArena"; batchId: number; applyTarget: WeightBatchTarget | null; seq: number };   // 竞技场（applyTarget 仅 builder-origin 回流）

/**
 * 「权重模型」section 根：按内部视图态在 列表 / 详情 / 实时训练 / 编辑 / 多模型训练（启动器 + 竞技场）间路由，
 * 并**消费一次性** store.weightTrainTarget（单次训练）与 store.weightBatchTarget（多模型训练）——由 FactorSetBuilder
 * 点「训练权重」/「多模型训练」写入，切到对应视图（消费模式严格照 FactorSetBuilder 消费 factorEditTarget）。
 */
export default function WeightModelPanel() {
  const wb = useWorkbench();
  const weightTrainTarget = useAppStore((s) => s.weightTrainTarget);
  const setWeightTrainTarget = useAppStore((s) => s.setWeightTrainTarget);
  const weightBatchTarget = useAppStore((s) => s.weightBatchTarget);
  const setWeightBatchTarget = useAppStore((s) => s.setWeightBatchTarget);
  const setSection = useAppStore((s) => s.setFactorLabSection);
  const [view, setView] = useState<View>({ kind: "list" });
  const consumedRef = useRef<WeightTrainTarget | null>(null);
  const consumedBatchRef = useRef<WeightBatchTarget | null>(null);
  const liveSeq = useRef(0);   // G-F10：每次进入实时训练 / 竞技场自增，作子组件 key（按 target 隔离实例）

  // 消费单次训练交接载荷：挂载 / 载荷变化时第一时间清空 store 并切到实时训练（一次性 + 防重入）。
  useEffect(() => {
    const target = weightTrainTarget;
    if (!target || consumedRef.current === target) return;
    consumedRef.current = target;
    setWeightTrainTarget(null);
    setView({ kind: "live", target, seq: ++liveSeq.current });
  }, [weightTrainTarget, setWeightTrainTarget]);

  // 消费多模型训练交接载荷：切到竞技场启动器（同上范式）。
  useEffect(() => {
    const target = weightBatchTarget;
    if (!target || consumedBatchRef.current === target) return;
    consumedBatchRef.current = target;
    setWeightBatchTarget(null);
    setView({ kind: "batchLaunch", target, seq: ++liveSeq.current });
  }, [weightBatchTarget, setWeightBatchTarget]);

  const range = { instId: wb.instId, bar: wb.bar, startTs: wb.startTs, endTs: wb.endTs };
  const enterTraining = (model: WeightModel) =>
    setView({ kind: "live", target: modelToTrainTarget(model, range), seq: ++liveSeq.current });

  // H-F8：视图切换时把焦点迁移到内容容器（tabIndex=-1），便于键盘 / 读屏定位新视图（参照 FactorLab headingRef）。
  const containerRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    containerRef.current?.focus();
  }, [view.kind]);

  const content =
    view.kind === "live" ? (
      <WeightTrainLive
        key={view.seq}
        target={view.target}
        onBack={() => setView({ kind: "list" })}
        onViewModel={(modelId) => setView({ kind: "detail", modelId })}
      />
    ) : view.kind === "batchLaunch" ? (
      <WeightBatchLauncher
        key={view.seq}
        target={view.target}
        onLaunched={(res, target) =>
          setView({
            kind: "batchArena",
            batchId: res.batchId,
            applyTarget: target.origin === "builder" ? target : null,
            seq: ++liveSeq.current,
          })
        }
        onBack={() => setView({ kind: "list" })}
        onGoBuilder={() => setSection("builder")}
      />
    ) : view.kind === "batchArena" ? (
      <WeightBatchArena
        key={view.seq}
        batchId={view.batchId}
        applyTarget={view.applyTarget}
        onBack={() => setView({ kind: "list" })}
        onViewModel={(modelId, runId) =>
          setView({ kind: "detail", modelId, autoBacktestRunId: runId })
        }
      />
    ) : view.kind === "detail" ? (
      <WeightModelDetail
        modelId={view.modelId}
        autoBacktestRunId={view.autoBacktestRunId}
        onBack={() => setView({ kind: "list" })}
        onTrain={enterTraining}
        trainRange={{ startTs: range.startTs, endTs: range.endTs }}
      />
    ) : view.kind === "editor" ? (
      <WeightModelEditor
        editingId={view.editingId}
        onSaved={() => setView({ kind: "list" })}
        onCancel={() => setView({ kind: "list" })}
      />
    ) : (
      <WeightModelList
        onTrain={enterTraining}
        onView={(model) => setView({ kind: "detail", modelId: model.id })}
        onBacktest={(model) =>
          setView({ kind: "detail", modelId: model.id, autoBacktestRunId: model.latestRunId ?? undefined })
        }
        onEdit={(model) => setView({ kind: "editor", editingId: model.id })}
        onNew={() => setView({ kind: "editor", editingId: null })}
        onNewBatch={() => setView({ kind: "batchLaunch", target: null, seq: ++liveSeq.current })}
        onOpenBatch={(batchId) =>
          setView({ kind: "batchArena", batchId, applyTarget: null, seq: ++liveSeq.current })
        }
      />
    );

  return (
    <div ref={containerRef} tabIndex={-1} className="outline-none">
      {content}
    </div>
  );
}
