"use client";

import clsx from "clsx";
import { gateVerdictLabel } from "@/lib/journal";
import type { JournalGateRow } from "@/lib/journalTypes";

/**
 * 门禁链（ai-trade-audit-journal §2.6.2 第 2 段）。
 *
 * **层集与层名一律取自 wire**（服务端按 `open_funnel.LAYERS` / `LAYER_LABELS` 下发）。
 * 本文件里一个层名字面量都没有——在前端补一张表就是本仓明令禁止的第二份真源，
 * 而它漂移时**不报任何错**（`gate_veto.py` 的纪律 1 已把这条写成明文）。
 *
 * 四态而不是三态：**未装配**（灰）/ **放行** / **否决** / **未知**（`armed === null`）。
 * 第四态是硬要求——把「问不出来」渲染成「没装」，就是用一个看起来确定的说法盖住一个不确定的
 * 事实。
 *
 * **绝不用「没显示 = 通过」**：层数一行不少地渲染（`trace.py` 的 `base_rule` 相位注释原文：
 * 「含放行轮——静默的保守会被用户当成故障」）。
 */
export default function JournalGateChain({
  gates,
  vetoedBy,
}: {
  gates: JournalGateRow[];
  vetoedBy: string | null;
}) {
  if (!gates.length) {
    return (
      <p className="px-1 py-2 text-xs text-[var(--muted)]">
        该轮未留存门禁链断面（本特性接线之前落的行）。
      </p>
    );
  }
  return (
    <div className="min-w-0 overflow-x-auto">
      <table className="w-full min-w-[520px] text-xs">
        <thead className="text-[var(--muted)]">
          <tr>
            {["层", "判定", "读数"].map((label) => (
              <th key={label} scope="col" className="whitespace-nowrap px-2 py-1.5 text-left font-medium">
                {label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {gates.map((row) => {
            const unknown = row.armed === null;
            const veto = row.verdict === "veto";
            const skip = row.verdict === "skip";
            return (
              <tr
                key={row.key}
                className={clsx(
                  "border-b border-[var(--border)]",
                  veto && "bg-[color-mix(in_srgb,var(--short)_10%,transparent)]",
                )}
              >
                <td className="whitespace-nowrap px-2 py-1.5">
                  {/* 形状（前缀记号）+ 文案 + 颜色三者齐全：语义绝不靠颜色单独承载。 */}
                  <span aria-hidden="true" className="mr-1 tabular">
                    {veto ? "✕" : unknown ? "?" : skip ? "○" : "✓"}
                  </span>
                  {row.label}
                </td>
                <td
                  className={clsx(
                    "whitespace-nowrap px-2 py-1.5",
                    veto && "font-medium text-[var(--short-ink)]",
                    (skip || unknown) && "text-[var(--muted)]",
                  )}
                >
                  {gateVerdictLabel(row.verdict, row.armed)}
                  {veto && vetoedBy === row.key && (
                    <span className="ml-1 text-[10px]">（本轮由该层改写）</span>
                  )}
                </td>
                {/* 读数列刻意允许换行：它装的是一段 JSON，横向滚动由内层 pre 承担。
                    `whitespace-normal` 是**显式表态**——源码扫描门禁不接受沉默。 */}
                <td className="whitespace-normal px-2 py-1.5 text-[var(--muted)]">
                  {row.readings ? (
                    <pre className="max-h-24 min-w-0 overflow-x-auto whitespace-pre text-[10px] leading-4">
                      {JSON.stringify(row.readings, null, 1)}
                    </pre>
                  ) : (
                    <span className="whitespace-nowrap">—</span>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
