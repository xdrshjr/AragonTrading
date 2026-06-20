"use client";

import { Badge } from "@/components/ui";
import { colorForReturn, fmtMoney, fmtPct, fmtPrice, signed } from "@/lib/format";
import type { PaperPositionRow } from "@/lib/paperMulti";

// 实时模拟「当前持仓」按策略列表（Fix B）：每条运行中策略一行，并列展示各自方向 / 持仓量 /
// 开仓均价 / 标记价 / 浮动盈亏 与当前模拟资金（净值）。持仓为空时该行诚实显示「当前无持仓」。
interface PaperPositionsTableProps {
  rows: PaperPositionRow[];
  instId: string;
}

export default function PaperPositionsTable({ rows, instId }: PaperPositionsTableProps) {
  if (rows.length === 0) {
    return (
      <div className="py-8 text-center text-xs text-[var(--muted)]">
        当前无持仓，启动实时模拟后按策略显示。
      </div>
    );
  }

  return (
    <div className="max-h-[320px] overflow-auto">
      <table className="w-full text-xs">
        <thead className="sticky top-0 bg-[var(--surface)] text-[var(--muted)]">
          <tr className="text-left">
            <th className="py-1.5 pr-2 font-medium">策略</th>
            <th className="py-1.5 pr-2 font-medium">方向</th>
            <th className="py-1.5 pr-2 text-right font-medium">持仓量</th>
            <th className="py-1.5 pr-2 text-right font-medium">开仓均价</th>
            <th className="py-1.5 pr-2 text-right font-medium">标记价</th>
            <th className="py-1.5 pr-2 text-right font-medium">浮动盈亏</th>
            <th className="py-1.5 text-right font-medium">模拟资金</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <PositionRow key={row.strategyId} row={row} />
          ))}
        </tbody>
      </table>
      <div className="pt-1.5 text-[10px] text-[var(--muted)]">合约 {instId}</div>
    </div>
  );
}

function PositionRow({ row }: { row: PaperPositionRow }) {
  const pos = row.position;
  const pnlColor = pos?.unrealizedPnl != null ? colorForReturn(pos.unrealizedPnl) : "var(--muted)";
  return (
    <tr className="border-t border-[var(--border)]">
      <td className="py-1.5 pr-2">
        <span className="inline-flex items-center gap-1.5">
          <span className="h-2 w-2 shrink-0 rounded-full" style={{ background: row.color }} />
          <span className="font-medium text-[var(--text)]">{row.strategyName}</span>
        </span>
      </td>
      {pos ? (
        <>
          <td className="py-1.5 pr-2">
            <Badge tone={pos.side === "long" ? "long" : "short"}>
              {pos.side === "long" ? "多" : "空"}
            </Badge>
          </td>
          <td className="py-1.5 pr-2 text-right tabular text-[var(--text)]">{pos.size.toFixed(4)}</td>
          <td className="py-1.5 pr-2 text-right tabular text-[var(--text)]">{fmtPrice(pos.avgEntry)}</td>
          <td className="py-1.5 pr-2 text-right tabular text-[var(--text)]">{fmtPrice(pos.markPrice)}</td>
          <td className="py-1.5 pr-2 text-right tabular font-medium" style={{ color: pnlColor }}>
            {pos.unrealizedPnl != null ? signed(pos.unrealizedPnl) : "—"} / {fmtPct(pos.unrealizedPct)}
          </td>
        </>
      ) : (
        <td className="py-1.5 pr-2 text-[var(--muted)]" colSpan={5}>
          当前无持仓
        </td>
      )}
      <td className="py-1.5 text-right tabular font-medium text-[var(--text)]">
        {row.equity != null ? fmtMoney(row.equity) : "—"}
      </td>
    </tr>
  );
}
