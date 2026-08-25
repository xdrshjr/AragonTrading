"use client";

import ConfirmDialog from "@/components/ui/ConfirmDialog";
import type { ExperiencePruneResult } from "@/lib/experienceTypes";

/**
 * 「清理沉降经验」的二次确认。
 *
 * **绝不静默批删**：先跑一次 `dryRun` 把条数拿回来，逐条列出「将删除 N 条 / 跳过置顶 M 条 /
 * 跳过你手动静音的 K 条」，用户确认之后才真删。
 *
 * 它为什么必须与本轮同批交付：J-4 让一批「语境未知」的旧桶在下一次重建中不再出现 ⇒ 被判为沉降。
 * 那是**正确的终局**，但会在管理界面上一次性冒出一批用户无法处置的条目——不给清理入口，
 * 用户看到的就是一次「数据坏了」。
 *
 * ⚠️ `ConfirmDialog` 的 `message` 是**纯文本**渲染：写 `**粗体**` 只会让用户看到两颗星号
 * （本仓库已复发两次）。要强调就用词，不用 markdown。
 */
export default function ExperiencePrunePrompt({
  preview,
  onCancel,
  onConfirm,
}: {
  preview: ExperiencePruneResult | null;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  if (!preview) return null;
  const skipped: string[] = [];
  if (preview.skippedPinned > 0) skipped.push(`置顶 ${preview.skippedPinned} 条`);
  if (preview.skippedUserMuted > 0) {
    skipped.push(`你手动静音的 ${preview.skippedUserMuted} 条`);
  }
  return (
    <ConfirmDialog
      open
      danger
      title={preview.matched > 0 ? "清理这些沉降经验？" : "没有可清理的沉降经验"}
      message={preview.matched > 0
        ? `将永久删除 ${preview.matched} 条已沉降的统计经验（它们的边也会一并移除）。`
          + (skipped.length > 0 ? `跳过：${skipped.join("、")}——用户按下过的每一个动作都会被保留。` : "")
          + "沉降 = 最近一次重建没有再产出它（语境收敛、样本过期或档位截断）。"
          + "此操作不可撤销；被删掉的经验若将来又被新证据产出，会以一条新记录重新出现。"
        : "当前没有「已沉降且非你手动静音、也未被置顶」的统计经验。"
          + (skipped.length > 0 ? `（跳过：${skipped.join("、")}。）` : "")}
      confirmText={preview.matched > 0 ? `永久删除 ${preview.matched} 条` : "知道了"}
      onCancel={onCancel}
      onConfirm={preview.matched > 0 ? onConfirm : onCancel}
    />
  );
}
