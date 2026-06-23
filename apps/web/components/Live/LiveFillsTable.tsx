"use client";

import { useQuery } from "@tanstack/react-query";
import { Badge } from "@/components/ui";
import { fmtNum, fmtPrice, fmtTime } from "@/lib/format";
import { tradingApi } from "@/lib/tradingApi";
import type { LiveFill } from "@/lib/tradingTypes";

// 实盘成交表（§4.5）：8s 轮询真实 OKX 成交明细；鉴权失败显式错误态，绝不假数据（C1）。
export default function LiveFillsTable({
  instId,
  accountId,
}: {
  instId?: string;
  accountId?: number;
}) {
  const { data, isLoading, isError, error } = useQuery({
    queryKey: ["trading", "fills", instId ?? null, accountId ?? null],
    queryFn: () => tradingApi.fills(instId, accountId),
    refetchInterval: 8_000,
    refetchOnWindowFocus: true,
  });

  if (isLoading) {
    return <div className="py-8 text-center text-xs text-[var(--muted)]">加载成交明细…</div>;
  }
  if (isError) {
    return (
      <div className="py-8 text-center text-xs text-[var(--short)]">
        加载成交失败：{(error as Error)?.message || "请稍后重试"}
      </div>
    );
  }
  const fills = data?.fills ?? [];
  if (fills.length === 0) {
    return <div className="py-8 text-center text-xs text-[var(--muted)]">近三日无实盘成交记录。</div>;
  }

  return (
    <table className="w-full text-xs">
      <thead className="sticky top-0 bg-[var(--surface)] text-[var(--muted)]">
        <tr className="text-left">
          <th className="py-1.5 pr-2 font-medium">时间</th>
          <th className="py-1.5 pr-2 font-medium">方向</th>
          <th className="py-1.5 pr-2 text-right font-medium">成交价</th>
          <th className="py-1.5 pr-2 text-right font-medium">数量</th>
          <th className="py-1.5 text-right font-medium">手续费</th>
        </tr>
      </thead>
      <tbody>
        {fills.map((f, i) => (
          <FillRow key={`${f.ordId}-${f.ts}-${i}`} fill={f} />
        ))}
      </tbody>
    </table>
  );
}

function FillRow({ fill }: { fill: LiveFill }) {
  const isBuy = fill.side === "buy";
  return (
    <tr className="border-t border-[var(--border)]">
      <td className="py-1.5 pr-2 tabular text-[var(--muted)]">{fmtTime(fill.ts)}</td>
      <td className="py-1.5 pr-2">
        <Badge tone={isBuy ? "long" : "short"}>{isBuy ? "买入" : "卖出"}</Badge>
      </td>
      <td className="py-1.5 pr-2 text-right tabular">{fmtPrice(fill.fillPx)}</td>
      <td className="py-1.5 pr-2 text-right tabular">{fmtNum(fill.fillSz, 4)}</td>
      <td className="py-1.5 text-right tabular text-[var(--muted)]">{fmtNum(fill.fee, 4)}</td>
    </tr>
  );
}
