"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "@/lib/api";
import {
  clampComboPolicy,
  DEFAULT_COMBO_POLICY,
  extractComboPolicy,
  groupBreakdownByRegime,
  importComboMembers,
  isUserCombo,
  MAX_MEMBERS,
  resolveMemberIds,
  selectedMembers,
  validateMemberCount,
} from "@/lib/combo";
import { POLICY_HINTS } from "@/lib/comboPolicyHints";
import { colorForReturn, fmtPct } from "@/lib/format";
import { regimeLabel } from "@/lib/regimeAffinity";
import { useAppStore } from "@/lib/store";
import type {
  ComboMemberSpec,
  ComboPolicy,
  ComboPreviewResult,
  ParamSchema,
  Strategy,
} from "@/lib/types";
import { Button } from "@/components/ui";
import ComboDetailModal from "./ComboDetailModal";
import ComboTimeline from "./ComboTimeline";
import DeleteComboConfirm from "./DeleteComboConfirm";
import MemberPicker from "./MemberPicker";
import MetricsCards from "./MetricsCards";
import SavedCombosList from "./SavedCombosList";

export default function ComboView() {
  const qc = useQueryClient();
  const {
    instId,
    bar,
    simBalance,
    backtestStartTs,
    backtestEndTs,
    comboDraft,
    setComboDraft,
    comboEditingId,
    setComboEditingId,
    setRunId,
    setMode,
  } = useAppStore();
  const { data: strategies, isLoading } = useQuery({
    queryKey: ["strategies"],
    queryFn: api.strategies,
  });

  const [result, setResult] = useState<ComboPreviewResult | null>(null);
  const [name, setName] = useState("");
  const [saveError, setSaveError] = useState<string | null>(null);
  const [importNotice, setImportNotice] = useState<string | null>(null);
  const [detailCombo, setDetailCombo] = useState<Strategy | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<Strategy | null>(null);

  const all = strategies ?? [];
  const candidates = all.filter((s) => s.kind !== "combo" && !s.unavailable && s.enabled);
  const userCombos = all.filter(isUserCombo);
  const pickerCombos = userCombos.filter((s) => s.id !== comboEditingId);
  const comboTemplate = all.find((s) => s.templateKey === "regime_rotation");
  const policySchema: ParamSchema = comboTemplate?.paramsSchema ?? {};
  const memberIds = comboDraft.memberIds.filter((id) => candidates.some((s) => s.id === id));
  const policy = comboDraft.policy;
  const memberError = validateMemberCount(memberIds.length);
  const nameError = name.trim().length === 0;
  const isEditing = comboEditingId !== null;

  const toggleMember = (id: number) => {
    const next = memberIds.includes(id)
      ? memberIds.filter((x) => x !== id)
      : memberIds.length >= MAX_MEMBERS
        ? memberIds
        : [...memberIds, id];
    setComboDraft({ ...comboDraft, memberIds: next });
  };

  const setPolicyValue = (key: string, value: unknown) =>
    setComboDraft({ ...comboDraft, policy: { ...policy, [key]: value } as ComboPolicy });

  // 复位编辑器到「新建」空态（兼作「+ 新建组合」「放弃编辑」「保存成功后」三处复用）。
  const resetEditor = () => {
    setComboDraft({ memberIds: [], policy: { ...DEFAULT_COMBO_POLICY } });
    setComboEditingId(null);
    setName("");
    setResult(null);
    setSaveError(null);
    setImportNotice(null);
  };

  // 进入编辑态：成员快照映射回草稿 id、取出轮动参数、回填名称（R4b），并置编辑指针。
  const loadForEdit = (combo: Strategy) => {
    const members = Array.isArray(combo.params?.members)
      ? (combo.params.members as ComboMemberSpec[])
      : [];
    const { memberIds: ids } = resolveMemberIds(members, candidates);
    setComboDraft({ memberIds: ids, policy: extractComboPolicy(combo.params ?? {}) });
    setComboEditingId(combo.id);
    setName(combo.name);
    setResult(null);
    setSaveError(null);
    setImportNotice(null);
    setDetailCombo(null);
  };

  const handleImport = (combo: Strategy) => {
    const { memberIds: next, importedCount, dropped } = importComboMembers(
      combo,
      candidates,
      memberIds,
    );
    setComboDraft({ ...comboDraft, memberIds: next });
    const parts = [`已展开导入 ${importedCount} 个原子成员（组合不可嵌套）`];
    if (dropped.length) parts.push(`${dropped.length} 个成员不可用已跳过`);
    setImportNotice(parts.join(" · "));
  };

  const preview = useMutation({
    mutationFn: () =>
      api.comboPreview({
        members: selectedMembers(memberIds, candidates),
        policy: clampComboPolicy(policy),
        instId,
        bar,
        limit: 1000,
        startTs: backtestStartTs,
        endTs: backtestEndTs,
        initialCapital: simBalance,
      }),
    onSuccess: (res) => {
      setResult(res);
      setRunId(res.runId);
    },
  });

  const buildParams = () => ({
    members: selectedMembers(memberIds, candidates),
    ...clampComboPolicy(policy),
  });

  const create = useMutation({
    mutationFn: () =>
      api.createStrategy({ templateKey: "regime_rotation", name: name.trim(), params: buildParams() }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["strategies"] });
      resetEditor();
    },
    onError: (e) => setSaveError((e as Error).message),
  });

  const update = useMutation({
    mutationFn: (id: number) => api.updateStrategy(id, { name: name.trim(), params: buildParams() }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["strategies"] });
      resetEditor();
    },
    onError: (e) => setSaveError((e as Error).message),
  });

  const remove = useMutation({
    mutationFn: (id: number) => api.deleteStrategy(id),
    onSuccess: (_data, id) => {
      qc.invalidateQueries({ queryKey: ["strategies"] });
      if (comboEditingId === id) resetEditor();
      if (detailCombo?.id === id) setDetailCombo(null);
      setDeleteTarget(null);
    },
  });

  const runBacktest = useMutation({
    mutationFn: (id: number) =>
      api.backtest(id, {
        instId,
        bar,
        limit: 1000,
        startTs: backtestStartTs,
        endTs: backtestEndTs,
        initialCapital: simBalance,
      }),
    onSuccess: (res) => {
      setRunId(res.runId);
      if (res.combo) setResult({ runId: res.runId, metrics: res.metrics, combo: res.combo });
    },
  });

  const handleSave = () => {
    if (memberError || nameError) return;
    if (comboEditingId !== null) update.mutate(comboEditingId);
    else create.mutate();
  };

  // 加载守门置于全部 hooks 之后（杜绝条件式调用 hooks）。
  if (isLoading || !strategies) {
    return <div className="py-8 text-center text-xs text-[var(--muted)]">加载策略…</div>;
  }

  const saving = create.isPending || update.isPending;
  const backtestingId = runBacktest.isPending ? (runBacktest.variables ?? null) : null;

  return (
    <div className="flex flex-col gap-4">
      <SavedCombosList
        combos={userCombos}
        isLoading={isLoading}
        editingId={comboEditingId}
        backtestingId={backtestingId}
        onNew={resetEditor}
        onView={setDetailCombo}
        onEdit={loadForEdit}
        onDelete={setDeleteTarget}
        onBacktest={(combo) => runBacktest.mutate(combo.id)}
      />

      <div className="flex flex-col gap-3 border-t border-[var(--border)] pt-3">
        <div>
          <h3 className="text-sm font-semibold text-[var(--text)]">
            {isEditing ? `编辑组合 · ${name || "未命名"}` : "新建组合 · 体制自适应轮动"}
          </h3>
          <p className="text-[11px] leading-relaxed text-[var(--muted)]">
            自动识别不同时段市场体制并切换到近端表现最优的成员策略；决策滞后 1 根、严格因果无未来函数。
          </p>
        </div>

        <NameField value={name} onChange={setName} invalid={nameError} />

        <MemberPicker
          candidates={candidates}
          userCombos={pickerCombos}
          memberIds={memberIds}
          maxMembers={MAX_MEMBERS}
          onToggle={toggleMember}
          onImportCombo={handleImport}
          onViewCombo={setDetailCombo}
          importNotice={importNotice}
        />

        <PolicyEditor schema={policySchema} policy={policy} onChange={setPolicyValue} />

        <div className="flex flex-wrap items-center gap-2">
          <Button
            variant="primary"
            size="sm"
            disabled={!!memberError || nameError || saving}
            onClick={handleSave}
          >
            {saving ? "保存中…" : isEditing ? "保存修改" : "保存组合"}
          </Button>
          <Button
            variant="default"
            size="sm"
            disabled={!!memberError || preview.isPending}
            onClick={() => preview.mutate()}
          >
            {preview.isPending ? "回测中…" : "运行组合回测"}
          </Button>
          {isEditing && (
            <Button variant="ghost" size="sm" onClick={resetEditor}>
              放弃编辑
            </Button>
          )}
          {memberError && <span className="text-[11px] text-[var(--muted)]">{memberError}</span>}
          {!memberError && nameError && (
            <span className="text-[11px] text-[var(--muted)]">请填写组合名称</span>
          )}
          {saveError && <span className="text-[11px] text-[var(--short)]">保存失败：{saveError}</span>}
        </div>

        {preview.isError && (
          <div className="rounded-[8px] border border-[var(--short)] bg-[var(--surface)] px-3 py-2 text-[11px] text-[var(--short)]">
            回测失败：{(preview.error as Error)?.message}
          </div>
        )}
      </div>

      {result && <ComboResult result={result} onGoCompare={() => setMode("compare")} />}

      {detailCombo && (
        <ComboDetailModal
          combo={detailCombo}
          strategies={all}
          editDisabled={comboEditingId !== null && comboEditingId !== detailCombo.id}
          onClose={() => setDetailCombo(null)}
          onEdit={loadForEdit}
          onDelete={(c) => {
            setDetailCombo(null);
            setDeleteTarget(c);
          }}
        />
      )}

      {deleteTarget && (
        <DeleteComboConfirm
          combo={deleteTarget}
          pending={remove.isPending}
          error={remove.isError ? (remove.error as Error).message : null}
          onCancel={() => setDeleteTarget(null)}
          onConfirm={() => remove.mutate(deleteTarget.id)}
        />
      )}
    </div>
  );
}

