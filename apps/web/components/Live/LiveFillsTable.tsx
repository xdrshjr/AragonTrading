"use client";

import { useQuery } from "@tanstack/react-query";
import clsx from "clsx";
import { useState, type KeyboardEvent } from "react";
import TradeExplainDetail from "@/components/Strategy/TradeExplainDetail";
import { Badge } from "@/components/ui";
import { fmtNum, fmtPct, fmtPrice, fmtTime } from "@/lib/format";
import { explainKindLabel, explainKindTone } from "@/lib/tradeExplain";
import { tradingApi } from "@/lib/tradingApi";
import type { LiveFill } from "@/lib/tradingTypes";

// chevron + 时间 + 方向 + 成交价 + 数量 + 仓位占比 + 手续费（展开行 colSpan 用）。
const COL_COUNT = 7;

// 实盘成交表（§4.5 / live-fills-explainability §2.5）：8s 轮询真实 OKX 成交明细，自动交易成交可
// 逐行展开查看触发原因；鉴权失败显式错误态，绝不假数据（C1）。
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
      <div className="py-8 text-center text-xs text-[var(--short-ink)]">
        加载成交失败：{(error as Error)?.message || "请稍后重试"}
      </div>
    );
  }
  const fills = data?.fills ?? [];
  if (fills.length === 0) {
    return <div className="py-8 text-center text-xs text-[var(--muted)]">近三日无实盘成交记录。</div>;
  }
  return <FillsTableView fills={fills} />;
}

/** 行 key 与展开态身份：OKX 每行唯一 tradeId 优先，缺失回落 ordId-ts-index（RV2）。 */
function fillRowKey(fill: LiveFill, index: number): string {
  return fill.tradeId || `${fill.ordId}-${fill.ts}-${index}`;
}

/**
 * 可展开成交表（与 query 解耦，便于单测）。展开态由本组件持有为一个 `Set<string>`，按稳定行键
 * 索引——故 8s 轮询刷新数据时已展开行保持展开、不闪烁、不错挂他行（RV2）。
 */
export function FillsTableView({ fills }: { fills: LiveFill[] }) {
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set());
  const toggle = (key: string) =>
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });

  return (
    <table className="w-full min-w-[520px] text-xs">
      <caption className="pb-1.5 text-left text-[10px] text-[var(--muted)]">
        点击带角标的成交行展开查看自动交易触发原因
      </caption>
      <thead className="sticky top-0 whitespace-nowrap bg-[var(--surface)] text-[var(--muted)]">
        <tr className="text-left">
          <th className="w-5 py-1.5" aria-hidden />
          <th className="py-1.5 pr-2 font-medium">时间</th>
          <th className="py-1.5 pr-2 font-medium">方向</th>
          <th className="py-1.5 pr-2 text-right font-medium">成交价</th>
          <th className="py-1.5 pr-2 text-right font-medium">数量</th>
          <th className="py-1.5 pr-2 text-right font-medium">仓位占比</th>
          <th className="py-1.5 text-right font-medium">手续费</th>
        </tr>
      </thead>
      <tbody>
        {fills.map((fill, i) => {
          const key = fillRowKey(fill, i);
          return (
            <FillRow key={key} fill={fill} open={expanded.has(key)} onToggle={() => toggle(key)} />
          );
        })}
      </tbody>
    </table>
  );
}

function FillRow({
  fill,
  open,
  onToggle,
}: {
  fill: LiveFill;
  open: boolean;
  onToggle: () => void;
}) {
  const isBuy = fill.side === "buy";
  const explain = fill.explain;
  const hasPositionPct = fill.positionPct != null
    && Number.isFinite(fill.positionPct)
    && fill.positionPct >= 0
    && fill.positionPct <= 1;
  const onKeyDown = (e: KeyboardEvent<HTMLTableRowElement>) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      onToggle();
    }
  };
  // 有 explain → 整行可点、键盘可达、focus-visible 高亮（HCI）；无 explain → 朴素行不诱导展开。
  const interactiveProps = explain
    ? { role: "button", tabIndex: 0, "aria-expanded": open, onClick: onToggle, onKeyDown }
    : {};
  return (
    <>
      <tr
        {...interactiveProps}
        className={clsx(
          "whitespace-nowrap border-t border-[var(--border)]",
          explain &&
            "cursor-pointer hover:bg-[var(--surface-2)]/50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-[var(--accent)]",
        )}
      >
        <td className="py-1.5 align-middle">{explain && <Chevron open={open} />}</td>
        <td className="py-1.5 pr-2 tabular text-[var(--muted)]">{fmtTime(fill.ts)}</td>
        <td className="py-1.5 pr-2">
          <div className="flex flex-col items-start gap-0.5">
            <Badge tone={isBuy ? "long" : "short"}>{isBuy ? "买入" : "卖出"}</Badge>
            <KindTag explain={explain} automatic={hasPositionPct} />
          </div>
        </td>
        <td className="py-1.5 pr-2 text-right tabular">{fmtPrice(fill.fillPx)}</td>
        <td className="py-1.5 pr-2 text-right tabular">{fmtNum(fill.fillSz, 4)}</td>
        <td className="py-1.5 pr-2 text-right tabular">{fmtPct(fill.positionPct)}</td>
        <td className="py-1.5 text-right tabular text-[var(--muted)]">{fmtNum(fill.fee, 4)}</td>
      </tr>
      {explain && open && (
        <tr>
          <td colSpan={COL_COUNT} className="whitespace-normal px-2 pb-3 pt-0.5">
            <TradeExplainDetail explain={explain} />
          </td>
        </tr>
      )}
    </>
  );
}

/** 有解释时展示触发类型；仅有关联仓位比例时仍应标为自动成交，而不是误标手动。 */
function KindTag({ explain, automatic }: {
  explain?: LiveFill["explain"];
  automatic: boolean;
}) {
  if (!explain) {
    return (
      <span className="rounded bg-[var(--surface-2)] px-1 py-px text-[9px] text-[var(--muted)]">
        {automatic ? "自动" : "手动"}
      </span>
    );
  }
  const tone = explainKindTone(explain.kind);
  return (
    <span
      className={clsx(
        "rounded px-1 py-px text-[9px]",
        tone === "warn" && "bg-[var(--accent-soft)] text-[var(--accent-ink)]",
        tone !== "warn" && "bg-[var(--surface-2)] text-[var(--muted)]",
      )}
    >
      {explainKindLabel(explain.kind)}
    </span>
  );
}

function Chevron({ open }: { open: boolean }) {
  return (
    <svg
      width="12"
      height="12"
      viewBox="0 0 12 12"
      className={clsx("text-[var(--muted)] transition-transform", open && "rotate-90")}
      aria-hidden
    >
      <path
        d="M4 2.5L8 6l-4 3.5"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}
