"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { Badge, Button, Input, Select, Skeleton, Switch } from "@/components/ui";
import { CATEGORY_LABELS, categoryLabel, formatIc, icTone, primaryRankIc } from "@/lib/factor";
import { factorApi } from "@/lib/factorApi";
import type { Factor, FactorDirection, FactorSource } from "@/lib/factorTypes";
import { useAppStore } from "@/lib/store";
import { useWorkbench } from "./FactorLab";

const LIST_KEY = ["factor", "list"];
const PAGE_SIZE = 50;
const SOURCE_LABELS: Record<string, string> = { builtin: "内置", mined: "挖掘", custom: "自定义" };
type SortKey = "" | "rankic" | "name" | "created";
type EnabledFilter = "all" | "enabled" | "disabled";

/** 我的因子：搜索 / 来源·类别·激活筛选 / 排序 / 分页浏览全部（含大量挖掘结果），编辑方向·激活取消·删除（L3）。 */
export default function FactorTable() {
  const qc = useQueryClient();
  const setSection = useAppStore((s) => s.setFactorLabSection);
  const wb = useWorkbench();

  // 搜索/筛选/排序态走组件本地瞬态 useState（不入 store，避免 persist 迁移；刷新回默认可接受，L3）。
  const [searchInput, setSearchInput] = useState("");
  const [q, setQ] = useState("");
  const [source, setSource] = useState<FactorSource | "all">("all");
  const [category, setCategory] = useState("");
  const [enabledFilter, setEnabledFilter] = useState<EnabledFilter>("all");
  const [sort, setSort] = useState<SortKey>("");
  const [page, setPage] = useState(1);

  // 搜索防抖：输入停 300ms 后才下发查询（减少抖动与请求）。
  useEffect(() => {
    const timer = setTimeout(() => setQ(searchInput.trim()), 300);
    return () => clearTimeout(timer);
  }, [searchInput]);

  // 任一筛选/排序变化即回到第一页（避免停留在越界页码）。
  useEffect(() => {
    setPage(1);
  }, [q, source, category, enabledFilter, sort]);

  const filter = {
    q: q || undefined,
    source: source === "all" ? undefined : source,
    category: category || undefined,
    enabled: enabledFilter === "all" ? undefined : enabledFilter === "enabled",
    sort: sort || undefined,
  };
  const { data, isLoading } = useQuery({
    queryKey: [...LIST_KEY, filter],
    queryFn: () => factorApi.list(filter),
  });
  // 仅日内徽标需要库元信息（Factor 行不带 intradayOnly，按 factorKey 查库）（L5）。
  const lib = useQuery({ queryKey: ["factor", "library"], queryFn: factorApi.library });
  const intradayKeys = useMemo(
    () => new Set((lib.data ?? []).filter((f) => f.intradayOnly).map((f) => f.key)),
    [lib.data],
  );

  const update = useMutation({
    mutationFn: ({ id, patch }: { id: number; patch: { enabled?: boolean; direction?: FactorDirection } }) =>
      factorApi.update(id, patch),
    onSettled: () => void qc.invalidateQueries({ queryKey: LIST_KEY }),
  });
  const remove = useMutation({
    mutationFn: (id: number) => factorApi.remove(id),
    onSettled: () => void qc.invalidateQueries({ queryKey: LIST_KEY }),
  });

  const factors = data ?? [];
  const visible = factors.slice(0, page * PAGE_SIZE);

  return (
    <div className="flex flex-col gap-3 pb-6">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-sm font-semibold text-[var(--text)]">我的因子（{factors.length}）</h2>
        <span className="text-[11px] text-[var(--muted)]">激活的因子可被挖掘 / 评估 / 组合复用</span>
      </div>

      <div className="card flex flex-wrap items-end gap-2 p-3">
        <label className="flex flex-1 flex-col gap-1" style={{ minWidth: 160 }}>
          <span className="text-[11px] text-[var(--muted)]">搜索</span>
          <Input
            value={searchInput}
            onChange={(e) => setSearchInput(e.target.value)}
            placeholder="按名称 / key 搜索…"
            className="py-1.5 text-xs"
          />
        </label>
        <FilterSelect
          label="来源"
          value={source}
          onChange={(v) => setSource(v as FactorSource | "all")}
          options={[
            ["all", "全部来源"],
            ["builtin", "内置"],
            ["mined", "挖掘"],
            ["custom", "自定义"],
          ]}
        />
        <FilterSelect
          label="类别"
          value={category}
          onChange={setCategory}
          options={[["", "全部类别"], ...Object.entries(CATEGORY_LABELS)]}
        />
        <FilterSelect
          label="激活"
          value={enabledFilter}
          onChange={(v) => setEnabledFilter(v as EnabledFilter)}
          options={[
            ["all", "全部"],
            ["enabled", "仅激活"],
            ["disabled", "仅停用"],
          ]}
        />
        <FilterSelect
          label="排序"
          value={sort}
          onChange={(v) => setSort(v as SortKey)}
          options={[
            ["", "默认"],
            ["rankic", "RankIC"],
            ["name", "名称"],
            ["created", "创建时间"],
          ]}
        />
      </div>

      {isLoading ? (
        <Skeleton className="h-64" />
      ) : (
        <div className="overflow-x-auto rounded-[12px] border border-[var(--border)]">
          <table className="w-full min-w-[680px] text-left text-xs">
            <thead className="bg-[var(--surface-2)] text-[var(--muted)]">
              <tr>
                <th className="px-3 py-2 font-medium">名称</th>
                <th className="px-3 py-2 font-medium">类别</th>
                <th className="px-3 py-2 font-medium">方向</th>
                <th className="px-3 py-2 font-medium">来源</th>
                <th className="px-3 py-2 font-medium tabular">RankIC</th>
                <th className="px-3 py-2 font-medium">激活</th>
                <th className="px-3 py-2 font-medium">操作</th>
              </tr>
            </thead>
            <tbody>
              {visible.map((f) => (
                <Row
                  key={f.id}
                  factor={f}
                  intradayOnly={intradayKeys.has(f.factorKey)}
                  onDirection={(direction) => update.mutate({ id: f.id, patch: { direction } })}
                  onToggle={(enabled) => update.mutate({ id: f.id, patch: { enabled } })}
                  onDelete={() => remove.mutate(f.id)}
                  onEvaluate={() => {
                    wb.setEvalTarget({ factorKey: f.factorKey, params: f.params, direction: f.direction });
                    setSection("evaluate");
                  }}
                />
              ))}
              {factors.length === 0 && (
                <tr>
                  <td colSpan={7} className="px-3 py-6 text-center text-[var(--muted)]">
                    {q || source !== "all" || category || enabledFilter !== "all"
                      ? "无匹配因子，调整筛选条件。"
                      : "暂无因子，去「因子目录」加入，或在「因子挖掘」中保存挖掘结果。"}
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      )}

      {factors.length > 0 && (
        <div className="flex items-center justify-center gap-3 text-[11px] text-[var(--muted)]">
          <span>
            显示 {visible.length} / 共 {factors.length} 条
          </span>
          {visible.length < factors.length && (
            <Button variant="default" size="sm" onClick={() => setPage((p) => p + 1)}>
              加载更多
            </Button>
          )}
        </div>
      )}
    </div>
  );
}

function FilterSelect({
  label,
  value,
  onChange,
  options,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  options: [string, string][];
}) {
  return (
    <label className="flex flex-col gap-1">
      <span className="text-[11px] text-[var(--muted)]">{label}</span>
      <Select value={value} onChange={(e) => onChange(e.target.value)} className="py-1 text-xs">
        {options.map(([val, text]) => (
          <option key={val} value={val}>
            {text}
          </option>
        ))}
      </Select>
    </label>
  );
}

function Row({
  factor,
  intradayOnly,
  onDirection,
  onToggle,
  onDelete,
  onEvaluate,
}: {
  factor: Factor;
  intradayOnly: boolean;
  onDirection: (direction: FactorDirection) => void;
  onToggle: (enabled: boolean) => void;
  onDelete: () => void;
  onEvaluate: () => void;
}) {
  const rankIc = factor.eval ? primaryRankIc(factor.eval.horizons) : null;
  const tone = icTone(rankIc);
  return (
    <tr className="border-t border-[var(--border)]">
      <td className="px-3 py-2">
        <div className="flex flex-col">
          <div className="flex items-center gap-1.5">
            <span className="font-medium text-[var(--text)]">{factor.name}</span>
            {intradayOnly && <Badge tone="neutral">仅日内</Badge>}
          </div>
          <code className="text-[10px] text-[var(--muted)]">{factor.factorKey}</code>
        </div>
      </td>
      <td className="px-3 py-2 text-[var(--muted)]">{categoryLabel(factor.category)}</td>
      <td className="px-3 py-2">
        <Select
          value={factor.direction}
          onChange={(e) => onDirection(e.target.value as FactorDirection)}
          className="px-1.5 py-0.5 text-[11px]"
        >
          <option value="auto">自动</option>
          <option value="long">看多</option>
          <option value="short">看空</option>
        </Select>
      </td>
      <td className="px-3 py-2">
        <Badge tone="neutral">{SOURCE_LABELS[factor.source] ?? factor.source}</Badge>
      </td>
      <td
        className="px-3 py-2 tabular"
        title={factor.eval == null ? "参数/方向已变更，请重新评估" : undefined}
      >
        <span
          className={
            tone === "long" ? "text-[var(--long)]" : tone === "short" ? "text-[var(--short)]" : "text-[var(--muted)]"
          }
        >
          {formatIc(rankIc)}
        </span>
      </td>
      <td className="px-3 py-2">
        <Switch checked={factor.enabled} onChange={onToggle} label={`激活 ${factor.name}`} />
      </td>
      <td className="px-3 py-2">
        <div className="flex items-center gap-1">
          <Button variant="ghost" size="sm" onClick={onEvaluate}>
            评估
          </Button>
          <Button
            variant="danger"
            size="sm"
            disabled={factor.builtin}
            title={factor.builtin ? "内置因子不可删除" : "删除"}
            onClick={onDelete}
          >
            删除
          </Button>
        </div>
      </td>
    </tr>
  );
}
