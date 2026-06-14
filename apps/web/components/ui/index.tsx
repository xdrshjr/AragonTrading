"use client";

import clsx from "clsx";
import React from "react";

/** 复用基础组件 (spec §8.2)：Button / Select / Card / Tabs / Skeleton / Badge。 */

export function Button({
  variant = "default",
  size = "md",
  className,
  ...props
}: React.ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "default" | "primary" | "ghost" | "danger";
  size?: "sm" | "md";
}) {
  return (
    <button
      className={clsx(
        "inline-flex items-center justify-center gap-1.5 rounded-[10px] font-medium transition-all duration-150 ease-smooth disabled:opacity-40 disabled:cursor-not-allowed",
        size === "sm" ? "px-2.5 py-1 text-xs" : "px-3.5 py-2 text-sm",
        variant === "primary" &&
          "bg-[var(--accent)] text-white hover:brightness-105 active:brightness-95 shadow-soft",
        variant === "default" &&
          "bg-[var(--surface-2)] text-[var(--text)] hover:bg-[var(--border)]",
        variant === "ghost" && "bg-transparent text-[var(--muted)] hover:text-[var(--text)] hover:bg-[var(--surface-2)]",
        variant === "danger" && "bg-transparent text-[var(--short)] hover:bg-[var(--accent-soft)]",
        className,
      )}
      {...props}
    />
  );
}

export function Select({
  className,
  ...props
}: React.SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <select
      className={clsx(
        "rounded-[10px] border border-[var(--border)] bg-[var(--surface)] px-2.5 py-1.5 text-sm text-[var(--text)]",
        "hover:border-[var(--accent)] focus:border-[var(--accent)] transition-colors cursor-pointer",
        className,
      )}
      {...props}
    />
  );
}

export function Card({ className, children, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div className={clsx("card", className)} {...props}>
      {children}
    </div>
  );
}

export function Badge({
  tone = "neutral",
  className,
  children,
}: {
  tone?: "neutral" | "long" | "short" | "accent";
  className?: string;
  children: React.ReactNode;
}) {
  return (
    <span
      className={clsx(
        "inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-medium tabular",
        tone === "neutral" && "bg-[var(--surface-2)] text-[var(--muted)]",
        tone === "long" && "bg-[color-mix(in_srgb,var(--long)_16%,transparent)] text-[var(--long)]",
        tone === "short" && "bg-[color-mix(in_srgb,var(--short)_16%,transparent)] text-[var(--short)]",
        tone === "accent" && "bg-[var(--accent-soft)] text-[var(--accent)]",
        className,
      )}
    >
      {children}
    </span>
  );
}

export function Skeleton({ className }: { className?: string }) {
  return <div className={clsx("skeleton", className)} />;
}

/** 开关原子组件（技术指标弹窗用）：role="switch" + aria-checked，复用设计 token，键盘 Space/Enter 切换。 */
export function Switch({
  checked,
  onChange,
  label,
  id,
  className,
  tabIndex,
}: {
  checked: boolean;
  onChange: (next: boolean) => void;
  label?: string;
  id?: string;
  className?: string;
  tabIndex?: number;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      id={id}
      tabIndex={tabIndex}
      onClick={() => onChange(!checked)}
      className={clsx(
        "relative inline-flex h-5 w-9 shrink-0 cursor-pointer items-center rounded-full transition-colors duration-150 ease-smooth",
        checked ? "bg-[var(--accent)]" : "bg-[var(--surface-2)] border border-[var(--border)]",
        className,
      )}
    >
      <span
        className={clsx(
          "inline-block h-3.5 w-3.5 transform rounded-full bg-white shadow-sm transition-transform duration-150 ease-smooth",
          checked ? "translate-x-[18px]" : "translate-x-[3px]",
        )}
      />
    </button>
  );
}

export function Tabs({
  tabs,
  active,
  onChange,
  variant = "segmented",
}: {
  tabs: { key: string; label: string }[];
  active: string;
  onChange: (key: string) => void;
  variant?: "segmented" | "underline";
}) {
  // underline 变体：靠左、内容宽度、下划线高亮（副图页签 + 下区数据页签共用）。
  // 评审 R-2：不加 overflow-x-auto（会令 overflow-y 计算为 auto 而裁掉 -mb-px 下划线）；
  // 两处调用方页签数少，单行不溢出。如未来需横向滚动，把溢出下沉到不承载底边线的内层 wrapper。
  if (variant === "underline") {
    return (
      <div className="flex items-center gap-1 border-b border-[var(--border)]">
        {tabs.map((t) => (
          <button
            key={t.key}
            onClick={() => onChange(t.key)}
            className={clsx(
              "-mb-px shrink-0 border-b-2 px-3 py-1.5 text-xs font-medium transition-colors duration-150",
              active === t.key
                ? "border-[var(--accent)] text-[var(--text)]"
                : "border-transparent text-[var(--muted)] hover:text-[var(--text)]",
            )}
          >
            {t.label}
          </button>
        ))}
      </div>
    );
  }
  // —— 既有 segmented 实现，原样保留（默认变体，不传 variant 的调用方零变化）——
  return (
    <div className="flex gap-1 rounded-[10px] bg-[var(--surface-2)] p-1">
      {tabs.map((t) => (
        <button
          key={t.key}
          onClick={() => onChange(t.key)}
          className={clsx(
            "flex-1 rounded-[7px] px-3 py-1.5 text-xs font-medium transition-all duration-150",
            active === t.key
              ? "bg-[var(--surface)] text-[var(--text)] shadow-soft"
              : "text-[var(--muted)] hover:text-[var(--text)]",
          )}
        >
          {t.label}
        </button>
      ))}
    </div>
  );
}
