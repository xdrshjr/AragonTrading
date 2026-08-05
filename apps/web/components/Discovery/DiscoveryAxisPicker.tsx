"use client";

import { Field, Input } from "@/components/ui";
import { AXIS_LABEL_CN } from "@/lib/discoveryStrategy";
import type { AxisInfo, DiscoveryAxis, TemplateSpec } from "@/lib/discoveryTypes";
import DiscoveryTemplatePicker from "./DiscoveryTemplatePicker";

export interface AxisSelection {
  axes: DiscoveryAxis[];
  templates: string[];
  includeLibraryFactors: boolean;
  comboSize: { min: number; max: number };
  rotationSize: { min: number; max: number };
}

/**
 * 多选轴卡片组（discovery-strategy-axes §2.12）。
 *
 * 三条纪律：
 * ① 每张卡片写清「这条轴挖什么」与「每个候选大约几次回测」——用户得能预判成本；
 * ② 依赖关系直接写在卡片上（轮动轴的成员只来自本任务常规策略轴的产出）；
 * ③ **绝不出现「敬请期待」这类占位**——四条轴全部可选，能选就能产候选。
 */
export default function DiscoveryAxisPicker({
  allAxes,
  axisInfo,
  templates,
  value,
  onChange,
}: {
  allAxes: DiscoveryAxis[];
  axisInfo: Record<string, AxisInfo>;
  templates: TemplateSpec[];
  value: AxisSelection;
  onChange: (next: AxisSelection) => void;
}) {
  const patch = (next: Partial<AxisSelection>) => onChange({ ...value, ...next });

  const toggleAxis = (axis: DiscoveryAxis) => {
    const has = value.axes.includes(axis);
    let axes = has ? value.axes.filter((item) => item !== axis) : [...value.axes, axis];
    // 轮动轴的成员只来自本任务常规策略轴的 kept 池（D-18）：勾它就自动带上上游轴，
    // 取消上游轴则一并取消它——否则提交必然 400，等于让用户去撞一堵看不见的墙。
    if (axis === "strategy_combo" && !has && !axes.includes("strategy_param")) {
      axes = [...axes, "strategy_param"];
    }
    if (axis === "strategy_param" && has) {
      axes = axes.filter((item) => item !== "strategy_combo");
    }
    patch({ axes: allAxes.filter((item) => axes.includes(item)) });
  };

  return (
    <div className="flex flex-col gap-2">
      <ul className="grid grid-cols-2 gap-2" aria-label="搜索轴">
        {allAxes.map((axis) => {
          const info = axisInfo[axis];
          const active = value.axes.includes(axis);
          const depends = (info?.dependsOn ?? []).map((item) => AXIS_LABEL_CN[item] ?? item);
          return (
            <li key={axis}>
              <label
                className={`flex h-full cursor-pointer flex-col gap-1 rounded-[10px] border p-2.5 transition-colors ${
                  active
                    ? "border-[var(--accent)] bg-[var(--accent)]/8"
                    : "border-[var(--border)] hover:bg-[var(--surface-2)]"
                }`}
              >
                <span className="flex items-center gap-2">
                  <input
                    type="checkbox"
                    aria-label={`搜索轴 ${info?.label ?? AXIS_LABEL_CN[axis] ?? axis}`}
                    checked={active}
                    onChange={() => toggleAxis(axis)}
                  />
                  <span className="text-xs font-medium text-[var(--text)]">
                    {info?.label ?? AXIS_LABEL_CN[axis] ?? axis}
                  </span>
                </span>
                <span className="text-[10px] leading-relaxed text-[var(--muted)]">
                  {info?.desc ?? ""}
                </span>
                <span className="text-[10px] tabular text-[var(--muted)]">
                  {info?.backtestsPerCandidate
                    ? `每个候选约 ${info.backtestsPerCandidate} 次回测`
                    : "无需回测（统计式验证）"}
                  {depends.length ? ` · 依赖：${depends.join("、")}` : ""}
                </span>
              </label>
            </li>
          );
        })}
      </ul>

      {value.axes.includes("strategy_param") && (
        <section className="rounded-[10px] border border-[var(--border)] p-2.5">
          <h4 className="mb-1.5 text-[11px] font-medium text-[var(--text)]">常规策略 · 模板白名单</h4>
          <DiscoveryTemplatePicker
            templates={templates}
            selected={value.templates}
            onChange={(next) => patch({ templates: next })}
          />
        </section>
      )}

      {value.axes.includes("factor_combo") && (
        <section className="rounded-[10px] border border-[var(--border)] p-2.5">
          <h4 className="mb-1.5 text-[11px] font-medium text-[var(--text)]">因子组合 · 成员与规模</h4>
          <label className="mb-2 flex items-center gap-2 text-[11px] text-[var(--muted)]">
            <input
              type="checkbox"
              checked={value.includeLibraryFactors}
              onChange={(e) => patch({ includeLibraryFactors: e.target.checked })}
            />
            纳入因子库里的既有因子（关闭后只用本任务挖到的因子）
          </label>
          <SizeRange
            idPrefix="combo"
            label="组合成员数"
            hint="2–5，越大越慢"
            value={value.comboSize}
            bounds={{ min: 2, max: 5 }}
            onChange={(next) => patch({ comboSize: next })}
          />
        </section>
      )}

      {value.axes.includes("strategy_combo") && (
        <section className="rounded-[10px] border border-[var(--border)] p-2.5">
          <h4 className="mb-1.5 text-[11px] font-medium text-[var(--text)]">策略轮动 · 规模</h4>
          <p className="mb-2 text-[10px] leading-relaxed text-[var(--muted)]">
            轮动成员只来自本任务「常规策略」轴挖到的策略。该轴在上游产出足够之前会把名额
            **让渡**给其它轴，实时监控页会如实显示让渡了多少、让给了谁。
          </p>
          <SizeRange
            idPrefix="rotation"
            label="轮动成员数"
            hint="2–4，每个成员各跑一次回测"
            value={value.rotationSize}
            bounds={{ min: 2, max: 4 }}
            onChange={(next) => patch({ rotationSize: next })}
          />
        </section>
      )}
    </div>
  );
}

function SizeRange({
  idPrefix,
  label,
  hint,
  value,
  bounds,
  onChange,
}: {
  idPrefix: string;
  label: string;
  hint: string;
  value: { min: number; max: number };
  bounds: { min: number; max: number };
  onChange: (next: { min: number; max: number }) => void;
}) {
  const clamp = (raw: number) =>
    Math.max(bounds.min, Math.min(bounds.max, Number.isFinite(raw) ? raw : bounds.min));
  return (
    <div className="grid grid-cols-2 gap-2">
      <Field label={`${label}（下限）`} htmlFor={`${idPrefix}-min`} hint={hint}>
        <Input
          id={`${idPrefix}-min`}
          type="number"
          value={value.min}
          onChange={(e) => {
            const min = clamp(Number(e.target.value));
            onChange({ min, max: Math.max(min, value.max) });
          }}
        />
      </Field>
      <Field label={`${label}（上限）`} htmlFor={`${idPrefix}-max`}>
        <Input
          id={`${idPrefix}-max`}
          type="number"
          value={value.max}
          onChange={(e) => {
            const max = clamp(Number(e.target.value));
            onChange({ min: Math.min(value.min, max), max });
          }}
        />
      </Field>
    </div>
  );
}
