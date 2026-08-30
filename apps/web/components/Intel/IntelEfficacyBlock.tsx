"use client";

import { CATEGORY_LABEL } from "@/lib/intel";
import { formatNetPnl, netPnlTone } from "@/lib/intelClosedLoop";
import {
  accuracyLabel,
  efficacyReadyText,
  statText,
  weightLabel,
} from "@/lib/intelActionability";
import type { IntelEfficacy } from "@/lib/intelTypes";

/**
 * 影响力页签的「战绩与信誉」块（ai-trade-intel-actionability 轴 B）。
 *
 * 三条纪律：
 *
 * - **「样本 3/8，先验未生效」必须如实写出来**。本轮的先验是**每标的一份**（跨标的池化是一条
 *   红线），新用户很久看不到它生效——靠画一个假的权重条把这件事藏起来，是这一块最容易犯的错。
 * - **准确率为 `null` 时写「样本不足」而不是 0%**：「没数据」与「全错」是两件事。
 * - **权重只影响排序与举证责任，一个字都不该暗示它是门禁**（R-6）：四个消费点里没有一个能
 *   改变一条情报进不进库、能不能被注入。
 */
export default function IntelEfficacyBlock({
  efficacy,
}: {
  efficacy: IntelEfficacy | null | undefined;
}) {
  if (!efficacy) return null;
  const ready = efficacyReadyText(efficacy);
  const rows = [...efficacy.byCategory].sort((a, b) => b.samples - a.samples);

  return (
    <section className="flex flex-col gap-1" data-testid="intel-efficacy-block">
      <h3 className="text-[11px] font-medium text-[var(--text)]">
        战绩与信誉
        <span className="ml-1.5 text-[10px] font-normal text-[var(--muted)]">
          按类别聚合，只用于排序与举证责任，不改变任何一条情报能否入库或被注入
        </span>
      </h3>
      {ready && (
        <p data-testid="intel-efficacy-ready" className="text-[10px] text-[var(--muted)]">
          {ready}
        </p>
      )}

      {!efficacy.enabled ? null : rows.length === 0 ? (
        <p className="text-[11px] text-[var(--muted)]">
          还没有任何已结算的引用或核查判定。这两条证据流会随着交易与核查轮自己积累起来。
        </p>
      ) : (
        <ul className="flex flex-col gap-1">
          {rows.map((row) => (
            <li
              key={row.category}
              data-testid="intel-efficacy-row"
              className="flex items-center justify-between gap-2 rounded-[10px] border border-[var(--border)] bg-[var(--bg)] px-2.5 py-1.5"
            >
              <span className="min-w-0 flex-1">
                <span className="block truncate text-[11px] text-[var(--text)]">
                  {CATEGORY_LABEL[row.category] ?? row.category}
                  <span className="ml-1.5 text-[10px] text-[var(--muted)]">
                    准确率 {accuracyLabel(row.accuracy)} · 权重 {weightLabel(row.weight)}
                  </span>
                </span>
                <span className="block truncate text-[10px] tabular text-[var(--muted)]">
                  {statText(row)}
                </span>
              </span>
              <span
                className="shrink-0 text-[11px] tabular"
                style={{ color: netPnlTone(row.netPnlUsd) }}
              >
                {formatNetPnl(row.netPnlUsd)}
              </span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
