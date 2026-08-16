"use client";

import clsx from "clsx";
import { useEffect, useMemo, useState } from "react";
import { Button } from "@/components/ui";
import { memberIdentity } from "@/lib/factor";
import type { FactorMember } from "@/lib/factorTypes";

/**
 * 手动组合构建器（factor-combo-exploration-insights §2.3，受控组件）：用「组合行 × 因子筹码」网格让用户亲手
 * 勾选参与对比的具体组合，向父级输出 `number[][]`（池内下标）。候选数预览 / 超限阻断由 ExploreConfigPanel 既有
 * estimateComboCount/CandidatePreview 复用（manual 分支 planCombos 已实现），本组件不重复枚举 / 去重逻辑。
 *
 * 下标随池编辑的失真防护（P1-2）：内部**以 memberIdentity 为选择键**记录勾选，每次渲染对**当前** pool 重解为
 * 下标再输出；pool 因 PoolEditor 增删而使某个已选成员消失时**显式提示并剔除**（绝不静默保留指向别因子的失真下标）。
 */
export default function ManualComboBuilder({
  pool,
  labelOf,
  value,
  onChange,
}: {
  pool: FactorMember[];
  labelOf: (m: FactorMember) => string;
  value: number[][];
  onChange: (value: number[][]) => void;
}) {
  const poolIdentities = useMemo(() => pool.map(memberIdentity), [pool]);
  const idToIndex = useMemo(() => {
    const map = new Map<string, number>();
    poolIdentities.forEach((id, i) => {
      if (!map.has(id)) map.set(id, i);
    });
    return map;
  }, [poolIdentities]);

  // 内部真源：以 memberIdentity 为键的选择行（对池编辑鲁棒）。初值由 value 下标经当前池映射为身份。
  const [rows, setRows] = useState<string[][]>(() =>
    value.map((row) => row.map((i) => poolIdentities[i]).filter((id): id is string => Boolean(id))),
  );
  const [removedNote, setRemovedNote] = useState(0);

  // 池变更 → 对当前池重解身份行；消失的成员显式剔除并累计提示条数，同时把剔除后的下标回传父级。
  useEffect(() => {
    let removed = 0;
    const pruned = rows.map((row) => {
      const kept = row.filter((id) => idToIndex.has(id));
      removed += row.length - kept.length;
      return kept;
    });
    if (removed > 0) {
      setRemovedNote((n) => n + removed);
      setRows(pruned);
      onChange(toIndexCombos(pruned, idToIndex));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [idToIndex]);

  const emit = (nextRows: string[][]) => {
    setRows(nextRows);
    onChange(toIndexCombos(nextRows, idToIndex));
  };

  const toggleInRow = (rowIndex: number, id: string) =>
    emit(rows.map((row, i) => (i !== rowIndex ? row : row.includes(id) ? row.filter((x) => x !== id) : [...row, id])));
  const removeRow = (rowIndex: number) => emit(rows.filter((_, i) => i !== rowIndex));
  const addRow = () => emit([...rows, []]);

  // 重复组合签名（成员集合相同者）：UI 提前提示，避免用户困惑（后端/前端 planCombos 会去重）。
  const dupSignatures = useMemo(() => duplicateSignatures(rows), [rows]);

  return (
    <div className="flex flex-col gap-2" data-testid="manual-combo-builder">
      {removedNote > 0 && (
        <p role="status" className="rounded-[8px] border border-[var(--border)] bg-[var(--surface-2)] px-2 py-1 text-[11px] text-[var(--muted)]">
          已因因子池变更移除 {removedNote} 项失真的组合成员，请核对下方组合。
        </p>
      )}
      {rows.length === 0 && <p className="text-[11px] text-[var(--muted)]">尚无手动组合，点「+ 新增组合」并勾选因子。</p>}

      {rows.map((row, rowIndex) => (
        <ComboRow
          key={rowIndex}
          rowIndex={rowIndex}
          row={row}
          pool={pool}
          poolIdentities={poolIdentities}
          labelOf={labelOf}
          duplicate={row.length > 0 && dupSignatures.get(signatureOf(row)) === "dup"}
          onToggle={(id) => toggleInRow(rowIndex, id)}
          onRemove={() => removeRow(rowIndex)}
        />
      ))}

      <div>
        <Button variant="ghost" size="sm" onClick={addRow} disabled={pool.length < 2}>
          + 新增组合
        </Button>
      </div>
    </div>
  );
}

// 单个组合行：标题 + 删除 + 池因子筹码网格（勾选 = 加入本行）。
function ComboRow({
  rowIndex,
  row,
  pool,
  poolIdentities,
  labelOf,
  duplicate,
  onToggle,
  onRemove,
}: {
  rowIndex: number;
  row: string[];
  pool: FactorMember[];
  poolIdentities: string[];
  labelOf: (m: FactorMember) => string;
  duplicate: boolean;
  onToggle: (id: string) => void;
  onRemove: () => void;
}) {
  return (
    <div className="card flex flex-col gap-1.5 p-2" data-testid="combo-row">
      <div className="flex items-center justify-between gap-2">
        <span className="text-[11px] font-medium text-[var(--text)]">
          组合 {rowIndex + 1} · {row.length} 因子
        </span>
        <button type="button" className="text-[11px] text-[var(--muted)] hover:text-[var(--short-ink)]" onClick={onRemove}>
          删除本行
        </button>
      </div>
      <div className="flex flex-wrap gap-1.5">
        {pool.map((member, i) => {
          const id = poolIdentities[i];
          const selected = row.includes(id);
          return (
            <button
              key={id}
              type="button"
              aria-pressed={selected}
              onClick={() => onToggle(id)}
              className={clsx(
                "rounded-full px-2.5 py-0.5 text-[11px] font-medium transition-colors",
                selected
                  ? "bg-[var(--accent-soft)] text-[var(--accent-ink)]"
                  : "bg-[var(--surface-2)] text-[var(--muted)] hover:text-[var(--text)]",
              )}
            >
              {labelOf(member)}
            </button>
          );
        })}
      </div>
      {row.length === 0 && <span className="text-[10px] text-[var(--muted)]">空组合不会参与训练。</span>}
      {duplicate && <span className="text-[10px] text-[var(--muted)]">与另一组合成员相同，将被自动去重。</span>}
    </div>
  );
}

// 身份行 → 下标行（当前池）：越界身份已在上游剔除，空行不产出（下标数组仅含非空组合）。
function toIndexCombos(rows: string[][], idToIndex: Map<string, number>): number[][] {
  return rows
    .map((row) => row.map((id) => idToIndex.get(id)).filter((i): i is number => i != null))
    .filter((row) => row.length > 0);
}

function signatureOf(row: string[]): string {
  return [...row].sort().join("||");
}

// 标记每个成员集合签名是「唯一」还是「重复」（出现 ≥2 次即 dup）。
function duplicateSignatures(rows: string[][]): Map<string, "unique" | "dup"> {
  const counts = new Map<string, number>();
  for (const row of rows) {
    if (row.length === 0) continue;
    const sig = signatureOf(row);
    counts.set(sig, (counts.get(sig) ?? 0) + 1);
  }
  const result = new Map<string, "unique" | "dup">();
  for (const [sig, count] of counts) result.set(sig, count >= 2 ? "dup" : "unique");
  return result;
}
