"use client";

import type { Trade } from "@/lib/types";
import { fmtPrice, fmtTime, signed } from "@/lib/format";
import { Badge } from "@/components/ui";

const SIDE_LABEL: Record<Trade["side"], string> = { long: "开多", short: "开空", close: "平仓" };
const SIDE_TONE: Record<Trade["side"], "long" | "short" | "neutral"> = {
  long: "long",
  short: "short",
  close: "neutral",
};

export default function TradesTable({ trades, loading }: { trades?: Trade[]; loading?: boolean }) {
  if (loading) {
    return <div className="py-8 text-center text-xs text-[var(--muted)]">加载成交明细…</div>;
  }
  if (!trades || trades.length === 0) {
    return (
      <div className="py-8 text-center text-xs text-[var(--muted)]">
        暂无成交记录，运行回测或启动模拟后显示。
      </div>
    );
  }

  const ordered = trades.slice().sort((a, b) => b.ts - a.ts);

  return (
    <div className="max-h-[320px] overflow-auto">
      <table className="w-full text-xs">
        <thead className="sticky top-0 bg-[var(--surface)] text-[var(--muted)]">
          <tr className="text-left">
            <th className="py-1.5 pr-2 font-medium">时间</th>
            <th className="py-1.5 pr-2 font-medium">方向</th>
            <th className="py-1.5 pr-2 text-right font-medium">价格</th>
            <th className="py-1.5 pr-2 text-right font-medium">数量</th>
            <th className="py-1.5 text-right font-medium">盈亏</th>
          </tr>
        </thead>
        <tbody>
          {ordered.map((t, i) => (
            <tr key={t.id ?? `${t.ts}-${i}`} className="border-t border-[var(--border)]">
              <td className="py-1.5 pr-2 tabular text-[var(--muted)]">{fmtTime(t.ts)}</td>
              <td className="py-1.5 pr-2">
                <Badge tone={SIDE_TONE[t.side]}>{SIDE_LABEL[t.side]}</Badge>
              </td>
              <td className="py-1.5 pr-2 text-right tabular">{fmtPrice(t.price)}</td>
              <td className="py-1.5 pr-2 text-right tabular">{t.qty.toFixed(4)}</td>
              <td
                className="py-1.5 text-right tabular font-medium"
                style={{ color: t.pnl > 0 ? "var(--long)" : t.pnl < 0 ? "var(--short)" : "var(--muted)" }}
              >
                {t.side === "close" ? signed(t.pnl) : "—"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
