"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Button, Input, Select } from "@/components/ui";
import { addMembersCapped, toggleMember, validateMembers } from "@/lib/factor";
import { factorApi } from "@/lib/factorApi";
import type { FactorMember, TrainMethod } from "@/lib/factorTypes";
import { ALL_METHODS } from "@/lib/weightBatch";
import { editorToCreateBody, methodDescription, methodLabel } from "@/lib/weightModel";
import { weightModelApi } from "@/lib/weightModelApi";
import { useWorkbench } from "./FactorLab";
import MemberPalette from "./MemberPalette";

const MAX_MEMBERS = 12;
// 方法下拉由 ALL_METHODS + methodLabel 派生（单源，消除本地标签重复漂移，评审 v2 P2-3）。
const METHODS: { value: TrainMethod; label: string }[] = ALL_METHODS.map((m) => ({ value: m, label: methodLabel(m) }));

/**
 * 权重模型编辑器（新建 | 编辑）：成员选择复用 MemberPalette、方法复用既有 METHODS。保存后回列表。
 * 模型仅承载「成员 + 方法 + 配置」规格；权重由「训练」生成（编辑器不训练，与 builder 职责区分）。
 */
export default function WeightModelEditor({
  editingId,
  onSaved,
  onCancel,
}: {
  editingId: number | null;
  onSaved: () => void;
  onCancel: () => void;
}) {
  const qc = useQueryClient();
  const wb = useWorkbench();
  const lib = useQuery({ queryKey: ["factor", "library"], queryFn: factorApi.library });
  const existing = useQuery({
    queryKey: ["weight-models", editingId],
    queryFn: () => weightModelApi.get(editingId as number),
    enabled: editingId != null,
  });

  const [members, setMembers] = useState<FactorMember[]>([]);
  const [method, setMethod] = useState<TrainMethod>("linear");
  const [name, setName] = useState("我的权重模型");
  const [horizon, setHorizon] = useState(6);
  const [notes, setNotes] = useState("");
  const [notice, setNotice] = useState<string | null>(null);

  // 编辑态：模型载入后回填一次。
  useEffect(() => {
    const data = existing.data;
    if (editingId == null || !data) return;
    setMembers(data.members);
    setMethod(data.method);
    setName(data.name);
    setHorizon(data.horizon);
    setNotes(data.notes);
  }, [editingId, existing.data]);

  const memberError = validateMembers(members);
  const nameError = !name.trim() ? "请填写模型名称" : "";   // H-F6：模型名必填校验

  const save = useMutation({
    mutationFn: () => {
      const trimmed = name.trim();
      // 建模从 wb.symbols 取组（factor-lab-symbol-groups §3.5）：焦点 = wb.instId；group 目标随来源组 id。
      const symbolGroupId = wb.target.kind === "group" ? wb.target.groupId : undefined;
      const body = editorToCreateBody({
        name: trimmed, instId: wb.instId, bar: wb.bar, members, method, horizon, notes,
        symbols: wb.symbols, symbolGroupId });
      return editingId == null
        ? weightModelApi.create(body)
        : weightModelApi.update(editingId, {
            name: trimmed, members, method, horizon, notes,
            ...(wb.symbols.length > 1 ? { symbols: wb.symbols } : {}),
            ...(symbolGroupId ? { symbolGroupId } : {}) });
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["weight-models"] });
      onSaved();
    },
    onError: (err) => setNotice(`保存失败：${(err as Error)?.message}`),
  });

  const handleToggle = (member: FactorMember) => {
    const { next, capped } = toggleMember(members, member, MAX_MEMBERS);
    if (capped) { setNotice(`最多 ${MAX_MEMBERS} 个因子成员`); return; }
    setMembers(next);
  };
  const handleAddMany = (candidates: FactorMember[]) => {
    const { next, skipped } = addMembersCapped(members, candidates, MAX_MEMBERS);
    setMembers(next);
    if (skipped > 0) setNotice(`${skipped} 个因重复或超上限未加入`);
  };

  return (
    <div className="flex flex-col gap-4 pb-6">
      <div className="flex items-center gap-2">
        <Button variant="ghost" size="sm" onClick={onCancel}>← 返回列表</Button>
        <span className="text-sm font-semibold text-[var(--text)]">{editingId == null ? "新建权重模型" : "编辑权重模型"}</span>
      </div>

      <MemberPalette
        library={lib.data ?? []}
        members={members}
        onToggle={handleToggle}
        onAddMany={handleAddMany}
        onClear={() => setMembers([])}
        onRemove={(i) => setMembers((prev) => prev.filter((_, idx) => idx !== i))}
        onDirection={(i, direction) => setMembers((prev) => prev.map((m, idx) => (idx === i ? { ...m, direction } : m)))}
        maxMembers={MAX_MEMBERS}
      />

      <div className="card flex flex-wrap items-end gap-3 p-3">
        <label className="flex flex-1 flex-col gap-1" style={{ minWidth: 180 }}>
          <span className="text-[11px] text-[var(--muted)]">模型名称</span>
          <Input
            value={name}
            onChange={(e) => setName(e.target.value)}
            invalid={!!nameError}
            className="py-1.5 text-xs"
          />
          {nameError && <span className="text-[11px] text-[var(--short)]">{nameError}</span>}
        </label>
        <label className="flex flex-col gap-1">
          <span className="text-[11px] text-[var(--muted)]">训练方法</span>
          <Select value={method} onChange={(e) => setMethod(e.target.value as TrainMethod)} className="py-1 text-xs" title={methodDescription(method)}>
            {METHODS.map((m) => (<option key={m.value} value={m.value} title={methodDescription(m.value)}>{m.label}</option>))}
          </Select>
        </label>
        <label className="flex flex-col gap-1">
          <span className="text-[11px] text-[var(--muted)]">前瞻根数</span>
          <input
            type="number"
            min={1}
            max={100}
            value={horizon}
            onChange={(e) => setHorizon(Math.max(1, Math.min(100, Number(e.target.value) || 6)))}
            className="w-24 rounded-[8px] border border-[var(--border)] bg-[var(--surface)] px-2 py-1 text-xs tabular"
          />
        </label>
      </div>

      <label className="card flex flex-col gap-1 p-3">
        <span className="text-[11px] text-[var(--muted)]">备注</span>
        <Input value={notes} onChange={(e) => setNotes(e.target.value)} placeholder="可选" className="py-1.5 text-xs" />
      </label>

      <div className="flex flex-wrap items-center gap-2">
        <Button variant="primary" disabled={!!memberError || !!nameError || save.isPending} onClick={() => save.mutate()}>
          {save.isPending ? "保存中..." : editingId == null ? "创建模型" : "保存修改"}
        </Button>
        {memberError && <span className="text-[11px] text-[var(--short)]">{memberError}</span>}
      </div>
      <p className="text-[11px] text-[var(--muted)]">
        模型保存后会出现在列表中；点列表上的「训练」即可在实时视图里训练并查看 loss/IC 曲线。
      </p>
      {notice && <p className="text-[11px] font-medium text-[var(--accent)]">{notice}</p>}
    </div>
  );
}
