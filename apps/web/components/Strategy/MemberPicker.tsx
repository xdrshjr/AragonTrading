"use client";

import { useState } from "react";
import { summarizeComboMembers } from "@/lib/combo";
import { regimeAffinityLabels } from "@/lib/regimeAffinity";
import type { Strategy } from "@/lib/types";
import { Badge, Button, Tabs } from "@/components/ui";

/**
 * 多页签成员选择器：「普通策略」勾选原子成员；「组合策略」可查看 / 把已存组合的原子成员展开导入。
 *
 * 组合不嵌套组合（轮动引擎成员只接原子 {templateKey, params}）：选「组合策略」落地为把其成员
 * 展开进当前草稿，由父组件经 importComboMembers 处理并回传 importNotice 文案。
 */
export default function MemberPicker({
  candidates,
  userCombos,
  memberIds,
  maxMembers,
  onToggle,
  onImportCombo,
  onViewCombo,
  importNotice,
}: {
  candidates: Strategy[];
  userCombos: Strategy[];
  memberIds: number[];
  maxMembers: number;
  onToggle: (id: number) => void;
  onImportCombo: (combo: Strategy) => void;
  onViewCombo: (combo: Strategy) => void;
  importNotice?: string | null;
}) {
  const [tab, setTab] = useState<"atomic" | "combo">("atomic");
  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex items-center justify-between">
        <span className="text-[11px] font-medium text-[var(--text)]">选择成员策略</span>
        <span className="text-[11px] text-[var(--muted)]">
          已选 {memberIds.length}/{maxMembers}
        </span>
      </div>
      <Tabs
        variant="underline"
        active={tab}
        onChange={(key) => setTab(key as "atomic" | "combo")}
        tabs={[
          { key: "atomic", label: "普通策略" },
          { key: "combo", label: "组合策略" },
        ]}
      />
      {tab === "atomic" ? (
        <AtomicList
          candidates={candidates}
          memberIds={memberIds}
          capReached={memberIds.length >= maxMembers}
          onToggle={onToggle}
        />
      ) : (
        <ComboSourceList
          userCombos={userCombos}
          notice={importNotice}
          onImport={onImportCombo}
          onView={onViewCombo}
        />
      )}
    </div>
  );
}

function AtomicList({
  candidates,
  memberIds,
  capReached,
  onToggle,
}: {
  candidates: Strategy[];
  memberIds: number[];
  capReached: boolean;
  onToggle: (id: number) => void;
}) {
  if (candidates.length === 0) {
    return <EmptyHint text="暂无可用的普通策略" />;
  }
  return (
    <div className="flex flex-col gap-1 pt-1">
      {candidates.map((s) => {
        const checked = memberIds.includes(s.id);
        const affinity = regimeAffinityLabels(s.templateKey);
        return (
          <label
            key={s.id}
            className="flex items-center gap-2 rounded-[8px] border border-[var(--border)] bg-[var(--surface)] px-2.5 py-1.5 text-xs"
          >
            <input
              type="checkbox"
              checked={checked}
              disabled={!checked && capReached}
              onChange={() => onToggle(s.id)}
              className="h-3.5 w-3.5 accent-[var(--accent)]"
            />
            <span className="min-w-0 flex-1 truncate text-[var(--text)]">{s.name}</span>
            {affinity.length > 0 && (
              <span className="flex shrink-0 flex-wrap justify-end gap-1">
                {affinity.map((label) => (
                  <span
                    key={label}
                    className="rounded-[5px] bg-[var(--surface-2)] px-1.5 py-0.5 text-[10px] text-[var(--muted)]"
                  >
                    {label}
                  </span>
                ))}
              </span>
            )}
          </label>
        );
      })}
    </div>
  );
}

function ComboSourceList({
  userCombos,
  notice,
  onImport,
  onView,
}: {
  userCombos: Strategy[];
  notice?: string | null;
  onImport: (combo: Strategy) => void;
  onView: (combo: Strategy) => void;
}) {
  if (userCombos.length === 0) {
    return <EmptyHint text="还没有可复用的组合策略，先在下方保存一个组合" />;
  }
  return (
    <div className="flex flex-col gap-1 pt-1">
      {notice && (
        <p className="rounded-[8px] bg-[var(--accent-soft)] px-2.5 py-1.5 text-[11px] text-[var(--accent)]">
          {notice}
        </p>
      )}
      {userCombos.map((combo) => (
        <div
          key={combo.id}
          className="flex items-center gap-2 rounded-[8px] border border-[var(--border)] bg-[var(--surface)] px-2.5 py-1.5 text-xs"
        >
          <div className="flex min-w-0 flex-1 flex-col">
            <div className="flex items-center gap-1.5">
              <span className="truncate text-[var(--text)]">{combo.name}</span>
              <Badge tone="accent">组合</Badge>
            </div>
            <span className="truncate text-[11px] text-[var(--muted)]">
              {summarizeComboMembers(combo)}
            </span>
          </div>
          <div className="flex shrink-0 items-center gap-1">
            <Button variant="ghost" size="sm" onClick={() => onView(combo)}>
              查看
            </Button>
            <Button variant="default" size="sm" onClick={() => onImport(combo)}>
              导入成员
            </Button>
          </div>
        </div>
      ))}
    </div>
  );
}

function EmptyHint({ text }: { text: string }) {
  return (
    <p className="rounded-[8px] border border-dashed border-[var(--border)] px-3 py-3 text-center text-[11px] text-[var(--muted)]">
      {text}
    </p>
  );
}