function NameField({
  value,
  onChange,
  invalid,
}: {
  value: string;
  onChange: (v: string) => void;
  invalid: boolean;
}) {
  return (
    <div className="flex flex-col gap-1">
      <label htmlFor="combo-name" className="text-[11px] font-medium text-[var(--text)]">
        组合名称
      </label>
      <input
        id="combo-name"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder="例如：趋势·震荡自适应组合"
        aria-invalid={invalid || undefined}
        className="rounded-[8px] border border-[var(--border)] bg-[var(--surface)] px-2.5 py-1.5 text-xs text-[var(--text)] placeholder:text-[var(--muted)] outline-none transition-colors duration-150 hover:border-[var(--accent)] focus:border-[var(--accent)]"
      />
    </div>
  );
}

function PolicyEditor({
  schema,
  policy,
  onChange,
}: {
  schema: ParamSchema;
  policy: ComboPolicy;
  onChange: (key: string, value: unknown) => void;
}) {
  const entries = Object.entries(schema);
  if (entries.length === 0) return null;
  const values = policy as unknown as Record<string, unknown>;
  return (
    <div className="flex flex-col gap-1.5">
      <span className="text-[11px] font-medium text-[var(--text)]">轮动参数</span>
      <div className="grid grid-cols-2 gap-2">
        {entries.map(([key, def]) => {
          const value = values[key] ?? def.default;
          return (
            <label key={key} className="flex flex-col gap-1" title={POLICY_HINTS[key] ?? def.label}>
              <span className="text-[11px] text-[var(--muted)]">{def.label}</span>
              {def.type === "bool" ? (
                <input
                  type="checkbox"
                  checked={Boolean(value)}
                  onChange={(e) => onChange(key, e.target.checked)}
                  className="h-4 w-4 accent-[var(--accent)]"
                />
              ) : def.type === "enum" ? (
                <select
                  value={String(value)}
                  onChange={(e) => onChange(key, e.target.value)}
                  className="rounded-[8px] border border-[var(--border)] bg-[var(--surface)] px-2 py-1 text-xs"
                >
                  {(def.options || []).map((option) => (
                    <option key={option} value={option}>
                      {option}
                    </option>
                  ))}
                </select>
              ) : (
                <input
                  type="number"
                  value={Number(value)}
                  min={def.min}
                  max={def.max}
                  step={def.step ?? (def.type === "int" ? 1 : "any")}
                  onChange={(e) =>
                    onChange(
                      key,
                      def.type === "int"
                        ? parseInt(e.target.value || "0", 10)
                        : parseFloat(e.target.value),
                    )
                  }
                  className="rounded-[8px] border border-[var(--border)] bg-[var(--surface)] px-2 py-1 text-xs tabular"
                />
              )}
            </label>
          );
        })}
      </div>
      <p className="text-[11px] leading-relaxed text-[var(--muted)]">
        提示：成员宜少而互补；体制门控 + 切换迟滞 + 最短持有共同抑制过拟合与频繁切换。
      </p>
    </div>
  );
}

