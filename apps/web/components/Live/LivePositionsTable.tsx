"use client";

import { useQuery } from "@tanstack/react-query";
import { Badge } from "@/components/ui";
import { colorForReturn, fmtNum, fmtPct, fmtPrice, signed } from "@/lib/format";
import { tradingApi } from "@/lib/tradingApi";
import type { LivePosition } from "@/lib/tradingTypes";

// 实盘持仓表（§4.4）：5s 轮询真实 OKX；鉴权失败显式错误态，绝不假数据（C1）。只读（平仓 out-of-scope，C2-2）。
export default function LivePositionsTable({ accountId }: { accountId?: number }) {
  const { data, isLoading, isError, error } = useQuery({
    queryKey: ["trading", "positions", accountId ?? null],
    queryFn: () => tradingApi.positions(accountId),
    refetchInterval: 5_000,
    refetchOnWindowFocus: true,
  });

  if (isLoading) {
    return <div className="py-8 text-center text-xs text-[var(--muted)]">加载持仓…</div>;
  }
  if (isError) {
    return (
      <div className="py-8 text-center text-xs text-[var(--short)]">
        加载持仓失败：{(error as Error)?.message || "请稍后重试"}
      </div>
    );
  }
  const rows = data?.positions ?? [];
  const open = rows.filter((p) => (p.pos ?? 0) !== 0);
  if (open.length === 0) {
    return <div className="py-8 text-center text-xs text-[var(--muted)]">当前实盘账户无持仓。</div>;
  }

  return (
    <table className="w-full text-xs">
      <thead className="sticky top-0 bg-[var(--surface)] text-[var(--muted)]">
        <tr className="text-left">
          <th className="py-1.5 pr-2 font-medium">合约</th>
          <th className="py-1.5 pr-2 font-medium">方向</th>
          <th className="py-1.5 pr-2 text-right font-medium">持仓</th>
          <th className="py-1.5 pr-2 text-right font-medium">开仓均价</th>
          <th className="py-1.5 pr-2 text-right font-medium">标记价</th>
          <th className="py-1.5 text-right font-medium">浮动盈亏</th>
        </tr>
      </thead>
      <tbody>
        {open.map((p, i) => (
          <PositionRow key={`${p.instId}-${p.posSide}-${i}`} pos={p} />
        ))}
      </tbody>
    </table>
  );
}

function PositionRow({ pos }: { pos: LivePosition }) {
  const isLong = pos.posSide === "long" || (pos.posSide === "net" && (pos.pos ?? 0) > 0);
  return (
    <tr className="border-t border-[var(--border)]">
      <td className="py-1.5 pr-2 font-medium text-[var(--text)]">{pos.instId}</td>
      <td className="py-1.5 pr-2">
        <Badge tone={isLong ? "long" : "short"}>{isLong ? "多" : "空"}</Badge>
      </td>
      <td className="py-1.5 pr-2 text-right tabular">
        {fmtNum(pos.pos, 0)}
        {pos.lever != null ? (
          <span className="ml-1 text-[10px] text-[var(--muted)]">{pos.lever}×</span>
        ) : null}
      </td>
      <td className="py-1.5 pr-2 text-right tabular">{fmtPrice(pos.avgPx)}</td>
      <td className="py-1.5 pr-2 text-right tabular">{fmtPrice(pos.last)}</td>
      <td
        className="py-1.5 text-right tabular font-medium"
        style={{ color: colorForReturn(pos.upl ?? 0) }}
      >
        {pos.upl != null ? signed(pos.upl) : "—"} / {fmtPct(pos.uplRatio)}
      </td>
    </tr>
  );
}
