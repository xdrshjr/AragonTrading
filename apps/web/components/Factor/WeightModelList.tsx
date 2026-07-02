"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { useState } from "react";
import { Badge, Button, Switch } from "@/components/ui";
import ConfirmDialog from "@/components/ui/ConfirmDialog";
import { formatIc } from "@/lib/factor";
import { fmtTime } from "@/lib/format";
import { canBacktestModel, methodLabel, runStatusLabel, runStatusTone, sortModelsByUpdated, symbolsSummary } from "@/lib/weightModel";
import { weightModelApi } from "@/lib/weightModelApi";
import type { WeightModel } from "@/lib/weightModelTypes";

/**
 * 「权重模型」列表 + CRUD（镜像 FactorSetList shell）：卡片呈现名称 / 标的·周期 / 方法 / 成员数 /
 * 最佳验证 IC / 最近训练态 / 启用；支持训练（进实时视图）/ 查看（详情）/ 编辑 / 删除（应用内二次确认）/ 新建。
 *
 * 加固：H-F10 显示已停用开关 + running 卡片轮询刷新 + 删除按钮去抖；H-F8 删除改用应用内 ConfirmDialog。
 */
export default function WeightModelList({
  onTrain,
  onView,
  onBacktest,
  onEdit,
  onNew,
}: {
  onTrain: (model: WeightModel) => void;
  onView: (model: WeightModel) => void;
  onBacktest: (model: WeightModel) => void;
  onEdit: (model: WeightModel) => void;
  onNew: () => void;
}) {
  const qc = useQueryClient();
  const [showDisabled, setShowDisabled] = useState(false);   // H-F10：默认仅显示启用，开关纳入已停用
  const [pendingDelete, setPendingDelete] = useState<WeightModel | null>(null);   // H-F8：应用内确认
  const models = useQuery({
    queryKey: ["weight-models", { enabled: showDisabled ? undefined : true }],
    queryFn: () => weightModelApi.list(showDisabled ? undefined : true),
    // H-F10：任一卡片处于 running 时轮询刷新（4s）；无 running 则不轮询（省资源）。
    refetchInterval: (query) => {
      const data = query.state.data as WeightModel[] | undefined;
      return data?.some((m) => m.latestRun?.status === "running") ? 4000 : false;
    },
  });
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
      <div className="flex items-center justify-between gap-2">
        <div className="flex flex-col">
          <span className="text-sm font-semibold text-[var(--text)]">权重模型</span>
          <span className="text-[11px] text-[var(--muted)]">管理模型并实时观测每次训练的 loss / IC 曲线</span>
        </div>
        <div className="flex items-center gap-3">
          {/* G-F11：Switch 不再带冗余 aria-label；改用 <label htmlFor> 关联（button 为可标注元素），
              名称只念一遍且点可见文即可切换。 */}
          <span className="flex items-center gap-1.5 text-[11px] text-[var(--muted)]">
            <Switch checked={showDisabled} onChange={setShowDisabled} id="wm-show-disabled" />
            <label htmlFor="wm-show-disabled" className="cursor-pointer">显示已停用</label>
          </span>
          <Button variant="primary" size="sm" onClick={onNew}>新建模型</Button>
        </div>
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
              deleting={remove.isPending && remove.variables === model.id}
              onTrain={() => onTrain(model)}
              onView={() => onView(model)}
              onBacktest={() => onBacktest(model)}
              onEdit={() => onEdit(model)}
              onDelete={() => setPendingDelete(model)}
            />
          ))}
        </div>
      )}
      {remove.isError && (
        <p className="text-[11px] text-[var(--short)]">删除失败：{(remove.error as Error)?.message}</p>
      )}

      <ConfirmDialog
        open={pendingDelete != null}
        title="删除权重模型"
        message={pendingDelete ? `确定删除模型「${pendingDelete.name}」？将一并删除其全部训练记录，且无法恢复。` : ""}
        confirmText="删除"
        onConfirm={() => {
          if (pendingDelete) remove.mutate(pendingDelete.id);
          setPendingDelete(null);
        }}
        onCancel={() => setPendingDelete(null)}
      />
    </div>
  );
}

function ModelCard({
  model,
  deleting,
  onTrain,
  onView,
  onBacktest,
  onEdit,
  onDelete,
}: {
  model: WeightModel;
  deleting: boolean;
  onTrain: () => void;
  onView: () => void;
  onBacktest: () => void;
  onEdit: () => void;
  onDelete: () => void;
}) {
  const latest = model.latestRun;
  const isRunning = latest?.status === "running";   // G-F10：在跑模型禁「训练」，杜绝重叠 run
  const canBacktest = canBacktestModel(model);      // 至少一次成功训练（latestRunId 非空）方可回测
  return (
    <div className={clsx("card flex flex-col gap-2 p-3 transition-colors hover:border-[var(--accent-soft)]")}>
      <button type="button" onClick={onView} className="flex flex-col gap-1 text-left">
        <div className="flex items-center gap-2">
          <span className="truncate text-sm font-medium text-[var(--text)]">{model.name}</span>
          <Badge tone="accent">{methodLabel(model.method)}</Badge>
          {!model.enabled && <Badge tone="neutral">已停用</Badge>}
        </div>
        <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5 text-[11px] text-[var(--muted)]">
          <span title={symbolsSummary(model.instId, model.symbols).title}>
            {symbolsSummary(model.instId, model.symbols).label} · {model.bar}
          </span>
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
        <Button variant="primary" size="sm" disabled={isRunning}
          title={isRunning ? "该模型正在训练" : undefined} onClick={onTrain}>训练</Button>
        <Button variant="default" size="sm" onClick={onView}>查看</Button>
        <Button variant="default" size="sm" disabled={!canBacktest}
          title={canBacktest ? undefined : "请先完成一次成功训练"} onClick={onBacktest}>回测</Button>
        <Button variant="default" size="sm" onClick={onEdit}>编辑</Button>
        <Button variant="ghost" size="sm" disabled={deleting} onClick={onDelete}>
          {deleting ? "删除中..." : "删除"}
        </Button>
      </div>
    </div>
  );
}
