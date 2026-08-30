"use client";

import { fmtPrice } from "@/lib/format";
import { useAppStore } from "@/lib/store";
import type { Bar, ChartView, Mode } from "@/lib/types";
import type { OkxConnectivity } from "@/lib/okxLinkTypes";
import ConnectionStatus from "./ConnectionStatus";
import IndicatorMenu from "./IndicatorMenu";

const BARS: Bar[] = ["1m", "5m", "15m", "30m", "1H", "4H", "1D"];
const MODES: { key: Mode; label: string }[] = [
  { key: "backtest", label: "回测" },
  { key: "compare", label: "对比" },
  { key: "paper", label: "实时" },
];

// 标的选择已迁至 InstrumentList（chart-workspace-layout #10）；工具栏专注周期 / 指标 / 日期 / 区间 / 模式 / 状态。
export interface ChartToolbarProps {
  // 既有三个 props 一律保留：端点缺失（老后端）时 ConnectionStatus 据它们回落到二元展示。
  okxUp: boolean | null;
  latencyMs?: number | null;
  lastOkTs?: number | null;
  connectivity?: OkxConnectivity | null;
  onConnectivityOpenChange?: (open: boolean) => void;
  onConnectivityProbe?: () => Promise<unknown> | void;
  liveWsOpen?: boolean; // F1：市场实时流连接态（仅非 paper 模式传入）
  lastPrice?: number | null;
  chartView: ChartView;
  rangeAvailable: boolean;
  onChartViewChange: (view: ChartView) => void;
  onJumpDate: (tsSeconds: number) => void;
}

