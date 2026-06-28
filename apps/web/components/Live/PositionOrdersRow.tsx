"use client";

import { fmtNum, fmtPrice } from "@/lib/format";
import { orderKindMeta, pctChange } from "@/lib/openOrders";
import type { LiveOpenOrder } from "@/lib/tradingTypes";

// 单个持仓下方的委托明细：列出该持仓的止盈 / 止损 / 限价平仓委托（默认展开，空态给文案）。
// refPx 为该持仓现值（pos.last，C1），用于每条委托相对最新价的涨跌幅着色。
export default function PositionOrdersRow({
  orders,
  refPx,
}: {
  orders: LiveOpenOrder[];
  refPx: number | null;
}) {
  if (orders.length === 0) {
    return (
      <div className="px-2 py-1.5 text-[11px] text-[var(--muted)]">
        该持仓暂无止盈 / 止损 / 平仓挂单。
      </div>
    );
  }
  return (
    <div className="flex flex-col gap-1 px-2 py-1.5">
      {orders.map((order) => (
        <OrderDetail key={`${order.ordId ?? order.algoId}:${order.kind}`} order={order} refPx={refPx} />
      ))}
    </div>
  );
}

function OrderDetail({ order, refPx }: { order: LiveOpenOrder; refPx: number | null }) {
  const meta = orderKindMeta(order.kind);
  const price = order.triggerPx ?? order.px;
  const pct = price != null ? pctChange(price, refPx) : null;
  const pctColor =
    pct == null
      ? "var(--muted)"
      : pct > 0
        ? "var(--long)"
        : pct < 0
          ? "var(--short)"
          : "var(--muted)";

  return (
    <div className="grid grid-cols-[auto_1fr_auto_auto] items-center gap-2 text-[11px]">
      <span
        className="inline-flex h-4 items-center rounded-full px-1.5 text-[10px] font-medium"
        style={{
          color: `var(${meta.colorVar})`,
          background: `color-mix(in srgb, var(${meta.colorVar}) 14%, transparent)`,
        }}
      >
        {meta.label}
      </span>
      <span className="tabular text-[var(--text)]">{price != null ? fmtPrice(price) : "市价"}</span>
      <span className="tabular text-right text-[var(--muted)]">{fmtNum(order.sz, 0)} 张</span>
      <span className="tabular text-right font-medium" style={{ color: pctColor }}>
        {pct == null ? "—" : `${pct > 0 ? "+" : ""}${(pct * 100).toFixed(2)}%`}
      </span>
    </div>
  );
}
