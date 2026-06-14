"use client";

import { Tabs } from "@/components/ui";
import { colorForReturn, fmtNum, fmtPct, fmtPrice, fmtTime, signed } from "@/lib/format";
import { computeOhlcStats } from "@/lib/ohlc";
import type { Candle, ChartPane } from "@/lib/types";

// 工具栏下方横栏（chart-workspace-layout 需求 1 视图页签 + 需求 2 OHLC 数值横栏）。
// 空安全（R2）：candle / prevClose 可能为 null，统计经纯函数 computeOhlcStats，仅对非空数值着色 / 取符号，否则「—」。
interface ChartHeaderBarProps {
  view: ChartPane;
  onView: (v: ChartPane) => void;
  candle: Candle | null; // 被选中 / 最新一根（price 视图用）
  prevClose: number | null; // 计算涨跌 / 振幅的昨收基准
  equitySummary?: { last: number | null; ret: number | null }; // equity 视图右侧摘要
}

const VIEW_TABS = [
  { key: "price", label: "K线价格走势" },
  { key: "equity", label: "净值" },
];

export default function ChartHeaderBar({
  view,
  onView,
  candle,
  prevClose,
  equitySummary,
}: ChartHeaderBarProps) {
  const { change, changePct, amplitude } = computeOhlcStats(candle, prevClose);
  // R1：共享 Tabs.onChange 形参为宽类型 string，在组件内部收窄后再上抛窄类型 setter。
  const changeColor = change != null ? colorForReturn(change) : "var(--muted)";

  return (
    <div className="flex shrink-0 flex-wrap items-center gap-x-4 gap-y-1 border-b border-[var(--border)] px-4 py-1.5 text-[11px]">
      <div className="w-[208px] shrink-0">
        <Tabs tabs={VIEW_TABS} active={view} onChange={(k) => onView(k as ChartPane)} />
      </div>

      {view === "price" ? (
        <div className="flex flex-wrap items-center gap-x-3 gap-y-0.5 tabular">
          <span className="text-[var(--muted)]">{candle ? fmtTime(candle.ts) : "—"}</span>
          <Field label="开" value={fmtPrice(candle?.o)} />
          <Field label="高" value={fmtPrice(candle?.h)} />
          <Field label="低" value={fmtPrice(candle?.l)} />
          <Field label="收" value={fmtPrice(candle?.c)} />
          <span className="flex items-center gap-1">
            <span className="text-[var(--muted)]">涨跌</span>
            <span className="font-medium" style={{ color: changeColor }}>
              {change != null ? signed(change) : "—"} / {fmtPct(changePct)}
            </span>
          </span>
          <Field label="振幅" value={fmtPct(amplitude)} />
        </div>
      ) : (
        <div className="flex items-center gap-x-3 tabular">
          <Field label="净值" value={equitySummary ? fmtNum(equitySummary.last) : "—"} />
          <span className="flex items-center gap-1">
            <span className="text-[var(--muted)]">收益</span>
            <span
              className="font-medium"
              style={{
                color:
                  equitySummary?.ret != null ? colorForReturn(equitySummary.ret) : "var(--muted)",
              }}
            >
              {fmtPct(equitySummary?.ret)}
            </span>
          </span>
        </div>
      )}
    </div>
  );
}

function Field({ label, value }: { label: string; value: string }) {
  return (
    <span className="flex items-center gap-1">
      <span className="text-[var(--muted)]">{label}</span>
      <span className="font-medium text-[var(--text)]">{value}</span>
    </span>
  );
}
