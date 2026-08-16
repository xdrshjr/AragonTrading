"use client";

import { extractComboPolicy } from "@/lib/combo";
import { compareKindLabel } from "@/lib/compare";
import { POLICY_LABELS } from "@/lib/comboPolicyHints";
import { directionLabel } from "@/lib/factor";
import type { FactorScoreSnapshotParams } from "@/lib/factorTypes";
import { fmtTime } from "@/lib/format";
import { useAppStore } from "@/lib/store";
import type { CompareItem, ComboMemberSpec, Strategy } from "@/lib/types";
import { Badge, Button, Modal } from "@/components/ui";
import {
  MembersSection,
  OverviewSection,
  RotationSection,
  SectionTitle,
} from "./ComboDetailSections";
import MetricsCards from "./MetricsCards";

interface FactorMemberSnapshot {
  factorKey: string;
  direction?: string;
}

/**
 * 策略详情弹窗（只读 · 顶级设计）：复用 `Modal` 原语（焦点陷阱 / Esc / 遮罩 / 滚动锁 / 焦点恢复），
 * 按 `kind`（原子 / 组合 / 因子）自适应展示完整内容。对比语境不夹带运行 / 编辑 / 删除——纯信息，
 * 命中当次对比时附「本次对比绩效」。组合分区复用 `ComboDetailSections`，因子分区复用快照只读化。
 */
export default function StrategyDetailModal({
  strategy,
  strategies,
  compareItem,
  onClose,
}: {
  strategy: Strategy;
  strategies: Strategy[];
  compareItem?: CompareItem;
  onClose: () => void;
}) {
  const titleId = `strategy-detail-${strategy.id}`;
  return (
    <Modal open onClose={onClose} labelledBy={titleId}>
      <header className="flex items-center justify-between gap-3 border-b border-[var(--border)] px-5 py-3.5">
        <div className="flex min-w-0 items-center gap-2">
          <h2 id={titleId} className="truncate text-sm font-semibold text-[var(--text)]">
            {strategy.name}
          </h2>
          <Badge tone={strategy.kind === "combo" ? "accent" : "neutral"}>
            {compareKindLabel(strategy.kind)}
          </Badge>
          {strategy.builtin && <Badge tone="neutral">内置</Badge>}
          {strategy.unavailable && <Badge tone="short">不可用</Badge>}
        </div>
        <Button variant="ghost" size="sm" onClick={onClose} aria-label="关闭详情">
          关闭
        </Button>
      </header>

      <div className="flex flex-col gap-5 overflow-y-auto px-5 py-4">
        {strategy.description && (
          <p className="text-xs leading-relaxed text-[var(--muted)]">{strategy.description}</p>
        )}
        <OverviewGrid strategy={strategy} />
        {strategy.kind === "combo" ? (
          <ComboDetail strategy={strategy} strategies={strategies} />
        ) : strategy.kind === "factor" ? (
          <FactorDetail strategy={strategy} />
        ) : (
          <AtomicDetail strategy={strategy} />
        )}
        {compareItem && <ComparePerformance strategy={strategy} compareItem={compareItem} onClose={onClose} />}
      </div>

      <footer className="flex items-center justify-end gap-2 border-t border-[var(--border)] px-5 py-3">
        <Button variant="ghost" size="sm" onClick={onClose}>
          关闭
        </Button>
      </footer>
    </Modal>
  );
}

// 通用概览：类型 / 模板键 / 是否内置 / 状态（可用-不可用）。
function OverviewGrid({ strategy }: { strategy: Strategy }) {
  const items = [
    { label: "类型", value: compareKindLabel(strategy.kind) },
    { label: "模板键", value: strategy.templateKey },
    { label: "内置", value: strategy.builtin ? "是" : "否" },
    { label: "状态", value: strategy.unavailable ? "不可用" : "可用" },
  ];
  return (
    <section className="flex flex-col gap-2">
      <SectionTitle>基本信息</SectionTitle>
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        {items.map((it) => (
          <div
            key={it.label}
            className="rounded-[10px] border border-[var(--border)] bg-[var(--surface-2)] px-3 py-2"
          >
            <div className="text-[11px] text-[var(--muted)]">{it.label}</div>
            <div className="tabular truncate text-sm font-semibold text-[var(--text)]">{it.value}</div>
          </div>
        ))}
      </div>
    </section>
  );
}

