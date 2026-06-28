"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { Badge, Button } from "@/components/ui";
import { categoryLabel, directionLabel, formatIc, formatPct, icTone } from "@/lib/factor";
import { factorApi } from "@/lib/factorApi";
import type { FactorLibraryItem, RankedFactor } from "@/lib/factorTypes";
import { useAppStore } from "@/lib/store";
import { useWorkbench } from "./FactorLab";

/** 因子挖掘：选类别 + topN → 扫描按 RankIC IR 排名 → 保存为因子 / 加入组合。 */
export default function FactorMiningPanel() {
  const qc = useQueryClient();
  const wb = useWorkbench();
  const setSection = useAppStore((s) => s.setFactorLabSection);
  const lib = useQuery({ queryKey: ["factor", "library"], queryFn: factorApi.library });
  const [categories, setCategories] = useState<string[]>([]);
  const [topN, setTopN] = useState(15);
  const [notice, setNotice] = useState<string | null>(null);

  const allCategories = useMemo(() => uniqueCategories(lib.data ?? []), [lib.data]);
  const range = { instId: wb.instId, bar: wb.bar, startTs: wb.startTs, endTs: wb.endTs };

  const mine = useMutation({
    mutationFn: () =>
      factorApi.mine({ ...range, categories: categories.length ? categories : undefined, topN }),
  });
  const saveAsFactor = useMutation({
    mutationFn: (item: RankedFactor) =>
      factorApi.create({
        factorKey: item.factorKey,
        name: factorName(lib.data ?? [], item),
        params: item.params,
        direction: item.direction,
        source: "mined",
      }),
    onSuccess: () => {
      setNotice("已保存为因子（来源：挖掘）");
      void qc.invalidateQueries({ queryKey: ["factor", "list"] });
    },
  });

  const toggleCategory = (cat: string) =>
    setCategories((prev) => (prev.includes(cat) ? prev.filter((c) => c !== cat) : [...prev, cat]));

  return (
    <div className="flex flex-col gap-4 pb-6">
      <div className="card flex flex-col gap-3 p-3">
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="mr-1 text-[11px] text-[var(--muted)]">类别：</span>
          {allCategories.map((cat) => (
            <button
              key={cat}
              type="button"
              onClick={() => toggleCategory(cat)}
              className={
                "rounded-full px-2.5 py-0.5 text-[11px] font-medium transition-colors " +
                (categories.includes(cat)
                  ? "bg-[var(--accent-soft)] text-[var(--accent)]"
                  : "bg-[var(--surface-2)] text-[var(--muted)] hover:text-[var(--text)]")
              }
            >
              {categoryLabel(cat)}
            </button>
          ))}
          {categories.length > 0 && (
            <button type="button" onClick={() => setCategories([])} className="text-[11px] text-[var(--muted)] underline">
              清除
            </button>
          )}
        </div>
        <div className="flex items-end gap-3">
          <label className="flex flex-col gap-1">
            <span className="text-[11px] text-[var(--muted)]">返回前 N</span>
            <input
              type="number"
              min={1}
              max={50}
              value={topN}
              onChange={(e) => setTopN(Math.max(1, Math.min(50, Number(e.target.value) || 1)))}
              className="w-20 rounded-[8px] border border-[var(--border)] bg-[var(--surface)] px-2 py-1 text-xs tabular"
            />
          </label>
          <Button variant="primary" disabled={mine.isPending} onClick={() => mine.mutate()}>
            {mine.isPending ? "扫描中..." : "开始挖掘"}
          </Button>
        </div>
      </div>

      {notice && <p className="text-[11px] font-medium text-[var(--accent)]">{notice}</p>}
      {mine.isError && (
        <p className="text-[11px] text-[var(--short)]">挖掘失败：{(mine.error as Error)?.message}</p>
      )}
      {mine.data?.truncated && (
        <p className="text-[11px] text-[var(--muted)]">已按 topN 截断返回行数（工作量未超限）。</p>
      )}

      {mine.data && (
        <div className="overflow-x-auto rounded-[12px] border border-[var(--border)]">
          <table className="w-full min-w-[680px] text-left text-xs tabular">
            <thead className="bg-[var(--surface-2)] text-[var(--muted)]">
              <tr>
                {["#", "因子", "方向", "RankIC", "IR", "单调性", "覆盖", "操作"].map((c) => (
                  <th key={c} className="px-3 py-2 font-medium">{c}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {mine.data.items.map((item, i) => (
                <tr key={`${item.factorKey}-${i}`} className="border-t border-[var(--border)]">
                  <td className="px-3 py-1.5 text-[var(--muted)]">{i + 1}</td>
                  <td className="px-3 py-1.5">
                    <div className="flex flex-col">
                      <span className="font-medium text-[var(--text)]">{factorName(lib.data ?? [], item)}</span>
                      <code className="text-[10px] text-[var(--muted)]">
                        {item.factorKey} {JSON.stringify(item.params)}
                      </code>
                    </div>
                  </td>
                  <td className="px-3 py-1.5">
                    <Badge tone={item.direction === "long" ? "long" : "short"}>{directionLabel(item.direction)}</Badge>
                  </td>
                  <td className={cellTone(item.rankIc)}>{formatIc(item.rankIc)}</td>
                  <td className={cellTone(item.ir)}>{formatIc(item.ir)}</td>
                  <td className={cellTone(item.monotonicity)}>{formatIc(item.monotonicity)}</td>
                  <td className="px-3 py-1.5 text-[var(--muted)]">{formatPct(item.coverage)}</td>
                  <td className="px-3 py-1.5">
                    <div className="flex items-center gap-1">
                      <Button variant="ghost" size="sm" disabled={saveAsFactor.isPending} onClick={() => saveAsFactor.mutate(item)}>
                        保存
                      </Button>
                      <Button
                        variant="default"
                        size="sm"
                        onClick={() => {
                          wb.addPendingMember({
                            factorKey: item.factorKey,
                            params: item.params,
                            weight: 0,
                            direction: item.direction,
                          });
                          setSection("builder");
                        }}
                      >
                        加入组合
                      </Button>
                    </div>
                  </td>
                </tr>
              ))}
              {mine.data.items.length === 0 && (
                <tr>
                  <td colSpan={8} className="px-3 py-6 text-center text-[var(--muted)]">本区间无有效因子</td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function uniqueCategories(items: FactorLibraryItem[]): string[] {
  return Array.from(new Set(items.map((i) => i.category)));
}

function factorName(lib: FactorLibraryItem[], item: { factorKey: string }): string {
  return lib.find((f) => f.key === item.factorKey)?.cn ?? item.factorKey;
}

function cellTone(value: number | null): string {
  const tone = icTone(value);
  return `px-3 py-1.5 ${tone === "long" ? "text-[var(--long)]" : tone === "short" ? "text-[var(--short)]" : "text-[var(--muted)]"}`;
}
