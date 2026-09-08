"use client";

import type { ReactNode } from "react";

/**
 * 启动向导内部共用的**五个排版原子**（ai-trade-guided-launch §4.4）。
 *
 * 全部从 `AiTradeLaunchModal.tsx` 原样搬出、逻辑与类名逐字不改——本轮是重组，不是换皮。
 * 换一套字号会让 79 条既有用例的文案断言与视觉回归同时发生，两者混在一起没人能定位。
 */

export function ModeTab({
  active,
  disabled,
  title,
  onClick,
  children,
}: {
  active: boolean;
  disabled?: boolean;
  title?: string;
  onClick: () => void;
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      disabled={disabled}
      title={title}
      onClick={onClick}
      className={`flex-1 rounded px-3 py-1.5 text-xs font-medium transition-colors ${
        active
          ? "bg-[var(--surface)] text-[var(--text)] shadow-sm"
          : "text-[var(--muted)] hover:text-[var(--text)]"
      } ${disabled && !active ? "cursor-not-allowed opacity-40" : ""}`}
    >
      {children}
    </button>
  );
}

export function AllocModeTab({
  active,
  disabled,
  onClick,
  children,
}: {
  active: boolean;
  disabled?: boolean;
  onClick: () => void;
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      disabled={disabled}
      onClick={onClick}
      className={`rounded px-2 py-0.5 text-[10px] font-medium transition-colors ${
        active
          ? "bg-[var(--surface)] text-[var(--text)] shadow-sm"
          : "text-[var(--muted)] hover:text-[var(--text)]"
      } ${disabled && !active ? "cursor-not-allowed opacity-40" : ""}`}
    >
      {children}
    </button>
  );
}

export function Row({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-3 bg-[var(--bg)] px-3 py-2 text-xs">
      <span className="text-[var(--muted)]">{label}</span>
      <span className="tabular text-right font-medium text-[var(--text)]">{value}</span>
    </div>
  );
}

export function DateField({
  label,
  value,
  onChange,
  disabled,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  disabled?: boolean;
}) {
  return (
    <label className="flex flex-col gap-1 text-[11px] text-[var(--muted)]">
      <span>{label}</span>
      <input
        type="datetime-local"
        value={value}
        disabled={disabled}
        onChange={(e) => onChange(e.target.value)}
        className="rounded-md border border-[var(--border)] bg-[var(--bg)] px-2 py-1.5 text-xs text-[var(--text)] outline-none focus:border-[var(--accent)] disabled:opacity-50"
      />
    </label>
  );
}

export function Field({
  label,
  placeholder,
  value,
  onChange,
  disabled,
  hint,
}: {
  label: string;
  placeholder: string;
  value: string;
  onChange: (v: string) => void;
  disabled?: boolean;
  hint?: string;
}) {
  return (
    <label className="flex flex-col gap-1 text-[11px] text-[var(--muted)]">
      <span>{label}</span>
      <input
        type="text"
        inputMode="decimal"
        value={value}
        placeholder={placeholder}
        disabled={disabled}
        onChange={(e) => onChange(e.target.value)}
        // 显式 aria-label：本组件的 <label> 还包着可选 hint 文本，仅靠包裹关系解析出的可访问名会被 hint
        // 污染（既影响读屏也影响按标签取元素），故以字段名为准显式声明。
        aria-label={label}
        className="rounded-md border border-[var(--border)] bg-[var(--bg)] px-2 py-1.5 text-xs text-[var(--text)] outline-none focus:border-[var(--accent)] disabled:opacity-50"
      />
      {hint && <span className="leading-relaxed text-[var(--muted)]">{hint}</span>}
    </label>
  );
}

/**
 * 步骤内**就地内联**的风险条目（不变式 I-1 的渲染落点之一）。
 *
 * 与第 6 步风险清单里的那一份是同一条风险的两个渲染点：用户在产地那一步就该看见它，而最后一屏
 * 再无条件复述一次——「藏起来的警示等于消失的警示」。
 */
export function InlineRiskNote({
  tone,
  title,
  detail,
  testId,
}: {
  tone: "warn" | "danger";
  title: string;
  detail: string;
  testId?: string;
}) {
  const color = tone === "danger" ? "var(--short-ink)" : "var(--warn)";
  return (
    <p
      data-testid={testId}
      data-tone={tone}
      className="rounded border bg-[var(--surface-2)] px-2 py-1 text-[10px] leading-relaxed"
      style={{ color, borderColor: color }}
    >
      <span className="font-medium">{tone === "danger" ? "⚠ " : "△ "}</span>
      {title}
      {detail ? `：${detail}` : ""}
    </p>
  );
}
