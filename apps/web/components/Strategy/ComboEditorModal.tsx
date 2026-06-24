"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import {
  clampComboPolicy,
  DEFAULT_COMBO_POLICY,
  extractComboPolicy,
  importComboMembers,
  isUserCombo,
  MAX_MEMBERS,
  resolveMemberIds,
  selectedMembers,
  validateMemberCount,
} from "@/lib/combo";
import { POLICY_HINTS } from "@/lib/comboPolicyHints";
import { useAppStore } from "@/lib/store";
import type {
  ComboMemberSpec,
  ComboPolicy,
  ComboPreviewResult,
  ParamSchema,
  Strategy,
} from "@/lib/types";
import { Button, Modal } from "@/components/ui";
import ComboResult from "./ComboResult";
import MemberPicker from "./MemberPicker";

/**
 * 新建 / 编辑组合的居中弹窗（需求 2）：复用 `Modal` 原语（焦点陷阱 / Esc / 遮罩 / 滚动锁 / 焦点恢复 /
 * Portal 居中），sticky 顶部标题与底部主操作（长表单滚动时主按钮常驻可见）。
 *
 * 成员 / 轮动参数草稿落 store.comboDraft（持久化）；名称 / 预览结果 / 错误态为弹窗本地态。
 * 打开瞬态（关→开）：编辑态从组合行回填成员/参数/名称，新建态清空草稿（与旧「+ 新建组合」同口径）。
 */
