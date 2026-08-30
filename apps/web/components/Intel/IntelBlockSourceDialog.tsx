"use client";

import { useEffect, useState } from "react";
import ConfirmDialog from "@/components/ui/ConfirmDialog";
import { intelApi } from "@/lib/intelApi";

/**
 * 「不再采纳此源」确认框（ai-trade-intel-closed-loop §3.7.2）。
 *
 * **必须显示受影响条数**——一个说不清后果的破坏性操作按钮不该出现在这个产品里。条数由
 * `POST /intel/feedback/preview` 返回（**纯读，绝不写库**）；取数在飞时先显示一个诚实的
 * 「正在计算…」，而不是先写一个 0 再跳变。
 *
 * 作用域勾选框的默认极性是**不勾选**（只对当前标的生效）：一次点击不该悄悄改掉别的标的的
 * 采集面。用户想全局拉黑，多点一下即可。
 */
export default function IntelBlockSourceDialog({
  open,
  domain,
  instId,
  onCancel,
  onConfirm,
}: {
  open: boolean;
  domain: string;
  instId: string;
  onCancel: () => void;
  onConfirm: (scopeInstId: string) => void;
}) {
  const [global, setGlobal] = useState(false);
  const [affected, setAffected] = useState<number | null>(null);

  // 关闭即把作用域收回默认的「只对本标的」。缺了这一步，用户上一次勾过的「对所有标的生效」
  // 会跟着弹窗活到下一个域名——一个不可逆动作的作用域**绝不允许**跨调用留存，哪怕它上一次
  // 是用户自己勾的。
  useEffect(() => {
    if (!open) setGlobal(false);
  }, [open]);

  useEffect(() => {
    if (!open || !domain) return;
    setAffected(null);
    let alive = true;
    intelApi
      .previewFeedback({ key: domain, action: "block", instId: global ? "" : instId })
      .then((res) => {
        if (alive) setAffected(res.affected);
      })
      .catch(() => {
        // 预览失败不挡确认：受影响条数是**知情**用的，取不到时改口说「无法预估」而不是显示 0。
        if (alive) setAffected(-1);
      });
    return () => {
      alive = false;
    };
  }, [open, domain, instId, global]);

  const countText =
    affected === null
      ? "正在计算受影响条数…"
      : affected < 0
        ? "受影响条数暂时无法预估。"
        : `已入库的 ${affected} 条情报（全部来源都是它）会被归档，可在「已归档」中恢复。`;

  return (
    <ConfirmDialog
      open={open}
      title={`不再采纳 ${domain}？`}
      message={`之后的采集与核查都会跳过这个来源。${countText}`}
      confirmText="不再采纳此源"
      onCancel={onCancel}
      onConfirm={() => onConfirm(global ? "" : instId)}
    >
      <label className="flex items-center gap-1.5 text-[11px] text-[var(--muted)]">
        <input
          type="checkbox"
          checked={global}
          onChange={(e) => setGlobal(e.target.checked)}
          className="h-3 w-3 accent-[var(--accent)]"
        />
        对所有标的生效（不勾选则只对 {instId} 生效）
      </label>
    </ConfirmDialog>
  );
}
