"use client";

import { useQuery } from "@tanstack/react-query";
import clsx from "clsx";
import { api } from "@/lib/api";
import { colorForReturn, fmtPct, fmtPrice } from "@/lib/format";
import { buildInstOptions } from "@/lib/instruments";
import type { Ticker } from "@/lib/types";

// 左 20% 竖向标的列表（chart-workspace-layout 需求 3）：点击切换标的、当前行高亮 + 实时价 / 区间涨跌。
// 自跑 useQuery(["instruments"])（与工具栏同键去重，R8）。无 P2 /tickers 时优雅降级：仅当前标的有价。
interface InstrumentListProps {
  active: string; // 当前 instId
  onSelect: (instId: string) => void; // = store.setInst
  lastPrice?: number | null; // 当前标的实时价（仅高亮行展示）
  changePct?: number | null; // 当前标的区间涨跌幅（来自 candles 首末，真实）
  tickers?: Record<string, Ticker>; // P2：全标的快照；缺省时其余行不显价
}

export default function InstrumentList({
  active,
  onSelect,
  lastPrice,
  changePct,
  tickers,
}: InstrumentListProps) {
  const { data: instruments } = useQuery({
    queryKey: ["instruments"],
    queryFn: () => api.instruments("SWAP"),
    staleTime: 5 * 60_000,
  });
  const options = buildInstOptions(instruments);

  return (
    <ul className="flex flex-col py-1">
      {options.map((id) => {
        const isActive = id === active;
        const snap = tickers?.[id];
        const price = isActive ? (lastPrice ?? snap?.last ?? null) : (snap?.last ?? null);
        const pct = isActive ? (changePct ?? snap?.changePct ?? null) : (snap?.changePct ?? null);
        return (
          <li key={id}>
            <button
              type="button"
              onClick={() => onSelect(id)}
              aria-current={isActive}
              className={clsx(
                "flex w-full flex-col items-start gap-0.5 border-l-2 px-3 py-2 text-left transition-colors",
                isActive
                  ? "border-[var(--accent)] bg-[var(--surface-2)]"
                  : "border-transparent hover:bg-[var(--surface-2)]",
              )}
            >
              <span
                className={clsx(
                  "text-xs font-medium",
                  isActive ? "text-[var(--text)]" : "text-[var(--muted)]",
                )}
              >
                {id}
              </span>
              <span className="flex items-center gap-1.5 text-[11px] tabular">
                <span className="text-[var(--text)]">{fmtPrice(price)}</span>
                {pct != null && (
                  <span style={{ color: colorForReturn(pct) }}>{fmtPct(pct)}</span>
                )}
              </span>
            </button>
          </li>
        );
      })}
    </ul>
  );
}
