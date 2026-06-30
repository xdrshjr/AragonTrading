"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState, type ReactNode } from "react";
import { api } from "@/lib/api";
import { factorApi } from "@/lib/factorApi";
import { isFactorStrategy } from "@/lib/strategyCatalog";
import type { Strategy } from "@/lib/types";
import DeleteStrategyConfirm from "./DeleteStrategyConfirm";

/**
 * 卡片删除态（原子 / 因子策略）+ 双路删除 mutation + 二次确认弹窗渲染，自 StrategyPanel 抽出以守单文件阈值。
 * 仅处理 atomic 与孤儿因子副本；**组合删除不经此 hook**（分流回 useComboPanel 保持组合删除单一真源）。
 *
 * 删除分流（守 factor_set ↔ 伴生 factor_score 策略 1:1）：
 * - 带 `params.factorSetId` 的因子策略 → `factorApi.sets.remove(factorSetId)`，后端单事务级联删伴生策略，
 *   避免直接删策略行留孤儿、被 `backfill_companions` 下次启动复活；双失效 `["factor","sets"]` + `["strategies"]`。
 * - 其余（原子、或「另存为」剥离 factorSetId 的孤儿因子副本）→ `api.deleteStrategy(id)`；失效 `["strategies"]`。
 */
export function useStrategyDelete({
  strategyId,
  onAfterDelete,
}: {
  strategyId: number | null;
  onAfterDelete: (deletedId: number) => void;
}): { requestDelete: (s: Strategy) => void; deleteModal: ReactNode } {
  const qc = useQueryClient();
  const [deleteTarget, setDeleteTarget] = useState<Strategy | null>(null);

  // 删后收敛：删的若是当前选中策略，回调让选中态回落（守门 useEffect 会在列表刷新后落到首个剩余策略）。
  const finishDelete = (deletedId: number) => {
    if (deletedId === strategyId) onAfterDelete(deletedId);
    setDeleteTarget(null);
  };

  const removeStrategy = useMutation({
    mutationFn: (id: number) => api.deleteStrategy(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["strategies"] }),
  });

  const removeFactorSet = useMutation({
    mutationFn: (factorSetId: number) => factorApi.sets.remove(factorSetId),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["factor", "sets"] });
      void qc.invalidateQueries({ queryKey: ["strategies"] });
    },
  });

  const confirmDelete = () => {
    const target = deleteTarget;
    if (!target) return;
    const factorSetId = target.params.factorSetId;
    if (isFactorStrategy(target) && typeof factorSetId === "number") {
      removeFactorSet.mutate(factorSetId, { onSuccess: () => finishDelete(target.id) });
    } else {
      removeStrategy.mutate(target.id, { onSuccess: () => finishDelete(target.id) });
    }
  };

  const pending = removeStrategy.isPending || removeFactorSet.isPending;
  const error = removeStrategy.isError
    ? (removeStrategy.error as Error).message
    : removeFactorSet.isError
      ? (removeFactorSet.error as Error).message
      : null;

  const deleteModal = deleteTarget ? (
    <DeleteStrategyConfirm
      strategy={deleteTarget}
      pending={pending}
      error={error}
      onCancel={() => setDeleteTarget(null)}
      onConfirm={confirmDelete}
    />
  ) : null;

  return { requestDelete: setDeleteTarget, deleteModal };
}
