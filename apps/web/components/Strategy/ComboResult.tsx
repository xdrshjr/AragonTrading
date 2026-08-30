"use client";

import { groupBreakdownByRegime } from "@/lib/combo";
import { colorForReturn, fmtPct } from "@/lib/format";
import { regimeLabel } from "@/lib/regimeAffinity";
import type { ComboPreviewResult, Metrics } from "@/lib/types";
import ComboTimeline from "./ComboTimeline";
import MetricsCards from "./MetricsCards";

/**
 * 组合回测结果区（自 ComboView 抽出）：期望年化 + 绩效卡 + 轮动时间线 + 分体制贡献。
 * 弹窗预览（ComboEditorModal）与列表选中组合回测（StrategyPanel 绩效页签）同源消费，杜绝重复实现。
 */
export default function ComboResult({
  result,
  onGoCompare,
}: {
  result: { metrics: Metrics; combo: ComboPreviewResult["combo"] };
  onGoCompare?: () => void;
}) {
  const { metrics, combo } = result;
  return (
    <div className="flex flex-col gap-3">
      <div className="rounded-[10px] border border-[var(--accent)] bg-[var(--accent-soft)] px-3 py-2">
        <div className="text-[11px] text-[var(--accent-ink)]">期望年化收益</div>
        <div
          className="tabular text-xl font-bold"
          style={{ color: colorForReturn(combo.annualizedReturn) }}
        >
          {fmtPct(combo.annualizedReturn)}
        </div>
      </div>

      <MetricsCards metrics={metrics} />

      <div className="flex flex-col gap-1.5">
        <span className="text-[11px] font-medium text-[var(--text)]">轮动时间线</span>
        <ComboTimeline segments={combo.timeline} />
      </div>

      <BreakdownTable rows={combo.breakdown} />

      {combo.notes && combo.notes.length > 0 && (
        <ul className="flex flex-col gap-0.5 text-[11px] text-[var(--muted)]">
          {combo.notes.map((note, i) => (
            <li key={i}>· {note}</li>
          ))}
        </ul>
      )}

      {onGoCompare && (
        <div className="flex flex-wrap items-center gap-2">
          <button
            onClick={onGoCompare}
            className="rounded-[6px] px-2 py-0.5 text-[11px] text-[var(--accent-ink)] hover:bg-[var(--accent-soft)]"
          >
            在对比中查看已保存组合 →
          </button>
        </div>
      )}
    </div>
  );
}

function BreakdownTable({ rows }: { rows: ComboPreviewResult["combo"]["breakdown"] }) {
  if (!rows.length) return null;
  const groups = groupBreakdownByRegime(rows);
  return (
    <div className="flex flex-col gap-1.5">
      <span className="text-[11px] font-medium text-[var(--text)]">分体制贡献</span>
      {/* 四张策略面板表里唯一没有自有滚动宿主的一张：不新建这层，横向压力会传导到
          整块 StrategyPanel 的滚动条上（§4.5.3 / K2）。 */}
      <div className="overflow-x-auto">
        <table className="w-full min-w-[360px] text-xs">
          <thead className="whitespace-nowrap text-[var(--muted)]">
            <tr className="text-left">
              <th className="py-1 pr-2 font-medium">体制 / 成员</th>
              <th className="py-1 pr-2 text-right font-medium">根数</th>
              <th className="py-1 text-right font-medium">贡献</th>
            </tr>
          </thead>
          <tbody>
            {groups.map((group) => (
              <RegimeRows key={group.regime} group={group} />
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function RegimeRows({ group }: { group: ReturnType<typeof groupBreakdownByRegime>[number] }) {
  return (
    <>
      <tr className="whitespace-nowrap border-t border-[var(--border)]">
        <td className="py-1 pr-2 font-medium text-[var(--text)]">{regimeLabel(group.regime)}</td>
        <td className="py-1 pr-2 text-right tabular text-[var(--muted)]">{group.bars}</td>
        <td className="py-1 text-right" />
      </tr>
      {group.rows.map((row) => (
        <tr key={`${group.regime}-${row.memberKey}`} className="whitespace-nowrap">
          <td className="py-1 pl-3 pr-2 text-[var(--muted)]">{row.memberName}</td>
          <td className="py-1 pr-2 text-right tabular">{row.bars}</td>
          <td
            className="py-1 text-right tabular"
            style={{ color: colorForReturn(row.contribution) }}
          >
            {fmtPct(row.contribution)}
          </td>
        </tr>
      ))}
    </>
  );
}