function ComboResult({
  result,
  onGoCompare,
}: {
  result: ComboPreviewResult;
  onGoCompare: () => void;
}) {
  const { metrics, combo } = result;
  return (
    <div className="flex flex-col gap-3 border-t border-[var(--border)] pt-3">
      <div className="rounded-[10px] border border-[var(--accent)] bg-[var(--accent-soft)] px-3 py-2">
        <div className="text-[11px] text-[var(--accent)]">期望年化收益</div>
        <div
          className="tabular text-xl font-bold"
          style={{ color: colorForReturn(combo.annualizedReturn) }}
        >
          {fmtPct(combo.annualizedReturn)}
        </div>
      </div>

      <MetricsCards metrics={metrics} />

      <div className="flex flex-col gap-1.5">
        <span className="text-[11px] font-medium text-[var(--text)]">轮动时间线</span>
        <ComboTimeline segments={combo.timeline} />
      </div>

      <BreakdownTable rows={combo.breakdown} />

      {combo.notes && combo.notes.length > 0 && (
        <ul className="flex flex-col gap-0.5 text-[11px] text-[var(--muted)]">
          {combo.notes.map((note, i) => (
            <li key={i}>· {note}</li>
          ))}
        </ul>
      )}

      <div className="flex flex-wrap items-center gap-2">
        <button
          onClick={onGoCompare}
          className="rounded-[6px] px-2 py-0.5 text-[11px] text-[var(--accent)] hover:bg-[var(--accent-soft)]"
        >
          在对比中查看已保存组合 →
        </button>
      </div>
    </div>
  );
}