export default function ChartToolbar({
  okxUp,
  latencyMs,
  lastOkTs,
  connectivity,
  onConnectivityOpenChange,
  onConnectivityProbe,
  liveWsOpen,
  lastPrice,
  chartView,
  rangeAvailable,
  onChartViewChange,
  onJumpDate,
}: ChartToolbarProps) {
  const { bar, mode, backtestStartTs, backtestEndTs, setBar, setMode, setBacktestRange } =
    useAppStore();

  return (
    <div
      data-nowrap="row"
      data-testid="chart-toolbar"
      className="flex flex-wrap items-center gap-2 whitespace-nowrap"
    >
      <div className="flex shrink-0 gap-0.5 rounded-[10px] bg-[var(--surface-2)] p-0.5">
        {BARS.map((b) => (
          <button
            key={b}
            onClick={() => setBar(b)}
            className={
              "rounded-[7px] px-2 py-1 text-xs font-medium transition-all " +
              (bar === b
                ? "bg-[var(--surface)] text-[var(--text)] shadow-soft"
                : "text-[var(--muted)] hover:text-[var(--text)]")
            }
          >
            {b}
          </button>
        ))}
      </div>

      {/* IndicatorMenu / ConnectionStatus 的根节点是它们自己文件里的 <div className="relative">
          （下拉定位的包含块，不能动）；工具栏行的 flex item 就是那个根节点，所以收缩控制
          只能加在**外面**这一层。包一层不影响 relative 的定位语义。 */}
      <div className="shrink-0">
        <IndicatorMenu />
      </div>

      <div className="flex shrink-0 gap-0.5 rounded-[10px] bg-[var(--surface-2)] p-0.5">
        <button
          onClick={() => onChartViewChange("live-follow")}
          className={
            "rounded-[7px] px-2.5 py-1 text-xs font-medium transition-all " +
            (chartView === "live-follow"
              ? "bg-[var(--surface)] text-[var(--text)] shadow-soft"
              : "text-[var(--muted)] hover:text-[var(--text)]")
          }
          title="跟随当前标的实时 K 线"
        >
          实时
        </button>
        <button
          onClick={() => onChartViewChange(mode === "compare" ? "compare-range" : "run-range")}
          disabled={!rangeAvailable}
          className={
            "rounded-[7px] px-2.5 py-1 text-xs font-medium transition-all disabled:cursor-not-allowed disabled:opacity-45 " +
            (chartView !== "live-follow"
              ? "bg-[var(--surface)] text-[var(--text)] shadow-soft"
              : "text-[var(--muted)] hover:text-[var(--text)]")
          }
          title="查看回测或对比使用的历史区间"
        >
          区间
        </button>
      </div>

      <input
        type="date"
        onChange={(e) => {
          if (!e.target.value) return;
          const ts = Math.floor(new Date(`${e.target.value}T00:00:00Z`).getTime() / 1000);
          onJumpDate(ts);
        }}
        className="shrink-0 rounded-[10px] border border-[var(--border)] bg-[var(--surface)] px-2 py-1.5 text-xs text-[var(--muted)] hover:border-[var(--accent)]"
        aria-label="跳转日期"
      />

      <input
        type="date"
        value={dateInputValue(backtestStartTs)}
        onChange={(e) => {
          if (!e.target.value) return;
          setBacktestRange(dateStartTs(e.target.value), backtestEndTs);
        }}
        className="shrink-0 rounded-[10px] border border-[var(--border)] bg-[var(--surface)] px-2 py-1.5 text-xs text-[var(--muted)] hover:border-[var(--accent)]"
        aria-label="回测开始日期"
      />

      <input
        type="date"
        value={dateInputValue(backtestEndTs)}
        onChange={(e) => {
          if (!e.target.value) return;
          setBacktestRange(backtestStartTs, dateEndTs(e.target.value));
        }}
        className="shrink-0 rounded-[10px] border border-[var(--border)] bg-[var(--surface)] px-2 py-1.5 text-xs text-[var(--muted)] hover:border-[var(--accent)]"
        aria-label="回测结束日期"
      />

      <div className="ml-auto flex shrink-0 items-center gap-2">
        <div className="flex shrink-0 gap-0.5 rounded-[10px] bg-[var(--surface-2)] p-0.5">
          {MODES.map((m) => (
            <button
              key={m.key}
              onClick={() => setMode(m.key)}
              className={
                "rounded-[7px] px-2.5 py-1 text-xs font-medium transition-all " +
                (mode === m.key
                  ? "bg-[var(--surface)] text-[var(--text)] shadow-soft"
                  : "text-[var(--muted)] hover:text-[var(--text)]")
              }
            >
              {m.label}
            </button>
          ))}
        </div>

        <LivePrice liveWsOpen={liveWsOpen} lastPrice={lastPrice} />
        <div className="shrink-0">
          <ConnectionStatus
            connectivity={connectivity}
            okxUp={okxUp}
            latencyMs={latencyMs}
            lastOkTs={lastOkTs}
            onOpenChange={onConnectivityOpenChange}
            onProbe={onConnectivityProbe}
          />
        </div>
      </div>
    </div>
  );
}

// F1：实时价 chip + 实时流状态脉冲点（仅在传入 lastPrice / liveWsOpen 时渲染）
function LivePrice({
  liveWsOpen,
  lastPrice,
}: {
  liveWsOpen?: boolean;
  lastPrice?: number | null;
}) {
  if (lastPrice == null && liveWsOpen === undefined) return null;
  const open = !!liveWsOpen;
  return (
    <span
      className="flex shrink-0 items-center gap-1.5 rounded-[10px] bg-[var(--surface-2)] px-2 py-1 text-xs"
      title={open ? "实时行情流已连接（最右一根随真实成交跳动）" : "实时行情流连接中…"}
    >
      <span
        className="inline-block h-1.5 w-1.5 shrink-0 rounded-full"
        style={{
          background: open ? "var(--long)" : "var(--muted)",
          animation: open ? "shimmer 1.4s ease-in-out infinite" : undefined,
        }}
      />
      <span className="tabular font-medium text-[var(--text)]">{fmtPrice(lastPrice)}</span>
    </span>
  );
}

function dateInputValue(ts: number): string {
  return new Date(ts * 1000).toISOString().slice(0, 10);
}

function dateStartTs(value: string): number {
  return Math.floor(new Date(`${value}T00:00:00Z`).getTime() / 1000);
}

function dateEndTs(value: string): number {
  return Math.floor(new Date(`${value}T23:59:59Z`).getTime() / 1000);
}
