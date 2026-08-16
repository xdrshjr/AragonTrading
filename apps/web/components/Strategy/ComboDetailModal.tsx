"use client";

import { useMutation } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { extractComboPolicy } from "@/lib/combo";
import { POLICY_LABELS } from "@/lib/comboPolicyHints";
import { colorForReturn, fmtPct } from "@/lib/format";
import { useAppStore } from "@/lib/store";
import type { ComboMemberSpec, Strategy } from "@/lib/types";
import { Badge, Button, Modal } from "@/components/ui";
import {
  MembersSection,
  OverviewSection,
  RotationSection,
  SectionTitle,
} from "./ComboDetailSections";
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
        <div className="rounded-[8px] border border-[var(--short)] bg-[var(--surface)] px-3 py-2 text-[11px] text-[var(--short-ink)]">
          回测失败：{(backtest.error as Error)?.message}
        </div>
      )}
      {backtest.data && (
        <div className="flex flex-col gap-3">
          {comboResult && (
            <div className="rounded-[10px] border border-[var(--accent)] bg-[var(--accent-soft)] px-3 py-2">
              <div className="text-[11px] text-[var(--accent-ink)]">期望年化收益</div>
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
