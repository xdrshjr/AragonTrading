"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { Badge, Button } from "@/components/ui";
import { formatIc } from "@/lib/factor";
import { fmtTime } from "@/lib/format";
import { methodLabel, runStatusLabel, runStatusTone, sortModelsByUpdated } from "@/lib/weightModel";
import { weightModelApi } from "@/lib/weightModelApi";
import type { WeightModel } from "@/lib/weightModelTypes";

/**
 * 「权重模型」列表 + CRUD（镜像 FactorSetList shell）：卡片呈现名称 / 标的·周期 / 方法 / 成员数 /
 * 最佳验证 IC / 最近训练态 / 启用；支持训练（进实时视图）/ 查看（详情）/ 编辑 / 删除（二次确认）/ 新建。
 */
export default function WeightModelList({
  onTrain,
  onView,
  onEdit,
  onNew,
}: {
  onTrain: (model: WeightModel) => void;
  onView: (model: WeightModel) => void;
  onEdit: (model: WeightModel) => void;
  onNew: () => void;
}) {
  const qc = useQueryClient();
  const models = useQuery({ queryKey: ["weight-models"], queryFn: () => weightModelApi.list() });
  const remove = useMutation({
    mutationFn: (id: number) => weightModelApi.remove(id),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["weight-models"] }),
  });

  if (models.isPending) {
    return <p className="py-8 text-center text-[11px] text-[var(--muted)]">加载权重模型...</p>;
  }
  if (models.isError) {
    return (
      <div className="flex flex-col items-center gap-2 py-8">
        <p className="text-[11px] text-[var(--short)]">加载权重模型失败：{(models.error as Error)?.message}</p>
        <Button variant="default" size="sm" onClick={() => models.refetch()}>重试</Button>
      </div>
    );
  }

  const items = sortModelsByUpdated(models.data ?? []);
  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center justify-between">
        <div className="flex flex-col">
          <span className="text-sm font-semibold text-[var(--text)]">权重模型</span>
          <span className="text-[11px] text-[var(--muted)]">管理模型并实时观测每次训练的 loss / IC 曲线</span>
        </div>
        <Button variant="primary" size="sm" onClick={onNew}>新建模型</Button>
      </div>

      {items.length === 0 ? (
        <div className="card flex flex-col items-center gap-3 p-8 text-center">
          <span className="text-sm font-medium text-[var(--text)]">还没有权重模型</span>
          <span className="text-[11px] text-[var(--muted)]">
            从「权重训练与组合」点「训练权重」会自动建模型并跳到这里实时训练；也可直接「新建模型」。
          </span>
          <Button variant="primary" size="sm" onClick={onNew}>新建模型</Button>
        </div>
      ) : (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-3">
          {items.map((model) => (
            <ModelCard
              key={model.id}
              model={model}
              onTrain={() => onTrain(model)}
              onView={() => onView(model)}
              onEdit={() => onEdit(model)}
              onDelete={() => {
                if (window.confirm(`确定删除模型「${model.name}」？将一并删除其全部训练记录。`)) remove.mutate(model.id);
              }}
            />
          ))}
        </div>
      )}
      {remove.isError && (
        <p className="text-[11px] text-[var(--short)]">删除失败：{(remove.error as Error)?.message}</p>
      )}
    </div>
  );
}

function ModelCard({
  model,
  onTrain,
  onView,
  onEdit,
  onDelete,
}: {
  model: WeightModel;
  onTrain: () => void;
  onView: () => void;
  onEdit: () => void;
  onDelete: () => void;
}) {
  const latest = model.latestRun;
  return (
    <div className={clsx("card flex flex-col gap-2 p-3 transition-colors hover:border-[var(--accent-soft)]")}>
      <button type="button" onClick={onView} className="flex flex-col gap-1 text-left">
        <div className="flex items-center gap-2">
          <span className="truncate text-sm font-medium text-[var(--text)]">{model.name}</span>
          <Badge tone="accent">{methodLabel(model.method)}</Badge>
          {!model.enabled && <Badge tone="neutral">已停用</Badge>}
        </div>
        <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5 text-[11px] text-[var(--muted)]">
          <span>{model.instId} · {model.bar}</span>
          <span>{model.members.length} 成员</span>
          <span>最佳验证 IC <span className="text-[var(--text)]">{formatIc(model.bestValidIc)}</span></span>
        </div>
        <div className="flex items-center gap-2 text-[11px] text-[var(--muted)]">
          {latest ? (
            <>
              <Badge tone={runStatusTone(latest.status)}>{runStatusLabel(latest.status)}</Badge>
              <span>最近训练 {fmtTime(latest.finishedTs ?? latest.startedTs)}</span>
            </>
          ) : (
            <span>尚未训练</span>
          )}
        </div>
      </button>
      <div className="flex flex-wrap gap-1.5">
        <Button variant="primary" size="sm" onClick={onTrain}>训练</Button>
        <Button variant="default" size="sm" onClick={onView}>查看</Button>
        <Button variant="default" size="sm" onClick={onEdit}>编辑</Button>
        <Button variant="ghost" size="sm" onClick={onDelete}>删除</Button>
      </div>
    </div>
  );
}
