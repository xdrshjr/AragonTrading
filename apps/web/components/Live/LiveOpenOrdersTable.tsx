"use client";

import { useQuery } from "@tanstack/react-query";
import clsx from "clsx";
import { Badge } from "@/components/ui";
import { fmtNum, fmtPrice, fmtTime } from "@/lib/format";
import { openOrderKindLabel, openOrdersKey } from "@/lib/openOrders";
import { tradingApi } from "@/lib/tradingApi";
import type { LiveOpenOrder } from "@/lib/tradingTypes";

// state → 中文（ai-trade-limit-orders-fix §2.6）：未知值原样透传，不臆造。
const STATE_LABELS: Record<string, string> = {
  live: "已挂出",
  partially_filled: "部分成交",
};

const POS_SIDE_LABELS: Record<string, string> = { long: "多", short: "空", net: "净" };

/**
 * 「委托交易」页签（F-5）：**全量**未结委托、不过滤——与持仓子行的本质差异是四类 kind 全收、
 * 无持仓匹配要求、跨 instId 全账户展示（AI 挂出的开仓限价单成交前没有持仓，唯有这里可见）。
 * 与持仓表 / 图表共用 `openOrdersKey` 查询键（C2 同源去重，不产生第三路轮询）。只读视图：
 * 撤单由 AI 会话自管（cancel_order + TTL 兜底），外部挂单请去 OKX 侧操作（§七 Non-goals）。
 */
export default function LiveOpenOrdersTable({
  instId,
  accountId,
}: {
  instId?: string;
  accountId?: number;
}) {
  const { data, isLoading, isError, error } = useQuery({
    queryKey: openOrdersKey(accountId),
    queryFn: () => tradingApi.openOrders(accountId),
    refetchInterval: 5_000,
    refetchOnWindowFocus: true,
  });

  if (isLoading) {
    return <div className="py-8 text-center text-xs text-[var(--muted)]">加载委托…</div>;
  }
  if (isError) {
    return (
      <div className="py-8 text-center text-xs text-[var(--short)]">
        加载委托失败：{(error as Error)?.message || "请稍后重试"}
      </div>
    );
  }
  const orders = data?.orders ?? [];
  if (orders.length === 0) {
    return (
      <div className="py-8 text-center text-xs text-[var(--muted)]">
        当前无未结委托。AI 自动交易挂出的限价委托与止盈止损委托会显示在这里。
      </div>
    );
  }
  return <OpenOrdersTableView orders={orders} instId={instId} />;
}

/** 排序（评审 R-5 复合次序）：先按「是否当前 instId」分组置顶，组内 createdTs 降序。 */
export function sortOpenOrders(orders: LiveOpenOrder[], instId?: string): LiveOpenOrder[] {
  return [...orders].sort((a, b) => {
    const aGroup = instId && a.instId === instId ? 0 : 1;
    const bGroup = instId && b.instId === instId ? 0 : 1;
    if (aGroup !== bGroup) return aGroup - bGroup;
    return b.createdTs - a.createdTs;
  });
}

/** 行 key 与图表价格线 reconcile 主键同构：`${ordId ?? algoId}:${kind}`（OCO 的 tp/sl 不撞）。 */
function orderRowKey(order: LiveOpenOrder): string {
  return `${order.ordId ?? order.algoId}:${order.kind}`;
}

/** 纯展示表（与 query 解耦，便于单测；镜像 FillsTableView 范式）。 */
export function OpenOrdersTableView({
  orders,
  instId,
}: {
  orders: LiveOpenOrder[];
  instId?: string;
}) {
  const sorted = sortOpenOrders(orders, instId);
  return (
    <table className="w-full text-xs">
      <thead className="sticky top-0 bg-[var(--surface)] text-[var(--muted)]">
        <tr className="text-left">
          <th className="py-1.5 pr-2 font-medium">合约</th>
          <th className="py-1.5 pr-2 font-medium">类型</th>
          <th className="py-1.5 pr-2 font-medium">方向</th>
          <th className="py-1.5 pr-2 text-right font-medium">委托价</th>
          <th className="py-1.5 pr-2 text-right font-medium">触发价</th>
          <th className="py-1.5 pr-2 text-right font-medium">数量</th>
          <th className="py-1.5 pr-2 text-right font-medium">随单止盈</th>
          <th className="py-1.5 pr-2 text-right font-medium">随单止损</th>
          <th className="py-1.5 pr-2 font-medium">状态</th>
          <th className="py-1.5 font-medium">挂出时间</th>
        </tr>
      </thead>
      <tbody>
        {sorted.map((order) => (
          <OpenOrderRow
            key={orderRowKey(order)}
            order={order}
            highlighted={order.instId === instId}
          />
        ))}
      </tbody>
    </table>
  );
}

function OpenOrderRow({
  order,
  highlighted,
}: {
  order: LiveOpenOrder;
  highlighted: boolean;
}) {
  const isBuy = order.side === "buy";
  // tp/sl 触发后市价（px=null）显示「市价」；普通挂单 px 恒有值，缺失兜底 "—"（fmtPrice 语义）。
  const pxText =
    order.px == null && (order.kind === "tp" || order.kind === "sl") ? "市价" : fmtPrice(order.px);
  return (
    <tr
      className={clsx(
        "border-t border-[var(--border)]",
        highlighted && "bg-[var(--surface-2)]/50",
      )}
    >
      <td className="py-1.5 pr-2">{order.instId}</td>
      <td className="py-1.5 pr-2">{openOrderKindLabel(order.kind)}</td>
      <td className="py-1.5 pr-2">
        <span className="flex items-center gap-1">
          <Badge tone={isBuy ? "long" : "short"}>{isBuy ? "买入" : "卖出"}</Badge>
          <Badge>{POS_SIDE_LABELS[order.posSide] ?? order.posSide}</Badge>
        </span>
      </td>
      <td className="py-1.5 pr-2 text-right tabular">{pxText}</td>
      <td className="py-1.5 pr-2 text-right tabular">{fmtPrice(order.triggerPx)}</td>
      <td className="py-1.5 pr-2 text-right tabular">{fmtNum(order.sz, 4)}</td>
      <td className="py-1.5 pr-2 text-right tabular">{fmtPrice(order.attachTpTriggerPx)}</td>
      <td className="py-1.5 pr-2 text-right tabular">{fmtPrice(order.attachSlTriggerPx)}</td>
      <td className="py-1.5 pr-2">{STATE_LABELS[order.state] ?? order.state}</td>
      <td className="py-1.5 tabular text-[var(--muted)]">{fmtTime(order.createdTs)}</td>
    </tr>
  );
}
