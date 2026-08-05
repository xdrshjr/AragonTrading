"use client";

import { rejectReasonCn } from "@/lib/discovery";
import { fmtNumber } from "@/lib/discoveryStrategy";
import type { DiscoveryGateRow } from "@/lib/discoveryTypes";

/**
 * 通用「闸位阶梯」：因子轴 F1–F11 与策略轴 S1–S9 共用一个组件。
 *
 * 逐条显示**实际值与门槛**，通过打勾、卡在哪条标红——只给一个 `rejectReason` 字符串等于
 * 让用户猜「差多少」。诚实优先于漂亮：没有逐条数据时明说，不画一排空壳。
 */
export default function DiscoveryGateLadder({
  gates,
  rejectReason,
}: {
  gates: DiscoveryGateRow[] | undefined;
  rejectReason?: string;
}) {
  const rows = gates ?? [];
  if (!rows.length) {
    return (
      <p className="text-[11px] text-[var(--muted)]">
        {rejectReason
          ? `本候选未产出逐条闸位数据；拒绝原因：${rejectReasonCn(rejectReason)}`
          : "本候选没有逐条闸位数据。"}
      </p>
    );
  }
  const blockedId = rows.find((gate) => !gate.passed)?.id ?? "";

  return (
    <ul className="flex flex-col gap-1" aria-label="闸位阶梯">
      {rows.map((gate) => {
        const blocked = gate.id === blockedId;
        return (
          <li
            key={gate.id}
            className={`flex items-center gap-2 rounded-[6px] px-2 py-1 text-[11px] ${
              blocked ? "bg-[var(--short)]/10" : ""
            }`}
          >
            <span
              aria-hidden
              className={`w-4 shrink-0 text-center ${
                gate.passed ? "text-[var(--long)]" : "text-[var(--short)]"
              }`}
            >
              {gate.passed ? "✓" : "✕"}
            </span>
            <code className="w-8 shrink-0 text-[var(--muted)]">{gate.id}</code>
            <span className="flex-1 truncate text-[var(--text)]" title={gate.label}>
              {gate.label}
            </span>
            <span className="shrink-0 tabular text-[var(--muted)]">
              {fmtNumber(gate.value, 4)}
              <span className="mx-1">/</span>
              <span title="门槛">{fmtNumber(gate.gate, 4)}</span>
            </span>
            {blocked && (
              <span className="shrink-0 text-[var(--short)]">卡在这条</span>
            )}
          </li>
        );
      })}
    </ul>
  );
}
