"use client";

import { useState } from "react";
import type {
  AiTradeProfitabilityCoverage,
} from "@/lib/aiTradeProfitability";

const REASONS: Record<string, string> = {
  basis_unknown: "盈亏口径未知", fee_unknown: "手续费证据未知",
  economics_incomplete: "经济证据不完整", net_invalid: "净额无效",
  net_unknown: "净额未知", net_nonfinite: "净额无效",
  orphan_close: "孤立平仓", quantity_conflict: "成交数量冲突",
  missing_open: "孤立平仓", invalid_quantity: "成交数量无效",
  invalid_side: "成交方向无效", position_epoch_changed: "持仓周期冲突",
  close_quantity_exceeded: "平仓数量超出持仓",
  position_quantity_mismatch: "持仓数量不一致",
  invalid_trade: "成交字段无效", incomplete_close: "平仓数量不完整",
};

function reasonText(code: string): string {
  return REASONS[code] ?? code;
}

function reasonsText(reasons: Record<string, number>): string {
  return Object.entries(reasons).map(([reason, count]) =>
    `${reasonText(reason)} ${count} 笔`).join("；") || "无排除";
}

/** 逐项展示服务端覆盖与原因；不从样本金额重新推断资格。 */
export default function ProfitabilityCoverage({ coverage, schemaVersion }: {
  coverage?: AiTradeProfitabilityCoverage;
  schemaVersion?: number;
}) {
  const [expanded, setExpanded] = useState(false);
  if (schemaVersion != null && schemaVersion !== 2) {
    return <p className="text-[10px] text-[var(--muted)]">
      此版本覆盖详情暂不可读
    </p>;
  }
  if (!coverage) return <p className="text-[10px] text-[var(--muted)]">
    旧版未提供总体覆盖
  </p>;
  return <div className="flex min-w-0 flex-col gap-1 text-[10px]">
    <div className="flex flex-wrap gap-x-3 gap-y-1 tabular">
      <span>可核算 {coverage.netEligibleTrips} / 总往返 {coverage.totalTrips}</span>
      <span>实测净额 {coverage.measuredNetTrips} / {coverage.totalTrips}</span>
    </div>
    {coverage.netEligibleTrips > coverage.measuredNetTrips &&
      <p className="text-[var(--muted)]">金额含估算或来源未确认</p>}
    {coverage.netEligibleTrips === 0 && coverage.totalTrips > 0 &&
      <p className="text-[var(--muted)]">费用或口径证据不足</p>}
    <button type="button" aria-expanded={expanded}
      onClick={() => setExpanded((value) => !value)}
      className="self-start rounded text-[var(--muted)] hover:text-[var(--text)] focus-visible:outline focus-visible:outline-2 focus-visible:outline-[var(--accent)]">
      {expanded ? "▾" : "▸"} 覆盖明细与未纳入原因
    </button>
    {expanded && <CoverageDetails coverage={coverage} />}
  </div>;
}

function CoverageDetails({ coverage }: {
  coverage: AiTradeProfitabilityCoverage;
}) {
  return <div className="flex min-w-0 flex-col gap-1 text-[var(--muted)]">
    <p className="break-words">{reasonsText(coverage.excludedByReason)}</p>
    {coverage.structuralIssueCount > 0 && <p>
      结构异常 {coverage.structuralIssueCount} 条
    </p>}
    {(coverage.structuralIssues ?? []).map((issue, index) =>
      <p key={`${issue.runId}:${issue.eventKey}:${index}`}
        className="break-words">
        {reasonText(issue.reason)} · 运行 {issue.runId}
      </p>)}
    <div className="min-w-0 max-w-full overflow-x-auto"
      role="region" aria-label="各标的覆盖" tabIndex={0}>
      <table className="w-full text-left">
        <thead className="whitespace-nowrap"><tr>
          <th className="pr-3">标的</th><th className="pr-3">总往返</th>
          <th className="pr-3">可核算</th><th>未纳入原因</th>
        </tr></thead>
        <tbody>{(coverage.byInstId ?? []).map((row) =>
          <tr key={row.instId}>
            <td className="whitespace-nowrap pr-3">{row.instId}</td>
            <td className="whitespace-nowrap pr-3 tabular">{row.totalTrips}</td>
            <td className="whitespace-nowrap pr-3 tabular">{row.netEligibleTrips}</td>
            <td className="min-w-[140px] break-words">
              {reasonsText(row.excludedByReason)}
            </td>
          </tr>)}</tbody>
      </table>
    </div>
  </div>;
}
