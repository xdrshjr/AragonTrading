"use client";

import { useQuery } from "@tanstack/react-query";
import { DISCIPLINE_COPY } from "@/lib/aiTradeDiscipline";
import { aiTradeHistoryApi } from "@/lib/aiTradeHistoryApi";
import type { AiTradePresetComparisonGroup } from "@/lib/aiTradeTypes";
import { colorForReturn, signed } from "@/lib/format";

/**
 * 历史纪律预设对照卡（ai-trade-tradability-and-open-funnel 轴 E）。
 *
 * 它偿还的是上一轮 G-17 欠账的一半：「这组阈值到底是让系统赚钱还是让系统停摆」。用的是**真实资金
 * 的历史**，而不是一次 50 分钟跑不完的两臂 LLM 回测——后者的结论**不可迁移**：回测与实盘的仓位
 * 求解至今仍不同构（回测侧没有先验运行时，凯利项恒不参与 `min`）。
 *
 * 两条呈现纪律：
 * - `conclusive === false` 必须**显式**写「样本不足，不下结论」（灰色，不是「快通过了」）；
 * - 文案**不得**出现「已校准 / 经回测验证」——这是一张历史统计，不是收益承诺。
 */
export default function PresetComparisonCard() {
  const query = useQuery({
    queryKey: ["aiTrade", "presetComparison"],
    queryFn: aiTradeHistoryApi.presetComparison,
  });
  const groups = query.data?.groups ?? [];
  // 没有历史 ⇒ 整卡不渲染（绝不在一个没有数据的账户上挂一张空表）。
  if (query.isPending || groups.length === 0) return null;

  return (
    <div
      className="flex flex-col gap-1.5 rounded-md border border-[var(--border)] bg-[var(--surface)] px-3 py-2"
      data-testid="ai-preset-comparison-card"
    >
      <div className="flex items-baseline justify-between gap-2">
        <span className="text-xs text-[var(--muted)]">历史纪律预设对照</span>
        <span className="tabular text-[10px] text-[var(--muted)]">
          满 {query.data?.minSamples ?? 3} 局才下结论
        </span>
      </div>
      <div className="flex flex-col divide-y divide-[var(--border)]">
        {groups.map((group) => (
          <PresetRow key={group.preset} group={group} />
        ))}
      </div>
      <p className="text-[10px] leading-relaxed text-[var(--muted)]">
        按每次会话启动时生效的预设分组，聚合已落库的
        <strong className="text-[var(--text)]">真实资金历史</strong>
        。「样本不足」的组同样给出全部数字，但
        <strong className="text-[var(--text)]">不构成任何结论</strong>
        ；缺少预设记录的老会话独立成「未记录」一组，绝不并进任一具名档。
      </p>
    </div>
  );
}

function PresetRow({ group }: { group: AiTradePresetComparisonGroup }) {
  // 预设名来自落库的历史数据，可能是任意字符串（含老会话的 `unknown`），故按宽键查表。
  const copy = DISCIPLINE_COPY as Record<string, { label: string } | undefined>;
  const label = copy[group.preset]?.label ?? presetFallbackLabel(group.preset);
  return (
    <div
      className="flex flex-col gap-0.5 py-1"
      data-testid={`ai-preset-comparison-row-${group.preset}`}
    >
      <div className="flex items-center justify-between gap-2 text-[11px]">
        <span className="flex min-w-0 items-center gap-1.5">
          <span className="truncate text-[var(--text)]">{label}</span>
          {!group.conclusive && (
            <span
              className="shrink-0 rounded-[3px] border border-[var(--border)] px-1 py-px text-[9px] text-[var(--muted)]"
              data-testid={`ai-preset-comparison-inconclusive-${group.preset}`}
            >
              样本不足，不下结论
            </span>
          )}
        </span>
        <span
          className="tabular shrink-0 font-medium"
          style={{ color: colorForReturn(group.netPnlUsd) }}
        >
          {signed(group.netPnlUsd)} USDT
        </span>
      </div>
      <span className="tabular text-[10px] text-[var(--muted)]">
        {group.samples} 局 · {group.roundTrips} 笔往返 · 胜率 {pct(group.winRate)} · 门禁否决{" "}
        {pct(group.edgeVetoRate)} · 意愿→成交 {pct(group.openConversion)}
      </span>
    </div>
  );
}

/** 算不出一律「—」，**绝不**显示 0%（那是一个不同的结论）。 */
function pct(value: number | null): string {
  return value == null || !Number.isFinite(value) ? "—" : `${(value * 100).toFixed(0)}%`;
}

function presetFallbackLabel(preset: string): string {
  return preset === "unknown" ? "未记录预设" : preset;
}