// 原子策略：从 paramsSchema 渲染只读参数网格（值取 params[key] ?? default）+ 高周期参考徽标。
function AtomicDetail({ strategy }: { strategy: Strategy }) {
  const schema = strategy.paramsSchema ?? {};
  const params = strategy.params ?? {};
  const htfOn = params.htf_trend_filter !== false;
  const htfBar = typeof params.htf_bar === "string" && htfOn ? params.htf_bar : null;
  const entries = Object.entries(schema);
  return (
    <section className="flex flex-col gap-2">
      <SectionTitle>策略参数</SectionTitle>
      {htfBar && (
        <span className="inline-flex w-fit items-center gap-1 rounded-[6px] bg-[var(--accent-soft)] px-2 py-0.5 text-[11px] font-medium text-[var(--accent-ink)]">
          参考周期：{htfBar}
        </span>
      )}
      {entries.length === 0 ? (
        <p className="text-[11px] text-[var(--muted)]">该策略无可调参数</p>
      ) : (
        <div className="grid grid-cols-2 gap-2">
          {entries.map(([key, def]) => (
            <div
              key={key}
              className="flex flex-col gap-0.5 rounded-[8px] border border-[var(--border)] bg-[var(--surface)] px-2.5 py-1.5"
            >
              <span className="text-[11px] text-[var(--muted)]">{def.label}</span>
              <span className="tabular text-xs font-medium text-[var(--text)]">
                {formatParamValue(def.type, params[key] ?? def.default)}
              </span>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}

// 参数值展示：布尔→是/否、其余原值（枚举原文 / 数值直读）。
function formatParamValue(type: string, value: unknown): string {
  if (type === "bool") return value ? "是" : "否";
  if (value === null || value === undefined) return "—";
  return String(value);
}

// 因子组合得分策略：成员方向徽标 + 最小所需根数 + 训练溯源（FactorStrategyBanner 的只读化复刻）。
function FactorDetail({ strategy }: { strategy: Strategy }) {
  const members = (strategy.params?.members as FactorMemberSnapshot[] | undefined) ?? [];
  const thresholds = (strategy.params?.thresholds as { normWindow?: number } | undefined) ?? {};
  const minBars = Number(thresholds.normWindow ?? 200) + 60;
  const provenance = (strategy.params as Partial<FactorScoreSnapshotParams>)?.provenance;
  const hasRange = provenance?.startTs != null && provenance?.endTs != null;
  return (
    <section className="flex flex-col gap-2">
      <SectionTitle>因子组合得分</SectionTitle>
      <p className="text-[11px] text-[var(--muted)]">
        消费独立因子层的复合得分做决策（内联快照 {members.length} 个因子成员）。
      </p>
      <p className="text-[11px] text-[var(--accent-ink)]">
        至少需约 {minBars} 根数据才会产出信号（区间过短将安静空仓）。
      </p>
      {members.length > 0 && (
        <div className="flex flex-wrap gap-1">
          {members.map((m, i) => (
            <Badge key={`${m.factorKey}-${i}`} tone={m.direction === "short" ? "short" : "long"}>
              {m.factorKey}·{directionLabel(m.direction ?? "long")}
            </Badge>
          ))}
        </div>
      )}
      {hasRange && provenance && (
        <div className="flex flex-col gap-0.5">
          <p className="text-[11px] text-[var(--muted)]">
            训练于 {provenance.instId ?? "—"} · {provenance.bar ?? "—"} · [{fmtTime(provenance.startTs)},{" "}
            {fmtTime(provenance.endTs)}]{provenance.horizon != null ? ` · 前瞻 ${provenance.horizon} 根` : ""}
          </p>
          <p className="text-[11px] text-[var(--muted)]">
            若对比区间落在上述训练区间内即为样本内，结果可能偏乐观，建议用样本外区间验证。
          </p>
        </div>
      )}
    </section>
  );
}

// 组合策略：复用抽出的只读分区（概览 / 成员 / 轮动参数）；不含懒回测绩效（对比面板已产出绩效）。
function ComboDetail({ strategy, strategies }: { strategy: Strategy; strategies: Strategy[] }) {
  const members = Array.isArray(strategy.params?.members)
    ? (strategy.params.members as ComboMemberSpec[])
    : [];
  const policy = extractComboPolicy(strategy.params ?? {});
  const schema = strategy.paramsSchema ?? {};
  return (
    <>
      <OverviewSection memberCount={members.length} policy={policy} />
      <MembersSection members={members} strategies={strategies} />
      <RotationSection
        policy={policy}
        labelOf={(key) => schema[key]?.label ?? POLICY_LABELS[key] ?? key}
      />
    </>
  );
}

// 本次对比绩效：命中当次对比榜单项时展示该策略指标卡 + 「打开该策略回测」入口。
function ComparePerformance({
  strategy,
  compareItem,
  onClose,
}: {
  strategy: Strategy;
  compareItem: CompareItem;
  onClose: () => void;
}) {
  const { setStrategyId, setRunId, setMode, setChartView } = useAppStore();
  const openBacktest = () => {
    setStrategyId(strategy.id);
    setRunId(compareItem.runId);
    setMode("backtest");
    setChartView("run-range");
    onClose();
  };
  return (
    <section className="flex flex-col gap-2">
      <div className="flex items-center justify-between gap-2">
        <SectionTitle>本次对比绩效</SectionTitle>
        <Button variant="ghost" size="sm" onClick={openBacktest}>
          打开该策略回测
        </Button>
      </div>
      <MetricsCards metrics={compareItem.metrics} />
    </section>
  );
}
