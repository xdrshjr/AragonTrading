"use client";

import clsx from "clsx";
import { Badge } from "@/components/ui";
import { colorForReturn, fmtPct, fmtPrice, fmtTime, signed } from "@/lib/format";
import type { RoundTrip } from "@/lib/autoTradePnl";
import { readRMultiple } from "@/lib/aiTradeProfitability";
import TradeExplainDetail from "../Strategy/TradeExplainDetail";
import TradeCandleChartPanel from "./TradeCandleChartPanel";

// 一条「完整交易」往返（开→平）行（auto-trade-pnl-records §3.4）：折叠态以盈亏 / 收益率为视觉焦点，
// 整行可展开查看平仓触发原因（复用 TradeExplainDetail）。键盘可达、渐进披露。
//
// ai-trade-stats-two-column-and-kline §3.5 / P1-3：新增**可选** `bar?`。仅 AI 统计看板向 row 传 `bar` →
// 放宽可展开并在展开态额外渲染该往返窗口的行内 K 线；其它调用点（live-auto / paper 面板）不传 `bar`，
// `expandable` 仍 = `Boolean(rt.exit.explain)`、展开仍只有 `TradeExplainDetail` → 逐字节不变（零回归）。
//
// ai-trade-realized-edge-calibration [P1-3]：再新增**可选** `showR?`。仅 AI 统计看板传 true →
// 在时间行尾部多渲染一列 R 倍数（`exit.rMultiple`，算不出即整列省略）；其它调用点不传 → 逐字节不变。
export default function AutoTradeRoundTripRow({
  rt,
  open,
  onToggle,
  bar,
  showR,
}: {
  rt: RoundTrip;
  open: boolean;
  onToggle: () => void;
  bar?: string;
  showR?: boolean;
}) {
  const tone = colorForReturn(rt.pnlUsd);
  const expandable = Boolean(bar) || Boolean(rt.exit.explain);
  // 历史通道读顶层、实时通道读 explain.meta —— 两条通道的落点不同，故收口在 `readRMultiple`。
  const rMultiple = showR ? readRMultiple(rt.exit) : null;
  return (
    <div className="border-t border-[var(--border)] first:border-t-0">
      <button
        type="button"
        aria-expanded={expandable ? open : undefined}
        onClick={expandable ? onToggle : undefined}
        className={clsx(
          "flex w-full items-center gap-3 px-3 py-2 text-left text-xs",
          expandable && "cursor-pointer hover:bg-[var(--surface-2)]/50",
        )}
      >
        <div className="flex min-w-0 flex-1 flex-col gap-0.5">
          <span className="flex items-center gap-1.5">
            <Badge tone={rt.direction === "long" ? "long" : "short"}>
              {rt.direction === "long" ? "开多" : "开空"}
            </Badge>
            <span className="truncate font-medium text-[var(--text)]">{rt.instId}</span>
            {expandable && (
              <span className="text-[10px] text-[var(--muted)]">{open ? "▾" : "▸"}</span>
            )}
          </span>
          <span className="tabular text-[10px] text-[var(--muted)]">
            {fmtPrice(rt.entry.price)} → {fmtPrice(rt.exit.price)} · {fmtTime(rt.closedTs)} ·
            持仓 {formatHoldDuration(rt.closedTs - rt.openedTs)}
            {showR && rMultiple != null && (
              <span data-testid="ai-round-trip-r"> · R {rMultiple.toFixed(2)}</span>
            )}
          </span>
        </div>
        <div className="flex shrink-0 flex-col items-end gap-0.5 tabular">
          <span className="text-sm font-semibold" style={{ color: tone }}>
            {signed(rt.pnlUsd)}
          </span>
          <span className="text-[11px] font-medium" style={{ color: tone }}>
            {fmtPct(rt.pnlPct)}
          </span>
        </div>
      </button>
      {expandable && open && (
        <div className="flex flex-col gap-2 px-3 pb-3 pt-0.5">
          {bar && (
            <TradeCandleChartPanel instId={rt.instId} bar={bar} entry={rt.entry} exit={rt.exit} />
          )}
          {rt.exit.explain && <TradeExplainDetail trade={rt.exit} />}
        </div>
      )}
    </div>
  );
}

// 持仓时长（秒）→ 紧凑可读串：分 / 时 / 天，至多两段，避免占用过宽空间。
function formatHoldDuration(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds));
  if (s < 60) return `${s}秒`;
  const minutes = Math.floor(s / 60);
  if (minutes < 60) return `${minutes}分`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) {
    const rem = minutes % 60;
    return rem > 0 ? `${hours}时${rem}分` : `${hours}时`;
  }
  const days = Math.floor(hours / 24);
  const remHours = hours % 24;
  return remHours > 0 ? `${days}天${remHours}时` : `${days}天`;
}
