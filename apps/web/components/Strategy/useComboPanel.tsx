"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState, type ReactNode } from "react";
import { api } from "@/lib/api";
import type { Strategy } from "@/lib/types";
import ComboDetailModal from "./ComboDetailModal";
import ComboEditorModal from "./ComboEditorModal";
import DeleteComboConfirm from "./DeleteComboConfirm";

/**
 * 组合面板的三弹窗开关态（编辑器 / 详情 / 删除二次确认）+ 删除 mutation + 渲染，自 StrategyPanel
 * 抽出以守住单文件阈值（CLAUDE 清洁码 ≤800 行 / 本方案硬约束 ≤750 行）。返回开启回调（供列表选中态
 * 与「+ 新建组合」入口调用）与 `comboModals` 渲染元素，状态留在本 hook、由 StrategyPanel 持有调用。
 */
export function useComboPanel({
  strategies,
  strategyId,
  comboEditingId,
  setComboEditingId,
  setStrategyId,
}: {
  strategies: Strategy[];
  strategyId: number | null;
  comboEditingId: number | null;
  setComboEditingId: (id: number | null) => void;
  setStrategyId: (id: number | null) => void;
}): {
  openNewCombo: () => void;
  openEditCombo: (combo: Strategy) => void;
  openComboDetail: (combo: Strategy) => void;
  openDeleteCombo: (combo: Strategy) => void;
  comboModals: ReactNode;
} {
  const qc = useQueryClient();
  const [editorOpen, setEditorOpen] = useState(false);
  const [detailCombo, setDetailCombo] = useState<Strategy | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<Strategy | null>(null);

  // 删除组合（二次确认后）：写后 invalidate（沿用既定口径，否则列表不刷新）；删的若是当前选中 /
  // 详情中 / 编辑中的组合则收敛对应态（选中合法性守门会在列表刷新后回落首个原子）。
  const removeCombo = useMutation({
    mutationFn: (id: number) => api.deleteStrategy(id),
    onSuccess: (_data, id) => {
      qc.invalidateQueries({ queryKey: ["strategies"] });
      if (strategyId === id) setStrategyId(null);
      if (detailCombo?.id === id) setDetailCombo(null);
      if (comboEditingId === id) {
        setComboEditingId(null);
        setEditorOpen(false);
      }
      setDeleteTarget(null);
    },
  });

  const openNewCombo = () => {
    setComboEditingId(null);
    setEditorOpen(true);
  };
  const openEditCombo = (combo: Strategy) => {
    setComboEditingId(combo.id);
    setEditorOpen(true);
  };

  const comboModals = (
    <>
      <ComboEditorModal
        open={editorOpen}
        editingId={comboEditingId}
        onClose={() => {
          setEditorOpen(false);
          setComboEditingId(null);
        }}
        onSaved={(created) => {
          if (created) setStrategyId(created.id);
        }}
      />

      {detailCombo && (
        <ComboDetailModal
          combo={detailCombo}
          strategies={strategies}
          editDisabled={comboEditingId !== null && comboEditingId !== detailCombo.id}
          onClose={() => setDetailCombo(null)}
          onEdit={(c) => {
            setDetailCombo(null);
            openEditCombo(c);
          }}
          onDelete={(c) => {
            setDetailCombo(null);
            setDeleteTarget(c);
          }}
        />
      )}

      {deleteTarget && (
        <DeleteComboConfirm
          combo={deleteTarget}
          pending={removeCombo.isPending}
          error={removeCombo.isError ? (removeCombo.error as Error).message : null}
          onCancel={() => setDeleteTarget(null)}
          onConfirm={() => removeCombo.mutate(deleteTarget.id)}
        />
      )}
    </>
  );

  return {
    openNewCombo,
    openEditCombo,
    openComboDetail: setDetailCombo,
    openDeleteCombo: setDeleteTarget,
    comboModals,
  };
}