function BreakdownTable({ rows }: { rows: ComboPreviewResult["combo"]["breakdown"] }) {
  if (!rows.length) return null;
  const groups = groupBreakdownByRegime(rows);
  return (
    <div className="flex flex-col gap-1.5">
      <span className="text-[11px] font-medium text-[var(--text)]">分体制贡献</span>
      <table className="w-full text-xs">
        <thead className="text-[var(--muted)]">
          <tr className="text-left">
            <th className="py-1 pr-2 font-medium">体制 / 成员</th>
            <th className="py-1 pr-2 text-right font-medium">根数</th>
            <th className="py-1 text-right font-medium">贡献</th>
          </tr>
        </thead>
        <tbody>
          {groups.map((group) => (
            <RegimeRows key={group.regime} group={group} />
          ))}
        </tbody>
      </table>
    </div>
  );
}

function RegimeRows({ group }: { group: ReturnType<typeof groupBreakdownByRegime>[number] }) {
  return (
    <>
      <tr className="border-t border-[var(--border)]">
        <td className="py-1 pr-2 font-medium text-[var(--text)]">{regimeLabel(group.regime)}</td>
        <td className="py-1 pr-2 text-right tabular text-[var(--muted)]">{group.bars}</td>
        <td className="py-1 text-right" />
      </tr>
      {group.rows.map((row) => (
        <tr key={`${group.regime}-${row.memberKey}`}>
          <td className="py-1 pl-3 pr-2 text-[var(--muted)]">{row.memberName}</td>
          <td className="py-1 pr-2 text-right tabular">{row.bars}</td>
          <td
            className="py-1 text-right tabular"
            style={{ color: colorForReturn(row.contribution) }}
          >
            {fmtPct(row.contribution)}
          </td>
        </tr>
      ))}
    </>
  );
}
