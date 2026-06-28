"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Badge, Button, Select, Skeleton, Switch } from "@/components/ui";
import { categoryLabel, formatIc, icTone, primaryRankIc } from "@/lib/factor";
import { factorApi } from "@/lib/factorApi";
import type { Factor, FactorDirection } from "@/lib/factorTypes";
import { useAppStore } from "@/lib/store";
import { useWorkbench } from "./FactorLab";

const LIST_KEY = ["factor", "list"];
const SOURCE_LABELS: Record<string, string> = { builtin: "内置", mined: "挖掘", custom: "自定义" };

/** 我的因子：查看 / 编辑方向 / 激活取消(Switch) / 删除 + 缓存 IC 列；可跳转到评估工作区。 */
export default function FactorTable() {
  const qc = useQueryClient();
  const setSection = useAppStore((s) => s.setFactorLabSection);
  const wb = useWorkbench();
  const { data, isLoading } = useQuery({ queryKey: LIST_KEY, queryFn: () => factorApi.list() });

  const update = useMutation({
    mutationFn: ({ id, patch }: { id: number; patch: { enabled?: boolean; direction?: FactorDirection } }) =>
      factorApi.update(id, patch),
    onSettled: () => void qc.invalidateQueries({ queryKey: LIST_KEY }),
  });
  const remove = useMutation({
    mutationFn: (id: number) => factorApi.remove(id),
    onSettled: () => void qc.invalidateQueries({ queryKey: LIST_KEY }),
  });

  if (isLoading) return <Skeleton className="h-64" />;
  const factors = data ?? [];

  return (
    <div className="flex flex-col gap-3 pb-6">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold text-[var(--text)]">我的因子（{factors.length}）</h2>
        <span className="text-[11px] text-[var(--muted)]">激活的因子可被挖掘 / 评估 / 组合复用</span>
      </div>
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
            {factors.map((f) => (
              <Row
                key={f.id}
                factor={f}
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
                  暂无因子，去「因子目录」加入，或在「因子挖掘」中保存挖掘结果。
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function Row({
  factor,
  onDirection,
  onToggle,
  onDelete,
  onEvaluate,
}: {
  factor: Factor;
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
          <span className="font-medium text-[var(--text)]">{factor.name}</span>
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
      <td className="px-3 py-2 tabular">
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
