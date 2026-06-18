"use client";

import { Tabs } from "@/components/ui";
import { colorForReturn, fmtNum, fmtPct, fmtPrice, fmtTime, signed } from "@/lib/format";
import { computeOhlcStats } from "@/lib/ohlc";
import type { Candle, ChartPane, PaperStrategySession } from "@/lib/types";

interface ChartHeaderBarProps {
  view: ChartPane;
  onView: (v: ChartPane) => void;
  candle: Candle | null;
  prevClose: number | null;
  equitySummary?: { last: number | null; ret: number | null };
  paperStrategies?: PaperStrategySession[];
  visiblePaperStrategyIds?: number[];
  onTogglePaperStrategy?: (id: number) => void;
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
  paperStrategies = [],
  visiblePaperStrategyIds = [],
  onTogglePaperStrategy,
}: ChartHeaderBarProps) {
  const { change, changePct, amplitude } = computeOhlcStats(candle, prevClose);
  const changeColor = change != null ? colorForReturn(change) : "var(--muted)";

  return (
    <div className="flex shrink-0 flex-wrap items-center gap-x-4 gap-y-1 border-b border-[var(--border)] px-4 py-1.5 text-[11px]">
      <div className="w-[208px] shrink-0">
        <Tabs tabs={VIEW_TABS} active={view} onChange={(k) => onView(k as ChartPane)} />
      </div>

      {view === "price" ? (
        <div className="flex flex-wrap items-center gap-x-3 gap-y-0.5 tabular">
          <span className="text-[var(--muted)]">{candle ? fmtTime(candle.ts) : "-"}</span>
          <Field label="开" value={fmtPrice(candle?.o)} />
          <Field label="高" value={fmtPrice(candle?.h)} />
          <Field label="低" value={fmtPrice(candle?.l)} />
          <Field label="收" value={fmtPrice(candle?.c)} />
          <span className="flex items-center gap-1">
            <span className="text-[var(--muted)]">涨跌</span>
            <span className="font-medium" style={{ color: changeColor }}>
              {change != null ? signed(change) : "-"} / {fmtPct(changePct)}
            </span>
          </span>
          <Field label="振幅" value={fmtPct(amplitude)} />
        </div>
      ) : (
        <div className="flex items-center gap-x-3 tabular">
          <Field label="净值" value={equitySummary ? fmtNum(equitySummary.last) : "-"} />
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

      {paperStrategies.length > 1 && (
        <div className="ml-auto flex max-w-full flex-wrap items-center justify-end gap-1">
          {paperStrategies.map((strategy) => {
            const active = visiblePaperStrategyIds.includes(strategy.strategyId);
            return (
              <button
                key={strategy.strategyId}
                type="button"
                onClick={() => onTogglePaperStrategy?.(strategy.strategyId)}
                className="flex h-6 items-center gap-1 rounded border px-1.5 text-[10px] font-medium"
                style={{
                  borderColor: active ? strategy.color : "var(--border)",
                  color: active ? "var(--text)" : "var(--muted)",
                  background: active ? "var(--surface-2)" : "transparent",
                }}
              >
                <span className="h-2 w-2 rounded-full" style={{ background: strategy.color }} />
                <span className="max-w-[72px] truncate">{strategy.strategyName}</span>
              </button>
            );
          })}
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
