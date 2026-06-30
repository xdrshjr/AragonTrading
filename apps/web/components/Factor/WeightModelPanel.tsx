"use client";

import { useEffect, useRef, useState } from "react";
import { modelToTrainTarget } from "@/lib/weightModel";
import type { WeightModel, WeightTrainTarget } from "@/lib/weightModelTypes";
import { useAppStore } from "@/lib/store";
import { useWorkbench } from "./FactorLab";
import WeightModelDetail from "./WeightModelDetail";
import WeightModelEditor from "./WeightModelEditor";
import WeightModelList from "./WeightModelList";
import WeightTrainLive from "./WeightTrainLive";

type View =
  | { kind: "list" }
  | { kind: "detail"; modelId: number }
  | { kind: "live"; target: WeightTrainTarget }
  | { kind: "editor"; editingId: number | null };

/**
 * 「权重模型」section 根：按内部视图态在 列表 / 详情 / 实时训练 / 编辑 间路由，并**消费一次性** store.weightTrainTarget
 * （FactorSetBuilder 点「训练权重」写入），切到实时训练视图（消费模式严格照 FactorSetBuilder 消费 factorEditTarget）。
 */
export default function WeightModelPanel() {
  const wb = useWorkbench();
  const weightTrainTarget = useAppStore((s) => s.weightTrainTarget);
  const setWeightTrainTarget = useAppStore((s) => s.setWeightTrainTarget);
  const [view, setView] = useState<View>({ kind: "list" });
  const consumedRef = useRef<WeightTrainTarget | null>(null);

  // 消费交接载荷：挂载 / 载荷变化时第一时间清空 store 并切到实时训练（一次性 + 防重入，模式同 factorEditTarget）。
  useEffect(() => {
    const target = weightTrainTarget;
    if (!target || consumedRef.current === target) return;
    consumedRef.current = target;
    setWeightTrainTarget(null);
    setView({ kind: "live", target });
  }, [weightTrainTarget, setWeightTrainTarget]);

  const range = { instId: wb.instId, bar: wb.bar, startTs: wb.startTs, endTs: wb.endTs };
  const enterTraining = (model: WeightModel) =>
    setView({ kind: "live", target: modelToTrainTarget(model, range) });

  // H-F8：视图切换时把焦点迁移到内容容器（tabIndex=-1），便于键盘 / 读屏定位新视图（参照 FactorLab headingRef）。
  const containerRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    containerRef.current?.focus();
  }, [view.kind]);

  const content =
    view.kind === "live" ? (
      <WeightTrainLive
        target={view.target}
        onBack={() => setView({ kind: "list" })}
        onViewModel={(modelId) => setView({ kind: "detail", modelId })}
      />
    ) : view.kind === "detail" ? (
      <WeightModelDetail
        modelId={view.modelId}
        onBack={() => setView({ kind: "list" })}
        onTrain={enterTraining}
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
        onEdit={(model) => setView({ kind: "editor", editingId: model.id })}
        onNew={() => setView({ kind: "editor", editingId: null })}
      />
    );

  return (
    <div ref={containerRef} tabIndex={-1} className="outline-none">
      {content}
    </div>
  );
}
