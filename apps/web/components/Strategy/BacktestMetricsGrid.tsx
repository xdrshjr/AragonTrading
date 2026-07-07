"use client";

import { fmtDuration } from "@/lib/backtestStats";
import { colorForReturn, fmtMoney, fmtNum, fmtPct } from "@/lib/format";
import type { ExtendedMetrics, Metrics } from "@/lib/types";

interface Cell {
  label: string;
  value: string;
  color?: string;
  hint?: string;
}

const N = "var(--text)";
const DASH = "—";

// 收益 / 风险组：既有总收益/夏普/回撤 + 扩展的年化/波动/Sortino/Calmar/水下时长/在场占比（缺失渲染「—」）。
function riskItems(metrics: Metrics, ext?: ExtendedMetrics): Cell[] {
  return [
    { label: "总收益率", value: fmtPct(metrics.totalReturn), color: colorForReturn(metrics.totalReturn) },
    {
      label: "年化收益",
      value: ext ? fmtPct(ext.annualizedReturn) : DASH,
      color: ext ? colorForReturn(ext.annualizedReturn) : N,
      hint: "CAGR：按年化的复合收益率",
    },
    {
      label: "年化波动",
      value: ext ? fmtPct(ext.annualizedVolatility) : DASH,
      color: N,
      hint: "逐根收益率的年化标准差（样本方差 ddof=1）",
    },
    { label: "夏普比率", value: fmtNum(metrics.sharpe), color: colorForReturn(metrics.sharpe) },
    {
      label: "索提诺",
      value: ext ? fmtNum(ext.sortino) : DASH,
      color: ext ? colorForReturn(ext.sortino) : N,
      hint: "下行风险调整收益：分母仅计下行波动",
    },
    {
      label: "卡玛",
      value: ext ? fmtNum(ext.calmar) : DASH,
      color: ext ? colorForReturn(ext.calmar) : N,
      hint: "年化收益 ÷ 最大回撤幅度",
    },
    {
      label: "最大回撤",
      value: fmtPct(metrics.maxDrawdown),
      color: metrics.maxDrawdown < 0 ? "var(--short)" : N,
    },
    {
      label: "最长水下",
      value: ext ? fmtDuration(ext.maxDrawdownSecs) : DASH,
      color: N,
      hint: "权益连续处于回撤中的最长时长",
    },
    {
      label: "在场占比",
      value: ext ? fmtPct(ext.timeInMarketPct) : DASH,
      color: N,
      hint: "持仓根数 ÷ 总根数",
    },
  ];
}

// 交易质量组：胜率/盈利因子/成交笔数/平均每笔 + 扩展的赔率/平均盈亏/连胜连亏/极值/持仓/频率。
function qualityItems(metrics: Metrics, ext?: ExtendedMetrics): Cell[] {
  return [
    { label: "胜率", value: fmtPct(metrics.winRate), color: N },
    {
      label: "盈利因子",
      value: fmtNum(metrics.profitFactor),
      color: N,
      hint: "毛盈利 ÷ 毛亏损（区别于「赔率」＝平均盈利/平均亏损）",
    },
    {
      label: "盈亏比·赔率",
      value: ext ? fmtNum(ext.payoffRatio) : DASH,
      color: N,
      hint: "平均盈利 ÷ 平均亏损（赔率）",
    },
    { label: "成交笔数", value: String(metrics.tradeCount), color: N },
    { label: "平均每笔", value: fmtMoney(metrics.avgPnl), color: colorForReturn(metrics.avgPnl) },
    {
      label: "平均盈利",
      value: ext ? fmtMoney(ext.avgWin) : DASH,
      color: ext ? "var(--long)" : N,
      hint: "平仓盈利笔的平均已实现盈亏",
    },
    {
      label: "平均亏损",
      value: ext ? fmtMoney(ext.avgLoss) : DASH,
      color: ext ? "var(--short)" : N,
      hint: "平仓亏损笔的平均已实现亏损（绝对值）",
    },
    { label: "单笔最大盈利", value: ext ? fmtMoney(ext.largestWin) : DASH, color: ext ? "var(--long)" : N },
    { label: "单笔最大亏损", value: ext ? fmtMoney(ext.largestLoss) : DASH, color: ext ? "var(--short)" : N },
    { label: "最大连胜", value: ext ? `${ext.maxConsecutiveWins} 笔` : DASH, color: N },
    { label: "最大连亏", value: ext ? `${ext.maxConsecutiveLosses} 笔` : DASH, color: N },
    {
      label: "平均持仓",
      value: ext ? `${fmtNum(ext.avgHoldBars)} 根` : DASH,
      color: N,
      hint: "每笔开→平的平均持仓 K 线数",
    },
    { label: "交易频率", value: ext ? `${fmtNum(ext.tradesPerWeek)} 笔/周` : DASH, color: N },
  ];
}

function MetricCard({ cell }: { cell: Cell }) {
  return (
    <div
      title={cell.hint}
      className="rounded-[10px] border border-[var(--border)] bg-[var(--surface-2)] px-3 py-2"
    >
      <div className="text-[10px] text-[var(--muted)]">{cell.label}</div>
      <div className="tabular text-sm font-semibold" style={{ color: cell.color ?? N }}>
        {cell.value}
      </div>
    </div>
  );
}

function Section({ title, items }: { title: string; items: Cell[] }) {
  return (
    <div className="flex flex-col gap-2">
      <div className="text-[11px] font-semibold text-[var(--muted)]">{title}</div>
      <div className="grid grid-cols-3 gap-2">
        {items.map((cell) => (
          <MetricCard key={cell.label} cell={cell} />
        ))}
      </div>
    </div>
  );
}

/**
 * 弹窗内绩效指标网格（strategy-backtest-config-modal §2.5）：既有 7 项 + 扩展 16 项，分「收益/风险」与
 * 「交易质量」两组。扩展项缺失（组合旧路径 / 旧数据）时统一渲染「—」，口径与 metrics.py 逐字对齐。
 */
export default function BacktestMetricsGrid({
  metrics,
  extended,
}: {
  metrics: Metrics;
  extended?: ExtendedMetrics;
}) {
  return (
    <div className="flex flex-col gap-3">
      <Section title="收益 / 风险" items={riskItems(metrics, extended)} />
      <Section title="交易质量" items={qualityItems(metrics, extended)} />
    </div>
  );
}
