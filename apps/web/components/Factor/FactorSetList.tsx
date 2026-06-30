"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { useState } from "react";
import { Badge, Button } from "@/components/ui";
import { api } from "@/lib/api";
import { applyWeights, formatIc, resolveThresholds, trainedFromSet } from "@/lib/factor";
import { factorApi } from "@/lib/factorApi";
import type { FactorSet } from "@/lib/factorTypes";
import { fmtTime } from "@/lib/format";
import { useAppStore } from "@/lib/store";
import FactorSetDetail from "./FactorSetDetail";

function sortByUpdated(sets: FactorSet[]): FactorSet[] {
  return [...sets].sort((a, b) => b.updatedTs - a.updatedTs);
}

// 从一条已存组合构造 factor_score 策略内联快照 params：成员烘焙训练权重、带归一化契约 / 溯源 +
// 来源组合 factorSetId（回链稳健 set 分支，无孤儿，P1-2）。复用 builder 同口径（applyWeights + trainedFromSet）。
function setToStrategyParams(set: FactorSet): Record<string, unknown> {
  const trained = trainedFromSet(set);
  return {
    members: applyWeights(set.members, trained?.weights),
    thresholds: resolveThresholds(set.config?.thresholds),
    normStats: trained?.normStats,
    provenance: trained?.provenance,
    factorSetId: set.id,
  };
}

/**
 * 「因子组合策略」工作区：列出用户已建的全部因子组合，支持选择 / 查看（FactorSetDetail）/ 编辑（载入
 * builder）/ 删除（二次确认）/ 从组合新建得分策略。读取 GET /factor-sets（TanStack Query）。
 */
export default function FactorSetList() {
  const qc = useQueryClient();
  const setSection = useAppStore((s) => s.setFactorLabSection);
  const setEditTarget = useAppStore((s) => s.setFactorEditTarget);
  const setStrategyId = useAppStore((s) => s.setStrategyId);
  const setActiveView = useAppStore((s) => s.setActiveView);

  const sets = useQuery({ queryKey: ["factor", "sets"], queryFn: () => factorApi.sets.list() });
  const [selectedId, setSelectedId] = useState<number | null>(null);

  const remove = useMutation({
    mutationFn: (id: number) => factorApi.sets.remove(id),
    onSuccess: (_data, id) => {
      if (selectedId === id) setSelectedId(null);
      void qc.invalidateQueries({ queryKey: ["factor", "sets"] });
    },
  });
  const createStrategy = useMutation({
    mutationFn: (set: FactorSet) =>
      api.createStrategy({ templateKey: "factor_score", name: `${set.name} · 得分策略`, params: setToStrategyParams(set) }),
    onSuccess: (strategy) => {
      setStrategyId(strategy.id);
      setActiveView("trade");
    },
  });

  const onEdit = (set: FactorSet) => {
    // set 分支（无需 snapshot 兜底：直接经 setId 拉取；拉取失败时 builder 已无可回落对象，提示新建态）。
    setEditTarget({ source: "set", setId: set.id });
    setSection("builder");
  };
  const onDelete = (set: FactorSet) => {
    const ok = window.confirm(
      `确定删除组合「${set.name}」？已基于它创建的得分策略不受影响（内联快照自包含），但其「在因子实验室编辑」回链将回落到策略内联快照。`,
    );
    if (ok) remove.mutate(set.id);
  };

  if (sets.isPending) {
    return <p className="py-8 text-center text-[11px] text-[var(--muted)]">加载因子组合...</p>;
  }
  if (sets.isError) {
    return (
      <div className="flex flex-col items-center gap-2 py-8">
        <p className="text-[11px] text-[var(--short)]">加载因子组合失败：{(sets.error as Error)?.message}</p>
        <Button variant="default" size="sm" onClick={() => sets.refetch()}>重试</Button>
      </div>
    );
  }

  const items = sortByUpdated(sets.data ?? []);
  if (items.length === 0) {
    return (
      <div className="card flex flex-col items-center gap-3 p-8 text-center">
        <span className="text-sm font-medium text-[var(--text)]">还没有因子组合</span>
        <span className="text-[11px] text-[var(--muted)]">
          去「权重训练与组合」选因子、训练权重并保存，组合会出现在这里供查看 / 编辑。
        </span>
        <Button variant="primary" size="sm" onClick={() => setSection("builder")}>去新建组合</Button>
      </div>
    );
  }

  const selected = items.find((s) => s.id === selectedId) ?? null;

  return (
    <div className="grid grid-cols-1 gap-4 lg:grid-cols-[minmax(220px,300px)_1fr]">
      <div className="flex flex-col gap-2">
        {items.map((set) => (
          <FactorSetCard
            key={set.id}
            set={set}
            selected={set.id === selectedId}
            onSelect={() => setSelectedId(set.id)}
            onEdit={() => onEdit(set)}
            onDelete={() => onDelete(set)}
            onCreateStrategy={() => createStrategy.mutate(set)}
            creating={createStrategy.isPending}
          />
        ))}
      </div>
      <div className="min-w-0">
        {selected ? (
          <FactorSetDetail set={selected} />
        ) : (
          <div className="card flex h-full items-center justify-center p-8 text-center text-[11px] text-[var(--muted)]">
            选择左侧的因子组合查看成员、权重、复合得分曲线与训练溯源。
          </div>
        )}
        {(remove.isError || createStrategy.isError) && (
          <p className="mt-2 text-[11px] text-[var(--short)]">
            操作失败：{((remove.error || createStrategy.error) as Error)?.message}
          </p>
        )}
      </div>
    </div>
  );
}

function FactorSetCard({
  set,
  selected,
  onSelect,
  onEdit,
  onDelete,
  onCreateStrategy,
  creating,
}: {
  set: FactorSet;
  selected: boolean;
  onSelect: () => void;
  onEdit: () => void;
  onDelete: () => void;
  onCreateStrategy: () => void;
  creating: boolean;
}) {
  return (
    <div
      className={clsx(
        "card flex flex-col gap-2 p-3 transition-colors",
        selected ? "border-[var(--accent)] bg-[var(--accent-soft)]/30" : "hover:border-[var(--accent-soft)]",
      )}
    >
      <button type="button" onClick={onSelect} className="flex flex-col gap-1 text-left" aria-pressed={selected}>
        <div className="flex items-center gap-2">
          <span className="truncate text-sm font-medium text-[var(--text)]">{set.name}</span>
          <Badge tone="accent">{set.method}</Badge>
        </div>
        <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5 text-[11px] text-[var(--muted)]">
          <span>{set.members.length} 成员</span>
          <span>样本外 IC {formatIc(set.metrics?.icOut ?? null)}</span>
          <span>更新于 {fmtTime(set.updatedTs)}</span>
        </div>
      </button>
      <div className="flex flex-wrap gap-1.5">
        <Button variant="primary" size="sm" onClick={onEdit}>编辑</Button>
        <Button variant="default" size="sm" disabled={creating} onClick={onCreateStrategy}>
          {creating ? "创建中..." : "新建策略"}
        </Button>
        <Button variant="ghost" size="sm" onClick={onDelete}>删除</Button>
      </div>
    </div>
  );
}
