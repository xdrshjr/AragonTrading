"use client";

import {
  POLICY_FIELD_ORDER,
  POLICY_HINTS,
  SELECTION_METRIC_LABELS,
} from "@/lib/comboPolicyHints";
import { fmtPct } from "@/lib/format";
import { regimeAffinityLabels } from "@/lib/regimeAffinity";
import type { ComboMemberSpec, ComboPolicy, Strategy } from "@/lib/types";
import { Badge } from "@/components/ui";

/**
 * 组合详情只读分区（概览 / 成员 / 轮动参数）。自 `ComboDetailModal` 抽出供其与
 * `StrategyDetailModal` 两处复用，杜绝重复实现（CLAUDE §四 去孤岛 / 去重复）。
 * 纯展示，无 hooks / 无副作用；行为与抽出前完全等价，仅改变文件归属。
 */

export function OverviewSection({
  memberCount,
  policy,
}: {
  memberCount: number;
  policy: ComboPolicy;
}) {
  const items = [
    { label: "成员数", value: `${memberCount} 个` },
    { label: "选优指标", value: SELECTION_METRIC_LABELS[policy.selectionMetric] ?? policy.selectionMetric },
    { label: "体制门控", value: policy.regimeGating ? "开启" : "关闭" },
    { label: "再平衡间隔", value: `${policy.rebalanceBars} 根` },
    { label: "表现回看", value: `${policy.lookbackBars} 根` },
    { label: "最短持有", value: `${policy.minDwellBars} 根` },
    { label: "切换迟滞", value: fmtPct(policy.switchMarginPct) },
    { label: "换手成本", value: `${policy.turnoverBps} bps` },
  ];
  return (
    <section className="flex flex-col gap-2">
      <SectionTitle>概览</SectionTitle>
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        {items.map((it) => (
          <div
            key={it.label}
            className="rounded-[10px] border border-[var(--border)] bg-[var(--surface-2)] px-3 py-2"
          >
            <div className="text-[11px] text-[var(--muted)]">{it.label}</div>
            <div className="tabular text-sm font-semibold text-[var(--text)]">{it.value}</div>
          </div>
        ))}
      </div>
    </section>
  );
}

export function MembersSection({
  members,
  strategies,
}: {
  members: ComboMemberSpec[];
  strategies: Strategy[];
}) {
  return (
    <section className="flex flex-col gap-2">
      <SectionTitle>成员策略 · {members.length}</SectionTitle>
      {members.length === 0 ? (
        <p className="text-[11px] text-[var(--muted)]">该组合暂无成员</p>
      ) : (
        <div className="flex flex-col gap-1.5">
          {members.map((member, i) => (
            <MemberCard key={`${member.templateKey}-${i}`} member={member} strategies={strategies} />
          ))}
        </div>
      )}
    </section>
  );
}

export function MemberCard({ member, strategies }: { member: ComboMemberSpec; strategies: Strategy[] }) {
  // R3：按 templateKey 在 strategies 列表回查同模板行取 name/description（前端无注册表），
  // 回退成员快照自带 name；模板已删（无同模板行）则标注失效。
  const row = strategies.find((s) => s.templateKey === member.templateKey);
  const name = row?.name ?? member.name ?? member.templateKey;
  const affinity = regimeAffinityLabels(member.templateKey);
  return (
    <div className="flex items-center gap-2 rounded-[8px] border border-[var(--border)] bg-[var(--surface)] px-3 py-2 text-xs">
      <div className="flex min-w-0 flex-1 flex-col gap-0.5">
        <div className="flex items-center gap-1.5">
          <span className="truncate font-medium text-[var(--text)]">{name}</span>
          {!row && <Badge tone="short">失效</Badge>}
        </div>
        <span className="truncate text-[11px] text-[var(--muted)]">{member.templateKey}</span>
      </div>
      {affinity.length > 0 && (
        <span className="flex shrink-0 flex-wrap justify-end gap-1">
          {affinity.map((label) => (
            <span
              key={label}
              className="rounded-[5px] bg-[var(--surface-2)] px-1.5 py-0.5 text-[10px] text-[var(--muted)]"
            >
              {label}
            </span>
          ))}
        </span>
      )}
    </div>
  );
}

export function RotationSection({
  policy,
  labelOf,
}: {
  policy: ComboPolicy;
  labelOf: (key: string) => string;
}) {
  const values = policy as unknown as Record<string, unknown>;
  return (
    <section className="flex flex-col gap-2">
      <SectionTitle>轮动参数</SectionTitle>
      <div className="grid grid-cols-2 gap-2">
        {POLICY_FIELD_ORDER.map((key) => (
          <div
            key={key}
            title={POLICY_HINTS[key]}
            className="flex flex-col gap-0.5 rounded-[8px] border border-[var(--border)] bg-[var(--surface)] px-2.5 py-1.5"
          >
            <span className="text-[11px] text-[var(--muted)]">{labelOf(key)}</span>
            <span className="tabular text-xs font-medium text-[var(--text)]">
              {formatPolicyValue(key, values[key])}
            </span>
          </div>
        ))}
      </div>
    </section>
  );
}

export function SectionTitle({ children }: { children: React.ReactNode }) {
  return (
    <h3 className="text-xs font-semibold uppercase tracking-wide text-[var(--muted)]">{children}</h3>
  );
}

// 轮动参数值的展示文案：选优指标中文化、布尔→是/否、迟滞→百分比、其余原值。
export function formatPolicyValue(key: string, value: unknown): string {
  if (key === "selectionMetric") return SELECTION_METRIC_LABELS[String(value)] ?? String(value);
  if (key === "regimeGating") return value ? "是" : "否";
  if (key === "switchMarginPct") return fmtPct(Number(value));
  return String(value);
}
