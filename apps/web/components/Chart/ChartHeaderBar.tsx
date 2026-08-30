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
    <div
      data-nowrap="row"
      data-testid="chart-header-bar"
      className="flex shrink-0 flex-wrap items-center gap-x-4 gap-y-1 whitespace-nowrap border-b border-[var(--border)] px-4 py-1.5 text-[11px]"
    >
      {/* 去掉写死的 w-[208px]：那个数字是照着「K线价格走势」六个字量出来的，余量只有约 7px，
          字体 / 字重任一微调即折行。segmented 的 flex-1 在内容宽容器里按 max-content 分配，
          配合 Tabs 自身的 whitespace-nowrap 后永不折行，窄档下整块掉到下一行。 */}
      <div className="shrink-0">
        <Tabs tabs={VIEW_TABS} active={view} onChange={(k) => onView(k as ChartPane)} />
      </div>

      {/* OHLC / 净值两组：组内每个字段各自 shrink-0（字段内部永不折），但**组本身必须能被
          max-w-full 收住**——只写 shrink-0 会让整组按 max-content（实测 626px）固定下来，
          自身的 flex-wrap 便永远轮不到触发，中栏一窄就整组横向溢出（实测中栏 240px 时溢出
          402px，越过整个盘口列）。写法与下方 paperStrategies 那组同源。 */}
      {view === "price" ? (
        <div className="flex max-w-full shrink-0 flex-wrap items-center gap-x-3 gap-y-0.5 tabular">
          <span className="shrink-0 text-[var(--muted)]">{candle ? fmtTime(candle.ts) : "-"}</span>
          <Field label="开" value={fmtPrice(candle?.o)} />
          <Field label="高" value={fmtPrice(candle?.h)} />
          <Field label="低" value={fmtPrice(candle?.l)} />
          <Field label="收" value={fmtPrice(candle?.c)} />
          <span className="flex shrink-0 items-center gap-1">
            <span className="text-[var(--muted)]">涨跌</span>
            <span className="font-medium" style={{ color: changeColor }}>
              {change != null ? signed(change) : "-"} / {fmtPct(changePct)}
            </span>
          </span>
          <Field label="振幅" value={fmtPct(amplitude)} />
        </div>
      ) : (
        <div className="flex max-w-full shrink-0 flex-wrap items-center gap-x-3 gap-y-0.5 tabular">
          <Field label="净值" value={equitySummary ? fmtNum(equitySummary.last) : "-"} />
          <span className="flex shrink-0 items-center gap-1">
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
        <div className="ml-auto flex max-w-full shrink-0 flex-wrap items-center justify-end gap-1">
          {paperStrategies.map((strategy) => {
            const active = visiblePaperStrategyIds.includes(strategy.strategyId);
            return (
              <button
                key={strategy.strategyId}
                type="button"
                onClick={() => onTogglePaperStrategy?.(strategy.strategyId)}
                className="flex h-6 shrink-0 items-center gap-1 rounded border px-1.5 text-[10px] font-medium"
                style={{
                  borderColor: active ? strategy.color : "var(--border)",
                  color: active ? "var(--text)" : "var(--muted)",
                  background: active ? "var(--surface-2)" : "transparent",
                }}
              >
                <span className="h-2 w-2 shrink-0 rounded-full" style={{ background: strategy.color }} />
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
    <span className="flex shrink-0 items-center gap-1">
      <span className="text-[var(--muted)]">{label}</span>
      <span className="font-medium text-[var(--text)]">{value}</span>
    </span>
  );
}