export default function ComboEditorModal({
  open,
  editingId,
  onClose,
  onSaved,
}: {
  open: boolean;
  editingId: number | null;
  onClose: () => void;
  onSaved?: (created: Strategy | null) => void;
}) {
  const qc = useQueryClient();
  const { instId, bar, simBalance, backtestStartTs, backtestEndTs, comboDraft, setComboDraft, setRunId } =
    useAppStore();
  const { data: strategies } = useQuery({ queryKey: ["strategies"], queryFn: api.strategies });

  const [name, setName] = useState("");
  const [result, setResult] = useState<ComboPreviewResult | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [importNotice, setImportNotice] = useState<string | null>(null);
  const initializedRef = useRef(false);

  const all = strategies ?? [];
  const candidates = all.filter((s) => s.kind !== "combo" && !s.unavailable && s.enabled);
  const pickerCombos = all.filter((s) => isUserCombo(s) && s.id !== editingId);
  const comboTemplate = all.find((s) => s.templateKey === "regime_rotation");
  const policySchema: ParamSchema = comboTemplate?.paramsSchema ?? {};
  const memberIds = comboDraft.memberIds.filter((id) => candidates.some((s) => s.id === id));
  const policy = comboDraft.policy;
  const memberError = validateMemberCount(memberIds.length);
  const nameError = name.trim().length === 0;
  const isEditing = editingId !== null;

  // 仅在开启沿（关→开）且 strategies 已就绪时初始化一次，避免：① strategies 异步加载完成前编辑态回填到空；
  // ② 写后 invalidate 刷新 strategies 时把用户正在编辑的内容清空。关闭即复位 initializedRef，下次开启再初始化。
  useEffect(() => {
    if (!open) {
      initializedRef.current = false;
      return;
    }
    if (initializedRef.current || !strategies) return;
    initializedRef.current = true;
    const atoms = strategies.filter((s) => s.kind !== "combo" && !s.unavailable && s.enabled);
    if (editingId != null) {
      const combo = strategies.find((s) => s.id === editingId);
      const members = Array.isArray(combo?.params?.members)
        ? (combo?.params?.members as ComboMemberSpec[])
        : [];
      const { memberIds: ids } = resolveMemberIds(members, atoms);
      setComboDraft({ memberIds: ids, policy: extractComboPolicy(combo?.params ?? {}) });
      setName(combo?.name ?? "");
    } else {
      setComboDraft({ memberIds: [], policy: { ...DEFAULT_COMBO_POLICY } });
      setName("");
    }
    setResult(null);
    setSaveError(null);
    setImportNotice(null);
  }, [open, editingId, strategies, setComboDraft]);

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

  const handleImport = (combo: Strategy) => {
    const { memberIds: next, importedCount, dropped } = importComboMembers(combo, candidates, memberIds);
    setComboDraft({ ...comboDraft, memberIds: next });
    const parts = [`已展开导入 ${importedCount} 个原子成员（组合不可嵌套）`];
    if (dropped.length) parts.push(`${dropped.length} 个成员不可用已跳过`);
    setImportNotice(parts.join(" · "));
  };

  const buildParams = () => ({
    members: selectedMembers(memberIds, candidates),
    ...clampComboPolicy(policy),
  });

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

  const create = useMutation({
    mutationFn: () =>
      api.createStrategy({ templateKey: "regime_rotation", name: name.trim(), params: buildParams() }),
    onSuccess: (created) => {
      qc.invalidateQueries({ queryKey: ["strategies"] });
      onSaved?.(created);
      onClose();
    },
    onError: (e) => setSaveError((e as Error).message),
  });

  const update = useMutation({
    mutationFn: (id: number) => api.updateStrategy(id, { name: name.trim(), params: buildParams() }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["strategies"] });
      onClose();
    },
    onError: (e) => setSaveError((e as Error).message),
  });

  const saving = create.isPending || update.isPending;

  const handleSave = () => {
    if (memberError || nameError || saving) return;
    if (editingId !== null) update.mutate(editingId);
    else create.mutate();
  };

  if (!open) return null;

  const titleId = "combo-editor-title";

  return (
    <Modal open={open} onClose={onClose} labelledBy={titleId} className="max-w-3xl">
      <header className="flex items-start justify-between gap-3 border-b border-[var(--border)] px-5 py-3.5">
        <div className="flex min-w-0 flex-col">
          <h2 id={titleId} className="truncate text-sm font-semibold text-[var(--text)]">
            {isEditing ? `编辑组合 · ${name || "未命名"}` : "新建组合 · 体制自适应轮动"}
          </h2>
          <p className="text-[11px] leading-relaxed text-[var(--muted)]">
            自动识别不同时段市场体制并切换到近端表现最优的成员策略；决策滞后 1 根、严格因果无未来函数。
          </p>
        </div>
        <Button variant="ghost" size="sm" onClick={onClose} aria-label="关闭编辑器">
          关闭
        </Button>
      </header>

      <div className="flex flex-col gap-4 overflow-y-auto px-5 py-4">
        <NameField value={name} onChange={setName} invalid={nameError} />

        <MemberPicker
          candidates={candidates}
          userCombos={pickerCombos}
          memberIds={memberIds}
          maxMembers={MAX_MEMBERS}
          onToggle={toggleMember}
          onImportCombo={handleImport}
          importNotice={importNotice}
        />

        <PolicyEditor schema={policySchema} policy={policy} onChange={setPolicyValue} />

        {preview.isError && (
          <div className="rounded-[8px] border border-[var(--short)] bg-[var(--surface)] px-3 py-2 text-[11px] text-[var(--short)]">
            回测失败：{(preview.error as Error)?.message}
          </div>
        )}

        {result && <ComboResult result={result} />}
      </div>

      <footer className="flex flex-wrap items-center justify-between gap-2 border-t border-[var(--border)] px-5 py-3">
        <div className="flex min-h-[1.25rem] items-center gap-2 text-[11px]">
          {memberError && <span className="text-[var(--muted)]">{memberError}</span>}
          {!memberError && nameError && <span className="text-[var(--muted)]">请填写组合名称</span>}
          {saveError && <span className="text-[var(--short)]">保存失败：{saveError}</span>}
        </div>
        <div className="flex items-center gap-2">
          <Button
            variant="default"
            size="sm"
            disabled={!!memberError || preview.isPending}
            onClick={() => preview.mutate()}
          >
            {preview.isPending ? "回测中…" : "运行组合回测"}
          </Button>
          <Button variant="ghost" size="sm" onClick={onClose}>
            取消
          </Button>
          <Button
            variant="primary"
            size="sm"
            disabled={!!memberError || nameError || saving}
            onClick={handleSave}
          >
            {saving ? "保存中…" : isEditing ? "保存修改" : "保存组合"}
          </Button>
        </div>
      </footer>
    </Modal>
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
