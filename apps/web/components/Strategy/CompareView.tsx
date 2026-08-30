"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "@/lib/api";
import {
  colorForIndex,
  filterCompareStrategies,
  invertSelection,
  MAX_COMPARE_SELECTIONS,
  MIN_COMPARE_SELECTIONS,
  selectAllEligible,
  sortCompareItems,
  type CompareFilter,
  type SortDir,
  type SortKey,
} from "@/lib/compare";
import { fmtNum, fmtPct } from "@/lib/format";
import { useAppStore } from "@/lib/store";
import type { CompareResult, Strategy } from "@/lib/types";
import CompareSelectionBar from "./CompareSelectionBar";
import CompareStrategyRow from "./CompareStrategyRow";
import StrategyDetailModal from "./StrategyDetailModal";

const COLUMNS: { key: SortKey; label: string }[] = [
  { key: "totalReturn", label: "收益率" },
  { key: "sharpe", label: "夏普" },
  { key: "winRate", label: "胜率" },
  { key: "maxDrawdown", label: "最大回撤" },
  { key: "tradeCount", label: "交易数" },
];

export default function CompareView() {
  const qc = useQueryClient();
  const { instId, bar, backtestStartTs, backtestEndTs, setRunId, setMode, setStrategyId, setChartView } =
    useAppStore();
  const { data: strategies } = useQuery({ queryKey: ["strategies"], queryFn: api.strategies });

  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [filter, setFilter] = useState<CompareFilter>("all");
  const [detail, setDetail] = useState<Strategy | null>(null);
  const [sortKey, setSortKey] = useState<SortKey>("totalReturn");
  const [sortDir, setSortDir] = useState<SortDir>("desc");

  // 对比结果写入共享缓存（page.tsx 据此叠加 overlays），与本组件配色一致。
  // 响应式订阅（enabled:false + initialData:null + queryFn:()=>null）：与 app/page.tsx 同模式，
  // 让「运行对比」/「清空结果」均即时反映到榜单与 hasResult（否则 setQueryData(null) 不触发重渲染）。
  const compareKey = ["compare", instId, bar, backtestStartTs, backtestEndTs];
  const { data: result } = useQuery<CompareResult | null>({
    queryKey: compareKey,
    queryFn: () => null,
    enabled: false,
    initialData: null,
  });
  const compare = useMutation({
    mutationFn: () =>
      api.compare({
        strategyIds: Array.from(selected),
        instId,
        bar,
        limit: 1000,
        startTs: backtestStartTs,
        endTs: backtestEndTs,
      }),
    onSuccess: (data) => qc.setQueryData(compareKey, data),
  });

  const atSelectionLimit = selected.size >= MAX_COMPARE_SELECTIONS;

  const toggle = (id: number) =>
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) {
        next.delete(id);
      } else if (next.size < MAX_COMPARE_SELECTIONS) {
        next.add(id);
      }
      return next;
    });

  const onSort = (key: SortKey) => {
    if (key === sortKey) setSortDir((d) => (d === "desc" ? "asc" : "desc"));
    else {
      setSortKey(key);
      setSortDir("desc");
    }
  };

  // 按结果原始顺序固定配色（与 CandleChart overlays 的 colorForIndex 一致）。
  const colorById = new Map<number, string>(
    (result?.items ?? []).map((it, i) => [it.strategyId, colorForIndex(i)]),
  );
  const rows = result ? sortCompareItems(result.items, sortKey, sortDir) : [];

  const openDetail = (strategyId: number, runId: number) => {
    setStrategyId(strategyId);
    setRunId(runId);
    setMode("backtest");
    setChartView("run-range");
  };

  if (!strategies) {
    return <div className="py-8 text-center text-xs text-[var(--muted)]">加载策略…</div>;
  }

  const visible = filterCompareStrategies(strategies, filter);

  return (
    <div className="flex flex-col gap-3">
      <div>
        <h3 className="text-sm font-semibold text-[var(--text)]">多策略对比</h3>
        <p className="text-[11px] text-[var(--muted)]">
          选择 2–12 个策略（可混选「组合」），对同一标的 / 周期一键回测并横向对比期望收益（真实 OKX 数据）。
        </p>
      </div>

      <CompareSelectionBar
        filter={filter}
        onFilter={setFilter}
        selectedCount={selected.size}
        maxSelections={MAX_COMPARE_SELECTIONS}
        minSelections={MIN_COMPARE_SELECTIONS}
        hasResult={!!result}
        running={compare.isPending}
        onSelectAll={() => setSelected(new Set(selectAllEligible(selected, visible)))}
        onInvert={() => setSelected(new Set(invertSelection(selected, visible)))}
        onClear={() => setSelected(new Set())}
        onRun={() => compare.mutate()}
        onClearResult={() => qc.setQueryData(compareKey, null)}
      />

      {selected.size === 0 && (
        <p className="rounded-[8px] border border-dashed border-[var(--border)] bg-[var(--surface)] px-3 py-2 text-[11px] text-[var(--muted)]">
          请从下方勾选 2–12 个策略后点「运行对比」。
        </p>
      )}

      <div className="flex flex-col gap-1.5">
        {visible.length === 0 ? (
          <p className="px-1 py-2 text-[11px] text-[var(--muted)]">该分类暂无策略。</p>
        ) : (
          visible.map((s) => (
            <CompareStrategyRow
              key={s.id}
              strategy={s}
              checked={selected.has(s.id)}
              disabled={s.unavailable || (!selected.has(s.id) && atSelectionLimit)}
              onToggle={toggle}
              onOpenDetail={setDetail}
            />
          ))
        )}
      </div>

      {compare.isError && (
        <div className="rounded-[8px] border border-[var(--short)] bg-[var(--surface)] px-3 py-2 text-[11px] text-[var(--short-ink)]">
          对比失败：{(compare.error as Error)?.message}
        </div>
      )}

      {rows.length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[560px] text-xs">
            <thead className="whitespace-nowrap text-[var(--muted)]">
              <tr className="text-left">
                <th className="py-1.5 pr-2 font-medium">策略</th>
                {COLUMNS.map((c) => (
                  <th
                    key={c.key}
                    onClick={() => onSort(c.key)}
                    className="cursor-pointer py-1.5 pr-2 text-right font-medium hover:text-[var(--text)]"
                  >
                    {c.label}
                    {sortKey === c.key ? (sortDir === "desc" ? " ↓" : " ↑") : ""}
                  </th>
                ))}
                <th className="py-1.5 font-medium" />
              </tr>
            </thead>
            <tbody>
              {rows.map((it) => (
                <tr key={it.runId} className="whitespace-nowrap border-t border-[var(--border)]">
                  <td className="py-1.5 pr-2">
                    <span className="flex items-center gap-1.5">
                      <span
                        className="inline-block h-2.5 w-2.5 shrink-0 rounded-[3px]"
                        style={{ background: colorById.get(it.strategyId) }}
                      />
                      <span className="truncate text-[var(--text)]">{it.name}</span>
                    </span>
                  </td>
                  <td
                    className="py-1.5 pr-2 text-right tabular font-medium"
                    style={{
                      color:
                        it.metrics.totalReturn > 0
                          ? "var(--long)"
                          : it.metrics.totalReturn < 0
                            ? "var(--short)"
                            : "var(--muted)",
                    }}
                  >
                    {fmtPct(it.metrics.totalReturn)}
                  </td>
                  <td className="py-1.5 pr-2 text-right tabular">{fmtNum(it.metrics.sharpe)}</td>
                  <td className="py-1.5 pr-2 text-right tabular">{fmtPct(it.metrics.winRate)}</td>
                  <td className="py-1.5 pr-2 text-right tabular text-[var(--short-ink)]">
                    {fmtPct(it.metrics.maxDrawdown)}
                  </td>
                  <td className="py-1.5 pr-2 text-right tabular">{it.metrics.tradeCount}</td>
                  <td className="py-1.5 text-right">
                    <button
                      onClick={() => openDetail(it.strategyId, it.runId)}
                      className="rounded-[6px] px-2 py-0.5 text-[11px] text-[var(--accent-ink)] hover:bg-[var(--accent-soft)]"
                    >
                      打开
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="mt-2 text-[11px] text-[var(--muted)]">
            左图按 净值/初始-1 归一化叠加各策略净值曲线，色块与表格对应。
          </p>
        </div>
      )}

      {detail && (
        <StrategyDetailModal
          strategy={detail}
          strategies={strategies}
          compareItem={result?.items.find((it) => it.strategyId === detail.id)}
          onClose={() => setDetail(null)}
        />
      )}
    </div>
  );
}
