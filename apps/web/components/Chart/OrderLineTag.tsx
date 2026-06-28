"use client";

import { fmtPrice } from "@/lib/format";
import type { OrderLineHover } from "./useOrderPriceLines";

// 悬停某条委托虚线时贴右价轴的浮层：委托类型 + 委托价 + 相对最新价的涨跌幅（着色）。
export default function OrderLineTag({ hover }: { hover: OrderLineHover }) {
  const { line, y } = hover;
  const pct = line.changePct;
  const pctColor =
    pct == null
      ? "var(--muted)"
      : pct > 0
        ? "var(--long)"
        : pct < 0
          ? "var(--short)"
          : "var(--muted)";

  return (
    <div
      className="pointer-events-none absolute right-1 z-30 flex items-center gap-1.5 rounded-[6px] border border-[var(--border)] bg-[var(--surface)] px-2 py-0.5 text-[11px] shadow-soft"
      style={{ top: y, transform: "translateY(-50%)" }}
    >
      <span className="font-semibold" style={{ color: `var(${line.colorVar})` }}>
        {line.label}
      </span>
      <span className="tabular text-[var(--text)]">{fmtPrice(line.price)}</span>
      {pct != null && (
        <span className="tabular font-medium" style={{ color: pctColor }}>
          {pct > 0 ? "+" : ""}
          {(pct * 100).toFixed(2)}%
        </span>
      )}
    </div>
  );
}
