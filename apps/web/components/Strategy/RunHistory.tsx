"use client";

import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "@/lib/api";
import { fmtPct, fmtTime } from "@/lib/format";
import { useAppStore } from "@/lib/store";
import type { Run } from "@/lib/types";
import { Badge } from "@/components/ui";

// mode 过滤：对比会一次落多行 backtest run，按来源过滤避免淹没历史（C8）
const MODE_FILTERS: { key: string; label: string }[] = [
  { key: "", label: "全部" },
  { key: "backtest", label: "回测" },
  { key: "paper", label: "模拟" },
];

const MODE_LABEL: Record<string, string> = { backtest: "回测", paper: "模拟" };

export default function RunHistory() {
  const { setRunId, setMode, setStrategyId } = useAppStore();
  const [modeFilter, setModeFilter] = useState("");

  const { data: runs, isLoading } = useQuery({
    queryKey: ["runs-history", modeFilter],
    queryFn: () => api.runsList({ mode: modeFilter || undefined, limit: 50 }),
    refetchInterval: 5000,
  });

  const open = (r: Run) => {
    setStrategyId(r.strategyId);
    setRunId(r.id);
    setMode("backtest");
  };

  return (
    <div className="flex flex-col gap-2">
      <div className="flex gap-0.5 rounded-[8px] bg-[var(--surface-2)] p-0.5">
        {MODE_FILTERS.map((f) => (
          <button
            key={f.key}
            onClick={() => setModeFilter(f.key)}
            className={
              "flex-1 rounded-[6px] px-2 py-1 text-[11px] font-medium transition-all " +
              (modeFilter === f.key
                ? "bg-[var(--surface)] text-[var(--text)] shadow-soft"
                : "text-[var(--muted)] hover:text-[var(--text)]")
            }
          >
            {f.label}
          </button>
        ))}
      </div>

      {isLoading ? (
        <div className="py-8 text-center text-xs text-[var(--muted)]">加载运行历史…</div>
      ) : !runs || runs.length === 0 ? (
        <div className="py-8 text-center text-xs text-[var(--muted)]">
          暂无运行历史，运行回测 / 对比 / 模拟后显示。
        </div>
      ) : (
        <div className="max-h-[320px] overflow-auto">
          <table className="w-full text-xs">
            <thead className="sticky top-0 bg-[var(--surface)] text-[var(--muted)]">
              <tr className="text-left">
                <th className="py-1.5 pr-2 font-medium">标的</th>
                <th className="py-1.5 pr-2 font-medium">来源</th>
                <th className="py-1.5 pr-2 text-right font-medium">收益</th>
                <th className="py-1.5 text-right font-medium">时间</th>
              </tr>
            </thead>
            <tbody>
              {runs.map((r) => (
                <tr
                  key={r.id}
                  onClick={() => open(r)}
                  className="cursor-pointer border-t border-[var(--border)] hover:bg-[var(--surface-2)]"
                >
                  <td className="py-1.5 pr-2">
                    <div className="text-[var(--text)]">{r.instId}</div>
                    <div className="text-[10px] text-[var(--muted)]">{r.bar}</div>
                  </td>
                  <td className="py-1.5 pr-2">
                    <Badge tone={r.mode === "paper" ? "accent" : "neutral"}>
                      {MODE_LABEL[r.mode] ?? r.mode}
                    </Badge>
                  </td>
                  <td
                    className="py-1.5 pr-2 text-right tabular font-medium"
                    style={{
                      color:
                        r.metrics.totalReturn > 0
                          ? "var(--long)"
                          : r.metrics.totalReturn < 0
                            ? "var(--short)"
                            : "var(--muted)",
                    }}
                  >
                    {fmtPct(r.metrics.totalReturn)}
                  </td>
                  <td className="py-1.5 text-right tabular text-[var(--muted)]">
                    {r.startTs ? fmtTime(r.startTs) : "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
