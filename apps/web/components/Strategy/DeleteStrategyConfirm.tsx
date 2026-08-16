"use client";

import type { ReactNode } from "react";
import { isCombo } from "@/lib/combo";
import { isFactorStrategy } from "@/lib/strategyCatalog";
import type { Strategy } from "@/lib/types";
import { Button, Modal } from "@/components/ui";

/**
 * 通用删除二次确认弹窗（泛化自原 DeleteComboConfirm）：复用 `Modal` 原语（焦点陷阱 / Esc / 遮罩关闭），
 * 由 `useComboPanel`（组合删除）与 `useStrategyDelete`（原子 / 因子删除）共用，单一删除确认实现。
 * 删除不可撤销，故确认后才打删除请求；失败显式错误态（不假成功）。文案按策略类型分支：
 * 因子策略额外提示「连带移除因子实验室对应组合」，因其删除经 factor_set 级联以守 1:1 同步。
 */
export default function DeleteStrategyConfirm({
  strategy,
  pending,
  error,
  onCancel,
  onConfirm,
}: {
  strategy: Strategy;
  pending: boolean;
  error: string | null;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  const titleId = `strategy-delete-${strategy.id}`;
  const { heading, body } = describeDeletion(strategy);
  return (
    <Modal open onClose={onCancel} labelledBy={titleId} className="max-w-sm">
      <div className="flex flex-col gap-3 px-5 py-4">
        <h2 id={titleId} className="text-sm font-semibold text-[var(--text)]">
          {heading}
        </h2>
        <p className="text-xs leading-relaxed text-[var(--muted)]">{body}</p>
        {error && (
          <div className="rounded-[8px] border border-[var(--short)] bg-[var(--surface)] px-3 py-2 text-[11px] text-[var(--short-ink)]">
            删除失败：{error}
          </div>
        )}
        <div className="flex items-center justify-end gap-2">
          <Button variant="ghost" size="sm" disabled={pending} onClick={onCancel}>
            取消
          </Button>
          <Button variant="danger" size="sm" disabled={pending} onClick={onConfirm}>
            {pending ? "删除中…" : "删除"}
          </Button>
        </div>
      </div>
    </Modal>
  );
}

// 标题 + 正文按策略类型分支：组合 / 带来源组合的因子（级联提示）/ 其余（原子或孤儿因子副本）。
function describeDeletion(strategy: Strategy): { heading: string; body: ReactNode } {
  const name = <span className="text-[var(--text)]">{strategy.name}</span>;
  if (isCombo(strategy)) {
    return {
      heading: "删除组合",
      body: <>确认删除组合「{name}」？此操作不可撤销。</>,
    };
  }
  if (isFactorStrategy(strategy) && typeof strategy.params.factorSetId === "number") {
    return {
      heading: "删除因子策略",
      body: (
        <>
          确认删除因子策略「{name}」？将同时移除因子实验室中对应的因子组合（数据保持一致）。此操作不可撤销。
        </>
      ),
    };
  }
  return {
    heading: "删除策略",
    body: <>确认删除策略「{name}」？此操作不可撤销。</>,
  };
}
