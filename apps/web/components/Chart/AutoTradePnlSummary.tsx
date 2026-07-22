"use client";

import clsx from "clsx";
import { colorForReturn, fmtPct, signed } from "@/lib/format";
import type { AutoTradeRange, PnlSummary } from "@/lib/autoTradePnl";

// 顶部时间维度筛选（segmented control）+ 总收益 / 总收益率 KPI 概览卡（auto-trade-pnl-records §3.4）。
// 主指标（总收益 / 总收益率）大字置左，次指标弱化，遵循视觉层级与 HCI 最佳实践。
const RANGE_LABELS: Record<AutoTradeRange, string> = {
  today: "今日",
  "7d": "近7日",
  "30d": "近30日",
  all: "全部",
  custom: "自定义",
};

export default function AutoTradePnlSummary({
  range,
  onRangeChange,
  custom,
  onCustomChange,
  summary,
  kpiCols = 4,
}: {
  range: AutoTradeRange;
  onRangeChange: (range: AutoTradeRange) => void;
  custom: { startTs?: number; endTs?: number };
  onCustomChange: (custom: { startTs?: number; endTs?: number }) => void;
  summary: PnlSummary;
  // 次级 KPI 列数：默认 4（既有调用点类名等价、行为零回归）；窄栏（AI 统计看板左栏）传 2 避免拥挤。
  kpiCols?: 2 | 4;
}) {
  return (
    <div className="flex flex-col gap-3">
      <div
        role="group"
        aria-label="时间维度筛选"
        className="flex flex-wrap items-center gap-0.5 rounded-md bg-[var(--surface-2)] p-0.5"
      >
        {(Object.keys(RANGE_LABELS) as AutoTradeRange[]).map((r) => (
          <button
            key={r}
            type="button"
            aria-pressed={range === r}
            onClick={() => onRangeChange(r)}
            className={clsx(
              "rounded px-2.5 py-1 text-[11px] font-medium transition-colors",
              range === r
                ? "bg-[var(--surface)] text-[var(--text)] shadow-sm"
                : "text-[var(--muted)] hover:text-[var(--text)]",
            )}
          >
            {RANGE_LABELS[r]}
          </button>
        ))}
      </div>

      {range === "custom" && (
        <div className="flex flex-wrap items-center gap-2 text-[11px] text-[var(--muted)]">
          <label className="flex items-center gap-1.5">
            起
            <input
              type="date"
              aria-label="自定义起始日期"
              value={secToDateInput(custom.startTs)}
              onChange={(e) =>
                onCustomChange({ ...custom, startTs: dateInputToStartSec(e.target.value) })
              }
              className="rounded border border-[var(--border)] bg-[var(--bg)] px-2 py-1 text-[var(--text)] outline-none focus:border-[var(--accent)]"
            />
          </label>
          <label className="flex items-center gap-1.5">
            至
            <input
              type="date"
              aria-label="自定义结束日期"
              value={secToDateInput(custom.endTs)}
              onChange={(e) =>
                onCustomChange({ ...custom, endTs: dateInputToEndSec(e.target.value) })
              }
              className="rounded border border-[var(--border)] bg-[var(--bg)] px-2 py-1 text-[var(--text)] outline-none focus:border-[var(--accent)]"
            />
          </label>
        </div>
      )}

      <div className="grid grid-cols-2 gap-2">
        <PrimaryKpi
          label="总收益（USDT）"
          value={signed(summary.totalPnlUsd)}
          color={colorForReturn(summary.totalPnlUsd)}
        />
        <PrimaryKpi
          label="总收益率（基于投入预算）"
          value={fmtPct(summary.totalReturnPct)}
          color={colorForReturn(summary.totalReturnPct)}
        />
      </div>

      <div className={clsx("grid gap-2", kpiCols === 2 ? "grid-cols-2" : "grid-cols-2 sm:grid-cols-4")}>
        <Kpi label="完整交易" value={`${summary.count} 笔`} />
        <Kpi label="胜率" value={fmtPct(summary.winRate)} />
        <Kpi label="最大单笔盈" value={signed(summary.bestUsd)} color={colorForReturn(summary.bestUsd)} />
        <Kpi label="最大单笔亏" value={signed(summary.worstUsd)} color={colorForReturn(summary.worstUsd)} />
      </div>
    </div>
  );
}

function PrimaryKpi({ label, value, color }: { label: string; value: string; color: string }) {
  return (
    <div className="flex flex-col gap-1 rounded-lg border border-[var(--border)] bg-[var(--surface)] px-3 py-2.5">
      <span className="text-[10px] uppercase tracking-wide text-[var(--muted)]">{label}</span>
      <span className="tabular text-xl font-semibold" style={{ color }}>
        {value}
      </span>
    </div>
  );
}

function Kpi({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <div className="flex flex-col gap-0.5 rounded-md bg-[var(--surface-2)] px-2.5 py-1.5">
      <span className="text-[10px] text-[var(--muted)]">{label}</span>
      <span className="tabular text-sm font-medium" style={{ color: color ?? "var(--text)" }}>
        {value}
      </span>
    </div>
  );
}

// 秒级时间戳 → date 输入值（YYYY-MM-DD，本地时区）；空 / 非正回落空串。
function secToDateInput(ts?: number): string {
  if (ts == null || ts <= 0) return "";
  const d = new Date(ts * 1000);
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

// date 输入值 → 当日本地零点 / 当日本地末刻的秒级时间戳（闭区间，含起止当天全部成交）；空 / 非法回落 undefined。
function dateInputToStartSec(value: string): number | undefined {
  return dateInputToSec(value, [0, 0, 0, 0]);
}

function dateInputToEndSec(value: string): number | undefined {
  return dateInputToSec(value, [23, 59, 59, 999]);
}

function dateInputToSec(value: string, time: [number, number, number, number]): number | undefined {
  if (!value) return undefined;
  const [y, m, day] = value.split("-").map(Number);
  if (!y || !m || !day) return undefined;
  return Math.floor(new Date(y, m - 1, day, ...time).getTime() / 1000);
}

function pad(n: number): string {
  return n < 10 ? `0${n}` : `${n}`;
}
