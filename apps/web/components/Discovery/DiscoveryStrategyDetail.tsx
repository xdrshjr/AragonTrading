"use client";

import EquityChart from "@/components/Chart/EquityChart";
import {
  CLIP_MODE_CN,
  axisLabel,
  fmtNumber,
  fmtPercent,
  formatParamValue,
  paramDiffRows,
  sideRows,
} from "@/lib/discoveryStrategy";
import type {
  DiscoveryCandidateDetail as Detail,
  DiscoveryEquityPoint,
  DiscoveryStrategyMetrics,
} from "@/lib/discoveryTypes";
import { useAppStore } from "@/lib/store";
import DiscoveryGateLadder from "./DiscoveryGateLadder";

const IS_COLOR = "#4f8cff";
const OOS_COLOR = "#f0a63a";

function toOverlay(id: number, name: string, color: string, points: DiscoveryEquityPoint[]) {
  return {
    id,
    name,
    color,
    equity: points.map((point) => ({ ts: point.ts, equity: point.equity, drawdown: 0 })),
  };
}

/**
 * 策略候选详情：参数对照 / 成员清单 / IS·OOS 双段净值 / S 闸阶梯 / 口径说明。
 *
 * 双段净值走 `EquityChart` 的 **`overlays` 分支**：该组件的 `equity` 与 `overlays` 两个入参
 * **互斥**且右轴口径不同（原值 vs 归一化百分比）。两段各自归一化到起点，正好是我们要的
 * 可比形态；混传会让右轴变成一笔糊涂账（评审 P2-9）。
 */
export default function DiscoveryStrategyDetail({ detail }: { detail: Detail }) {
  const theme = useAppStore((s) => s.theme);
  const metrics = (detail.metrics ?? {}) as Partial<DiscoveryStrategyMetrics>;
  const equityIs = detail.series.equityIs ?? [];
  const equityOos = detail.series.equityOos ?? [];
  const overlays = [
    equityIs.length ? toOverlay(1, "样本内", IS_COLOR, equityIs) : null,
    equityOos.length ? toOverlay(2, "样本外", OOS_COLOR, equityOos) : null,
  ].filter(Boolean) as ReturnType<typeof toOverlay>[];
  const diff = paramDiffRows(metrics);
  const members = metrics.members ?? [];

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-2 text-[11px] text-[var(--muted)]">
        <span className="rounded-full bg-[var(--surface-2)] px-2 py-0.5 text-[var(--text)]">
          {axisLabel(detail.kind)}
        </span>
        {metrics.templateKey && (
          <span title={metrics.templateKey}>模板 {metrics.templateName ?? metrics.templateKey}</span>
        )}
        <span>入库时试验数 {metrics.trials ?? "—"}</span>
        <span>夏普门槛 {fmtNumber(metrics.requiredSharpe, 3)}</span>
        <span>暖机前伸 {metrics.warmupPad ?? "—"} 根</span>
      </div>

      <div className="grid grid-cols-2 gap-3">
        {[
          { title: "样本内（IS）", side: metrics.is },
          { title: "样本外（OOS）", side: metrics.oos },
        ].map(({ title, side }) => (
          <div key={title} className="rounded-[10px] border border-[var(--border)] p-2.5">
            <h4 className="mb-1 text-xs font-medium text-[var(--text)]">{title}</h4>
            <dl className="grid grid-cols-2 gap-x-3 gap-y-0.5 text-[11px] tabular">
              {sideRows(side).map((row) => (
                <div key={row.label} className="flex justify-between gap-2">
                  <dt className="text-[var(--muted)]">{row.label}</dt>
                  <dd className="text-[var(--text)]">{row.value}</dd>
                </div>
              ))}
            </dl>
          </div>
        ))}
      </div>

      <div>
        <h4 className="mb-1 text-xs font-medium text-[var(--text)]">样本内 / 样本外净值（各自归一化到起点）</h4>
        <div className="h-[180px] rounded-[10px] border border-[var(--border)]">
          <EquityChart
            overlays={overlays}
            theme={theme === "dark" ? "dark" : "light"}
            emptyHint="本候选没有留存净值曲线。"
          />
        </div>
        <p className="mt-1 text-[10px] leading-relaxed text-[var(--muted)]">
          样本外口径：{CLIP_MODE_CN[metrics.clipMode ?? "gated"]}
        </p>
      </div>

      <div>
        <h4 className="mb-1 text-xs font-medium text-[var(--text)]">硬闸阶梯（S1–S9）</h4>
        <DiscoveryGateLadder gates={detail.series.gates} rejectReason={detail.rejectReason} />
        {metrics.stress && (
          <p className="mt-1 text-[10px] text-[var(--muted)]">
            成本压力：以 {fmtNumber(metrics.stress.costMult, 1)} 倍成本重跑样本内，
            总收益 {fmtPercent(metrics.stress.totalReturn)}。
          </p>
        )}
      </div>

      {diff.length > 0 && (
        <div>
          <h4 className="mb-1 text-xs font-medium text-[var(--text)]">参数对照（默认值 vs 挖到的值）</h4>
          <table className="w-full text-left text-[11px]">
            <thead className="text-[var(--muted)]">
              <tr>
                <th scope="col" className="py-1">参数</th>
                <th scope="col" className="py-1 text-right">默认</th>
                <th scope="col" className="py-1 text-right">挖到</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-[var(--border)]">
              {diff.map((row) => (
                <tr key={row.key}>
                  <td className="py-1 text-[var(--text)]">{row.label}</td>
                  <td className="py-1 text-right tabular text-[var(--muted)]">
                    {formatParamValue(row.default)}
                  </td>
                  <td className="py-1 text-right tabular font-medium text-[var(--accent-ink)]">
                    {formatParamValue(row.value)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {members.length > 0 && (
        <div>
          <h4 className="mb-1 text-xs font-medium text-[var(--text)]">成员清单</h4>
          <ul className="flex flex-col gap-0.5 text-[11px]">
            {members.map((member, index) => (
              <li key={index} className="flex items-center justify-between gap-2">
                <span className="truncate text-[var(--text)]">
                  {/* `label` 是后端 describe_strategy 的中文渲染；缺了才回落裸标识符。 */}
                  {String(member.label ?? member.name ?? member.templateKey ?? member.factorKey ?? "—")}
                </span>
                <span className="shrink-0 tabular text-[var(--muted)]">
                  {member.weight !== undefined
                    ? `权重 ${fmtNumber(member.weight, 3)}`
                    : JSON.stringify(member.params ?? {})}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}

      <p className="text-[10px] leading-relaxed text-[var(--muted)]">
        成本口径：本金 {metrics.costs?.initialCapital ?? "—"}、taker {metrics.costs?.takerFeeBps ?? "—"} bps、
        滑点 {metrics.costs?.slippageBps ?? "—"} bps、杠杆 {metrics.costs?.leverage ?? "—"}。
        与已入库策略的最大逐根收益相关性 {fmtNumber(metrics.maxEquityCorr, 3)}。
        以上均为历史样本统计，不构成任何收益预期或投资建议。
      </p>
    </div>
  );
}
