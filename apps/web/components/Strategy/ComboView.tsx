"use client";

import { useMutation, useQueryClient, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "@/lib/api";
import {
  clampComboPolicy,
  groupBreakdownByRegime,
  MAX_MEMBERS,
  validateMemberCount,
} from "@/lib/combo";
import { colorForReturn, fmtPct } from "@/lib/format";
import { regimeAffinityLabels, regimeLabel } from "@/lib/regimeAffinity";
import { useAppStore } from "@/lib/store";
import type {
  ComboMemberSpec,
  ComboPolicy,
  ComboPreviewResult,
  ParamSchema,
  Strategy,
} from "@/lib/types";
import { Badge, Button } from "@/components/ui";
import ComboTimeline from "./ComboTimeline";
import MetricsCards from "./MetricsCards";

// 轮动参数 tooltip：解释每项的抗过拟合 / 抗未来函数含义（人机交互：让保守默认可被理解）。
const POLICY_HINTS: Record<string, string> = {
  rebalanceBars: "每隔多少根重新决策一次活跃成员。间隔越大切换越少、越稳健。",
  lookbackBars: "评估成员近端表现的回看窗口。宜覆盖多个再平衡周期，过短易过拟合。",
  selectionMetric: "在候选成员中选优的指标：收益率 / 夏普(风险调整) / 卡玛(收益/回撤)。",
  minDwellBars: "选中成员的最短持有根数，抑制频繁来回切换(whipsaw)。",
  switchMarginPct: "切换迟滞：挑战者须超出在任者该相对优势才替换，过滤噪声切换。",
  regimeGating: "仅在与当前体制匹配的候选成员内选优(注入领域先验，抗过拟合)。",
  turnoverBps: "每次切换扣的换手成本(基点)，近似建仓代价，避免高估收益。",
};

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
    setRunId,
    setMode,
  } = useAppStore();
  const { data: strategies } = useQuery({ queryKey: ["strategies"], queryFn: api.strategies });
  const [result, setResult] = useState<ComboPreviewResult | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);

  const candidates = (strategies ?? []).filter(
    (s) => s.kind !== "combo" && !s.unavailable && s.enabled,
  );
  const comboTemplate = (strategies ?? []).find((s) => s.templateKey === "regime_rotation");
  const policySchema: ParamSchema = comboTemplate?.paramsSchema ?? {};
  const memberIds = comboDraft.memberIds.filter((id) => candidates.some((s) => s.id === id));
  const policy = comboDraft.policy;
  const memberError = validateMemberCount(memberIds.length);

  const selectedMembers = (): ComboMemberSpec[] =>
    memberIds
      .map((id) => candidates.find((s) => s.id === id))
      .filter((s): s is Strategy => Boolean(s))
      .map((s) => ({ templateKey: s.templateKey, params: s.params, name: s.name }));

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

  const preview = useMutation({
    mutationFn: () =>
      api.comboPreview({
        members: selectedMembers(),
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

  const saveAs = useMutation({
    mutationFn: (name: string) =>
      api.createStrategy({
        templateKey: "regime_rotation",
        name,
        params: { members: selectedMembers(), ...clampComboPolicy(policy) },
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["strategies"] });
      setSaveError(null);
    },
    onError: (e) => setSaveError((e as Error).message),
  });

  // 加载守门置于全部 hooks 之后（杜绝条件式调用 hooks）。
  if (!strategies) {
    return <div className="py-8 text-center text-xs text-[var(--muted)]">加载策略…</div>;
  }

  return (
    <div className="flex flex-col gap-3">
      <div>
        <h3 className="text-sm font-semibold text-[var(--text)]">策略组合 · 体制自适应轮动</h3>
        <p className="text-[11px] leading-relaxed text-[var(--muted)]">
          自动识别不同时段市场体制并切换到近端表现最优的成员策略；决策滞后 1 根、严格因果无未来函数。
        </p>
      </div>

      <MemberPicker
        candidates={candidates}
        memberIds={memberIds}
        maxMembers={MAX_MEMBERS}
        onToggle={toggleMember}
      />

      <PolicyEditor schema={policySchema} policy={policy} onChange={setPolicyValue} />

      <div className="flex flex-wrap items-center gap-2">
        <Button
          variant="primary"
          size="sm"
          disabled={!!memberError || preview.isPending}
          onClick={() => preview.mutate()}
        >
          {preview.isPending ? "回测中…" : "运行组合回测"}
        </Button>
        {memberError && <span className="text-[11px] text-[var(--muted)]">{memberError}</span>}
      </div>

      {preview.isError && (
        <div className="rounded-[8px] border border-[var(--short)] bg-[var(--surface)] px-3 py-2 text-[11px] text-[var(--short)]">
          回测失败：{(preview.error as Error)?.message}
        </div>
      )}

      {result && (
        <ComboResult
          result={result}
          disabled={!!memberError}
          saveError={saveError}
          onSaveAs={(name) => saveAs.mutate(name)}
          onGoCompare={() => setMode("compare")}
        />
      )}
    </div>
  );
}

function MemberPicker({
  candidates,
  memberIds,
  maxMembers,
  onToggle,
}: {
  candidates: Strategy[];
  memberIds: number[];
  maxMembers: number;
  onToggle: (id: number) => void;
}) {
  const capReached = memberIds.length >= maxMembers;
  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex items-center justify-between">
        <span className="text-[11px] font-medium text-[var(--text)]">选择成员策略</span>
        <span className="text-[11px] text-[var(--muted)]">
          已选 {memberIds.length}/{maxMembers}
        </span>
      </div>
      <div className="flex flex-col gap-1">
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
  disabled,
  saveError,
  onSaveAs,
  onGoCompare,
}: {
  result: ComboPreviewResult;
  disabled: boolean;
  saveError: string | null;
  onSaveAs: (name: string) => void;
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
        <Button
          variant="default"
          size="sm"
          disabled={disabled}
          onClick={() => {
            const name = window.prompt("另存为组合名称", "我的策略组合");
            if (name) onSaveAs(name);
          }}
        >
          另存为组合
        </Button>
        <button
          onClick={onGoCompare}
          className="rounded-[6px] px-2 py-0.5 text-[11px] text-[var(--accent)] hover:bg-[var(--accent-soft)]"
        >
          在对比中查看已保存组合 →
        </button>
        {saveError && <span className="text-[11px] text-[var(--short)]">保存失败：{saveError}</span>}
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
