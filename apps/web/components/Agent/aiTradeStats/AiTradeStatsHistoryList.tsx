"use client";

import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Badge, Button, Skeleton } from "@/components/ui";
import { aiTradeHistoryApi } from "@/lib/aiTradeHistoryApi";
import type { AiTradeHistoryEntry } from "@/lib/aiTradeHistoryTypes";
import { colorForReturn, fmtPct, fmtTime, signed } from "@/lib/format";
import CalibrationCard from "./CalibrationCard";
import PresetComparisonCard from "./PresetComparisonCard";
import ProfitabilityCard from "./ProfitabilityCard";

const PAGE_SIZE = 20;

// AI 自动交易统计「历史」列表（ai-trade-stats-dashboard §3.10.2）：拉取已持久化的过往会话（跨刷新 / 重启
// 可见），每张卡显示起止时间 / 标的 / 策略 / 状态 + 已实现总收益 / 收益率 / 笔数 / 胜率；整卡可点进详情、底部分页。
export default function AiTradeStatsHistoryList({ onSelect }: { onSelect: (groupId: string) => void }) {
  const [offset, setOffset] = useState(0);
  const query = useQuery({
    queryKey: ["ai-trade-history", offset],
    queryFn: () => aiTradeHistoryApi.list(PAGE_SIZE, offset),
  });

  if (query.isLoading) {
    return (
      <div className="flex flex-col gap-2">
        {[0, 1, 2].map((i) => (
          <Skeleton key={i} className="h-[68px] w-full rounded-[12px]" />
        ))}
      </div>
    );
  }

  if (query.isError) {
    return (
      <p role="alert" className="rounded-md border border-[var(--short)] bg-[var(--surface-2)] px-3 py-6 text-center text-xs text-[var(--short)]">
        加载历史失败，请稍后重试。
      </p>
    );
  }

  const data = query.data;
  const items = data?.items ?? [];
  const total = data?.total ?? 0;
  const hasPrev = offset > 0;
  const hasNext = offset + items.length < total;
  return (
    <div className="flex flex-col gap-2">
      {/* realized-edge-calibration [P1-4]：三张跨会话卡必须挂在 `items.length === 0` 的空态早返回
          **之前**——`/ai-trade/history` 会排除**正在运行**的那一组，只跑过一组的用户在会话运行期间
          整页恒空，卡一张都看不到。空态文案因此下移为**列表区**的空态，而不是整页的空态。 */}
      <ProfitabilityCard />
      <CalibrationCard />
      {/* 历史纪律预设对照（tradability-and-open-funnel 轴 E）：挂在列表**顶部**——「哪一档更赚」
          是用户翻历史时的第一个问题，答案不该藏在几十张卡片下面。无历史 ⇒ 整卡不渲染。 */}
      <PresetComparisonCard />
      {items.length === 0 ? (
        <p className="px-3 py-10 text-center text-xs leading-relaxed text-[var(--muted)]">
          暂无历史 AI 自动交易记录。启动并停止一次 AI 自动交易后，本组的完整收益概况会持久保存在这里，可随时回溯。
        </p>
      ) : (
        items.map((item) => (
          <HistoryCard key={item.groupId} entry={item} onSelect={() => onSelect(item.groupId)} />
        ))
      )}
      {/* 零历史时不挂翻页条：空态文案下面跟一句「共 0 次 · 第 1 页」与两个禁用按钮，是把
          「这里什么都还没有」说成了「这一页什么都没有」。 */}
      {total > 0 && (
        <div className="flex items-center justify-between pt-1 text-[11px] text-[var(--muted)]">
          <span className="tabular">
            共 {total} 次 · 第 {Math.floor(offset / PAGE_SIZE) + 1} 页
          </span>
          <div className="flex items-center gap-2">
            <Button
              variant="ghost"
              size="sm"
              disabled={!hasPrev}
              onClick={() => setOffset((o) => Math.max(0, o - PAGE_SIZE))}
            >
              上一页
            </Button>
            <Button variant="ghost" size="sm" disabled={!hasNext} onClick={() => setOffset((o) => o + PAGE_SIZE)}>
              下一页
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}

function HistoryCard({ entry, onSelect }: { entry: AiTradeHistoryEntry; onSelect: () => void }) {
  const tone = colorForReturn(entry.realizedPnlUsd);
  return (
    <button
      type="button"
      onClick={onSelect}
      className="flex w-full items-center gap-3 rounded-[12px] border border-[var(--border)] bg-[var(--surface-2)] px-3.5 py-2.5 text-left transition-colors hover:border-[var(--accent)]"
    >
      <div className="flex min-w-0 flex-1 flex-col gap-1">
        <span className="flex flex-wrap items-center gap-1.5">
          {statusBadge(entry.status)}
          <InstIds instIds={entry.instIds} />
          {entry.strategyName && (
            <span className="truncate text-[11px] text-[var(--muted)]">· {entry.strategyName}</span>
          )}
        </span>
        <span className="tabular text-[10px] text-[var(--muted)]">
          {fmtTime(entry.startedTs)} → {fmtTime(entry.endedTs)} · {entry.bar} · {entry.tradeCount} 笔 · 胜率{" "}
          {fmtPct(entry.winRate)}
        </span>
      </div>
      <div className="flex shrink-0 flex-col items-end gap-0.5 tabular">
        <span className="text-sm font-semibold" style={{ color: tone }}>
          {signed(entry.realizedPnlUsd)}
        </span>
        <span className="text-[11px] font-medium" style={{ color: colorForReturn(entry.returnPct) }}>
          {fmtPct(entry.returnPct)}
        </span>
      </div>
    </button>
  );
}

function InstIds({ instIds }: { instIds: string[] }) {
  const shown = instIds.slice(0, 3);
  const extra = instIds.length - shown.length;
  return (
    <span className="truncate text-xs font-medium text-[var(--text)]">
      {shown.join("、")}
      {extra > 0 && <span className="ml-0.5 text-[10px] text-[var(--muted)]">+{extra}</span>}
    </span>
  );
}

function statusBadge(status: string) {
  if (status === "error") return <Badge tone="short">异常</Badge>;
  if (status === "running") return <Badge tone="accent">进行中</Badge>;
  return <Badge tone="neutral">已停止</Badge>;
}
