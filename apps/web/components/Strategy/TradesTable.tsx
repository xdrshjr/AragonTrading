"use client";

import { useState } from "react";
import type { Trade } from "@/lib/types";
import { colorForReturn, fmtPct, fmtPrice, fmtQty, fmtTime, signed } from "@/lib/format";
import { formatMaxHoldBars, formatRiskValue, riskColumnLabel } from "@/lib/riskDisplay";
import { Badge } from "@/components/ui";
import TradeExplainDetail from "./TradeExplainDetail";

const SIDE_LABEL: Record<Trade["side"], string> = { long: "开多", short: "开空", close: "平仓" };
const SIDE_TONE: Record<Trade["side"], "long" | "short" | "neutral"> = {
  long: "long",
  short: "short",
  close: "neutral",
};

const COLUMN_COUNT = 7;

function riskTitle(trade: Trade): string {
  return formatMaxHoldBars(trade.maxHoldBars) ?? "";
}

export default function TradesTable({ trades, loading }: { trades?: Trade[]; loading?: boolean }) {
  if (loading) {
    return <div className="py-8 text-center text-xs text-[var(--muted)]">加载成交明细…</div>;
  }
  if (!trades || trades.length === 0) {
    return (
      <div className="whitespace-normal break-words py-8 text-center text-xs text-[var(--muted)]">
        暂无成交记录，运行回测或启动模拟后显示。
      </div>
    );
  }

  const ordered = trades.slice().sort((a, b) => b.ts - a.ts);

  return (
    <div className="max-h-[320px] overflow-auto">
      <table className="w-full min-w-[620px] text-xs">
        <thead className="sticky top-0 whitespace-nowrap bg-[var(--surface)] text-[var(--muted)]">
          <tr className="text-left">
            <th className="py-1.5 pr-2 font-medium">时间</th>
            <th className="py-1.5 pr-2 font-medium">方向</th>
            <th className="py-1.5 pr-2 text-right font-medium">价格</th>
            <th className="py-1.5 pr-2 text-right font-medium">数量</th>
            <th className="py-1.5 pr-2 text-right font-medium">盈亏</th>
            <th className="py-1.5 pr-2 text-right font-medium">
              {riskColumnLabel("stopLoss")}
            </th>
            <th className="py-1.5 text-right font-medium">
              {riskColumnLabel("takeProfit")}
            </th>
          </tr>
        </thead>
        <tbody>
          {ordered.map((t, i) => (
            <TradeRow key={t.id ?? `${t.ts}-${i}`} trade={t} />
          ))}
        </tbody>
      </table>
    </div>
  );
}

/**
 * 单条成交行；当 trade 含结构化 `explain`（实盘自动交易成交）时可点击展开触发原因详情，
 * 复用 `TradeExplainDetail`（auto-trade-explainability §三）。无 explain（回测 / 模拟）时行为不变。
 */
function TradeRow({ trade: t }: { trade: Trade }) {
  const [open, setOpen] = useState(false);
  const expandable = Boolean(t.explain);
  return (
    <>
      <tr
        className={
          "whitespace-nowrap border-t border-[var(--border)]" +
          (t.seeded ? " bg-[var(--surface-2)]/40" : "") +
          (expandable ? " cursor-pointer hover:bg-[var(--surface-2)]/50" : "")
        }
        {...(expandable
          ? {
              role: "button",
              tabIndex: 0,
              "aria-expanded": open,
              onClick: () => setOpen((v) => !v),
              onKeyDown: (e: React.KeyboardEvent) => {
                if (e.key === "Enter" || e.key === " ") {
                  e.preventDefault();
                  setOpen((v) => !v);
                }
              },
            }
          : {})}
      >
        {/* 轴 D：成交表读真实墙钟 `wallTs`（`ts` 已是 bar 对齐秒）；缺键回退 `ts`。 */}
        <td className="py-1.5 pr-2 tabular text-[var(--muted)]">{fmtTime(t.wallTs ?? t.ts)}</td>
        <td className="py-1.5 pr-2">
          <span className="inline-flex items-center gap-1">
            <Badge tone={SIDE_TONE[t.side]}>{SIDE_LABEL[t.side]}</Badge>
            {/* B4：预热回填成交以「历史预热」角标区分于实时新成交，避免误读为刚发生（R2）。 */}
            {t.seeded && (
              <span
                title="基于近端真实历史的预热回填，非刚发生的实时成交"
                className="rounded-full border border-[var(--border)] px-1.5 py-px text-[10px] text-[var(--muted)]"
              >
                历史预热
              </span>
            )}
            {expandable && (
              <span className="text-[10px] text-[var(--muted)]">{open ? "▾" : "▸"}</span>
            )}
          </span>
        </td>
        <td className="py-1.5 pr-2 text-right tabular">{fmtPrice(t.price)}</td>
        <td className="py-1.5 pr-2 text-right tabular">{fmtQty(t.qty)}</td>
        <td
          className="py-1.5 pr-2 text-right tabular font-medium"
          style={{ color: colorForReturn(t.pnl) }}
        >
          {t.side === "close" ? (
            <span className="flex flex-col items-end leading-tight">
              <span>{signed(t.pnl)}</span>
              {/* 实盘自动交易平仓携带收益率 pnlPct（已实现盈亏/开仓保证金）；回测 / 模拟无此字段则仅显金额。 */}
              {t.pnlPct != null && <span className="text-[10px] font-normal">{fmtPct(t.pnlPct)}</span>}
            </span>
          ) : (
            "—"
          )}
        </td>
        <td className="py-1.5 pr-2 text-right tabular" title={riskTitle(t)}>
          {formatRiskValue(t.stopLossPct, t.stopLossPrice)}
        </td>
        <td className="py-1.5 text-right tabular" title={riskTitle(t)}>
          {formatRiskValue(t.takeProfitPct, t.takeProfitPrice)}
        </td>
      </tr>
      {expandable && open && (
        <tr className="border-t border-[var(--border)]">
          <td colSpan={COLUMN_COUNT} className="whitespace-normal px-2 pb-3 pt-1">
            <TradeExplainDetail trade={t} />
          </td>
        </tr>
      )}
    </>
  );
}
