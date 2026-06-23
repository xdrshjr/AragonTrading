"use client";

import clsx from "clsx";
import React, { useEffect, useRef } from "react";
import { createPortal } from "react-dom";

/** 复用基础组件 (spec §8.2)：Button / Select / Card / Tabs / Skeleton / Badge / Modal。 */

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

/** 文本 / 密码输入原子组件（设置中心表单用）：复用设计 token，自动适配明暗主题。
 * `invalid` 同步 `aria-invalid` 供读屏识别；`aria-describedby` 由调用方透传以串联错误说明（H6）。 */
export function Input({
  className,
  invalid,
  ...props
}: React.InputHTMLAttributes<HTMLInputElement> & { invalid?: boolean }) {
  return (
    <input
      aria-invalid={invalid || undefined}
      className={clsx(
        "w-full rounded-[10px] border bg-[var(--surface)] px-3 py-2 text-sm text-[var(--text)]",
        "placeholder:text-[var(--muted)] outline-none transition-colors duration-150 ease-smooth",
        "disabled:opacity-50 disabled:cursor-not-allowed",
        invalid
          ? "border-[var(--short)] focus:border-[var(--short)]"
          : "border-[var(--border)] hover:border-[var(--accent)] focus:border-[var(--accent)]",
        className,
      )}
      {...props}
    />
  );
}

/** 表单字段包裹：label + 控件（children）+ 错误 / 提示文案，统一垂直节奏。 */
export function Field({
  label,
  htmlFor,
  error,
  hint,
  className,
  children,
}: {
  label?: string;
  htmlFor?: string;
  error?: string;
  hint?: string;
  className?: string;
  children: React.ReactNode;
}) {
  // 错误 span 生成稳定 id（`${htmlFor}-err`），供调用方串到对应 Input 的 aria-describedby（H6）。
  const errorId = htmlFor ? `${htmlFor}-err` : undefined;
  return (
    <div className={clsx("flex flex-col gap-1.5", className)}>
      {label && (
        <label htmlFor={htmlFor} className="text-xs font-medium text-[var(--muted)]">
          {label}
        </label>
      )}
      {children}
      {error ? (
        <span id={errorId} className="text-[11px] text-[var(--short)]">{error}</span>
      ) : hint ? (
        <span className="text-[11px] leading-relaxed text-[var(--muted)]">{hint}</span>
      ) : null}
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

/** 可复用滑块原语（实盘下单仓位 / 杠杆用）：受控 range，键盘可达（原生 range 支持方向键 / Home / End），
 * 显式 aria-valuemin/max/now 供读屏朗读，配色用设计 token（accent-color 着色拇指与轨道）。 */
export function Slider({
  value,
  min = 0,
  max = 100,
  step = 1,
  onChange,
  ariaLabel,
  id,
  disabled,
  className,
}: {
  value: number;
  min?: number;
  max?: number;
  step?: number;
  onChange: (next: number) => void;
  ariaLabel?: string;
  id?: string;
  disabled?: boolean;
  className?: string;
}) {
  return (
    <input
      type="range"
      id={id}
      value={value}
      min={min}
      max={max}
      step={step}
      disabled={disabled}
      aria-label={ariaLabel}
      aria-valuemin={min}
      aria-valuemax={max}
      aria-valuenow={value}
      onChange={(e) => onChange(Number(e.target.value))}
      className={clsx(
        "h-1.5 w-full cursor-pointer appearance-none rounded-full bg-[var(--surface-2)]",
        "accent-[var(--accent)] disabled:cursor-not-allowed disabled:opacity-40",
        className,
      )}
    />
  );
}

// 可见且可聚焦的元素选择器（焦点陷阱用）。
const FOCUSABLE =
  'a[href], button:not([disabled]), textarea:not([disabled]), input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])';

/**
 * 可复用对话框原语（P1-Modal）：详情面板与未来任何浮层均复用，避免一次性孤岛。
 *
 * a11y / 行为契约（与 Modal.test.tsx 一致）：
 * - `role="dialog"` + `aria-modal="true"` + `aria-labelledby`（指向调用方渲染的标题 id）。
 * - React portal 到 `document.body`；半透明遮罩 + 居中卡片，配色用既有 CSS 变量、明/暗主题零适配。
 * - `Esc` 与**点击遮罩**触发 `onClose`；挂载时初始焦点入对话框；`Tab`/`Shift+Tab` 焦点陷阱于框内；
 *   卸载时焦点恢复到触发元素；打开期 `body` 滚动锁定。
 */
export function Modal({
  open,
  onClose,
  labelledBy,
  className,
  children,
}: {
  open: boolean;
  onClose: () => void;
  labelledBy?: string;
  className?: string;
  children: React.ReactNode;
}) {
  const dialogRef = useRef<HTMLDivElement>(null);
  const restoreFocusRef = useRef<HTMLElement | null>(null);

  // 挂载：记住触发元素 → 初始焦点入对话框 → body 滚动锁定；卸载：解锁并把焦点交还触发元素。
  useEffect(() => {
    if (!open) return;
    restoreFocusRef.current = (document.activeElement as HTMLElement) ?? null;
    const dialog = dialogRef.current;
    const first = dialog?.querySelector<HTMLElement>(FOCUSABLE);
    (first ?? dialog)?.focus();
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = prevOverflow;
      restoreFocusRef.current?.focus?.();
    };
  }, [open]);

  // Esc 关闭 + Tab/Shift+Tab 焦点陷阱（捕获期绑定，优先于面板级 Esc 流，避免双重关闭）。
  useEffect(() => {
    if (!open) return;
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        onClose();
        return;
      }
      if (e.key !== "Tab") return;
      const dialog = dialogRef.current;
      if (!dialog) return;
      const items = Array.from(dialog.querySelectorAll<HTMLElement>(FOCUSABLE));
      if (items.length === 0) {
        e.preventDefault();
        dialog.focus();
        return;
      }
      const first = items[0];
      const last = items[items.length - 1];
      const active = document.activeElement;
      if (e.shiftKey && (active === first || active === dialog)) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && active === last) {
        e.preventDefault();
        first.focus();
      }
    };
    window.addEventListener("keydown", onKeyDown, true);
    return () => window.removeEventListener("keydown", onKeyDown, true);
  }, [open, onClose]);

  if (!open) return null;

  return createPortal(
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={labelledBy}
        tabIndex={-1}
        className={clsx(
          "flex max-h-[85vh] w-full max-w-2xl flex-col overflow-hidden rounded-[14px]",
          "border border-[var(--border)] bg-[var(--bg)] shadow-soft outline-none",
          className,
        )}
      >
        {children}
      </div>
    </div>,
    document.body,
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
