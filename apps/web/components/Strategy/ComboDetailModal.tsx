"use client";

import { useMutation } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { extractComboPolicy } from "@/lib/combo";
import {
  POLICY_FIELD_ORDER,
  POLICY_HINTS,
  POLICY_LABELS,
  SELECTION_METRIC_LABELS,
} from "@/lib/comboPolicyHints";
import { colorForReturn, fmtPct } from "@/lib/format";
import { regimeAffinityLabels } from "@/lib/regimeAffinity";
import { useAppStore } from "@/lib/store";
import type { ComboMemberSpec, ComboPolicy, Strategy } from "@/lib/types";
import { Badge, Button, Modal } from "@/components/ui";
import ComboTimeline from "./ComboTimeline";
import MetricsCards from "./MetricsCards";

/**
 * 组合详情弹窗（顶级设计）：复用 `Modal` 原语（焦点陷阱 / Esc / 遮罩 / 滚动锁 / 焦点恢复）。
 * 静态信息（概览 + 成员 + 轮动参数）直接由组合行 `params` 渲染，无需额外拉取；
 * 即时绩效为懒触发——点「运行回测」才打 `api.backtest`，组合行响应带 `combo` 渲染年化 / 时间线（R1）。
 */
export default function ComboDetailModal({
  combo,
  strategies,
  editDisabled,
  onClose,
  onEdit,
  onDelete,
}: {
  combo: Strategy;
  strategies: Strategy[];
  // 正在编辑另一组合时禁用「编辑」，避免静默覆盖未保存脏草稿（R5b，与列表「编辑他项」同口径）。
  editDisabled?: boolean;
  onClose: () => void;
  onEdit: (combo: Strategy) => void;
  onDelete: (combo: Strategy) => void;
}) {
  const titleId = `combo-detail-${combo.id}`;
  const members = (Array.isArray(combo.params?.members)
    ? (combo.params.members as ComboMemberSpec[])
    : []);
  const policy = extractComboPolicy(combo.params ?? {});
  const schema = combo.paramsSchema ?? {};

  return (
    <Modal open onClose={onClose} labelledBy={titleId}>
      <header className="flex items-center justify-between gap-3 border-b border-[var(--border)] px-5 py-3.5">
        <div className="flex min-w-0 items-center gap-2">
          <h2 id={titleId} className="truncate text-sm font-semibold text-[var(--text)]">
            {combo.name}
          </h2>
          <Badge tone="accent">组合</Badge>
          {combo.builtin && <Badge tone="neutral">内置</Badge>}
        </div>
        <Button variant="ghost" size="sm" onClick={onClose} aria-label="关闭详情">
          关闭
        </Button>
      </header>

      <div className="flex flex-col gap-5 overflow-y-auto px-5 py-4">
        <OverviewSection memberCount={members.length} policy={policy} />
        <MembersSection members={members} strategies={strategies} />
        <RotationSection
          policy={policy}
          labelOf={(key) => schema[key]?.label ?? POLICY_LABELS[key] ?? key}
        />
        <PerformanceSection combo={combo} />
      </div>

      <footer className="flex items-center justify-end gap-2 border-t border-[var(--border)] px-5 py-3">
        {!combo.builtin && (
          <>
            <Button
              variant="default"
              size="sm"
              disabled={editDisabled}
              title={editDisabled ? "请先保存或放弃当前编辑" : undefined}
              onClick={() => onEdit(combo)}
            >
              编辑
            </Button>
            <Button variant="danger" size="sm" onClick={() => onDelete(combo)}>
              删除
            </Button>
          </>
        )}
        <Button variant="ghost" size="sm" onClick={onClose}>
          关闭
        </Button>
      </footer>
    </Modal>
  );
}

function OverviewSection({ memberCount, policy }: { memberCount: number; policy: ComboPolicy }) {
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

function MembersSection({
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

function MemberCard({ member, strategies }: { member: ComboMemberSpec; strategies: Strategy[] }) {
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

function RotationSection({
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

function PerformanceSection({ combo }: { combo: Strategy }) {
  const { instId, bar, simBalance, backtestStartTs, backtestEndTs } = useAppStore();
  const rangeReady = backtestStartTs > 0 && backtestEndTs > 0 && backtestStartTs < backtestEndTs;
  const backtest = useMutation({
    mutationFn: () =>
      api.backtest(combo.id, {
        instId,
        bar,
        limit: 1000,
        startTs: backtestStartTs,
        endTs: backtestEndTs,
        initialCapital: simBalance,
      }),
  });
  const comboResult = backtest.data?.combo;
  return (
    <section className="flex flex-col gap-2">
      <div className="flex items-center justify-between gap-2">
        <SectionTitle>即时绩效</SectionTitle>
        <Button
          variant="primary"
          size="sm"
          disabled={!rangeReady || backtest.isPending}
          title={rangeReady ? undefined : "请先在左侧选择回测区间"}
          onClick={() => backtest.mutate()}
        >
          {backtest.isPending ? "回测中…" : "运行回测"}
        </Button>
      </div>
      {!rangeReady && (
        <p className="text-[11px] text-[var(--muted)]">回测区间未就绪：请先在左侧图表选择起止时间。</p>
      )}
      {backtest.isError && (
        <div className="rounded-[8px] border border-[var(--short)] bg-[var(--surface)] px-3 py-2 text-[11px] text-[var(--short)]">
          回测失败：{(backtest.error as Error)?.message}
        </div>
      )}
      {backtest.data && (
        <div className="flex flex-col gap-3">
          {comboResult && (
            <div className="rounded-[10px] border border-[var(--accent)] bg-[var(--accent-soft)] px-3 py-2">
              <div className="text-[11px] text-[var(--accent)]">期望年化收益</div>
              <div
                className="tabular text-xl font-bold"
                style={{ color: colorForReturn(comboResult.annualizedReturn) }}
              >
                {fmtPct(comboResult.annualizedReturn)}
              </div>
            </div>
          )}
          <MetricsCards metrics={backtest.data.metrics} />
          {comboResult && (
            <div className="flex flex-col gap-1.5">
              <span className="text-[11px] font-medium text-[var(--text)]">轮动时间线</span>
              <ComboTimeline segments={comboResult.timeline} />
            </div>
          )}
        </div>
      )}
    </section>
  );
}

function SectionTitle({ children }: { children: React.ReactNode }) {
  return (
    <h3 className="text-xs font-semibold uppercase tracking-wide text-[var(--muted)]">{children}</h3>
  );
}

// 轮动参数值的展示文案：选优指标中文化、布尔→是/否、迟滞→百分比、其余原值。
function formatPolicyValue(key: string, value: unknown): string {
  if (key === "selectionMetric") return SELECTION_METRIC_LABELS[String(value)] ?? String(value);
  if (key === "regimeGating") return value ? "是" : "否";
  if (key === "switchMarginPct") return fmtPct(Number(value));
  return String(value);
}
