"use client";

import clsx from "clsx";
import React, { useEffect, useRef } from "react";
import { createPortal } from "react-dom";

/** 复用基础组件 (spec §8.2)：Button / Select / Card / Tabs / Skeleton / Badge / Modal。 */

export interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: "default" | "primary" | "ghost" | "danger";
  size?: "sm" | "md";
}

/**
 * 基础按钮。**转 `forwardRef` 是纯加法**（spec D6）：既有几十个调用方一行不改，
 * 不传 `ref` 时行为逐字相同；而 `ConfirmDialog` 需要把初始焦点显式钉在「取消」上，
 * 普通函数组件在 React 18.3 下拿不到 DOM 节点（`ref` 不是它的合法 prop，`tsc` 直接红）。
 */
export const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { variant = "default", size = "md", className, ...props },
  ref,
) {
  return (
    <button
      ref={ref}
      className={clsx(
        "inline-flex items-center justify-center gap-1.5 rounded-[10px] font-medium transition-all duration-150 ease-smooth disabled:opacity-40 disabled:cursor-not-allowed",
        // H-F8：键盘聚焦可见环（仅 focus-visible，鼠标点击不显，不改既有视觉）。
        "outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)] focus-visible:ring-offset-1 focus-visible:ring-offset-[var(--bg)]",
        size === "sm" ? "px-2.5 py-1 text-xs" : "px-3.5 py-2 text-sm",
        // 白字 on --accent 实测浅 3.28 / 暗 2.69（暗色连大字号 3:1 都不到），而这是全应用的主行动按钮。
        variant === "primary" &&
          "bg-[var(--accent)] text-[var(--on-accent)] hover:brightness-105 active:brightness-95 shadow-soft",
        variant === "default" &&
          "bg-[var(--surface-2)] text-[var(--text)] hover:bg-[var(--border)]",
        variant === "ghost" && "bg-transparent text-[var(--muted)] hover:text-[var(--text)] hover:bg-[var(--surface-2)]",
        // hover 底从 --accent-soft 改为 --surface-2：暗色 --short-ink on --accent-soft 只有 4.41，
        // 而「删除 / 平掉全部 / 停止会话」正是最需要看清的那颗按钮；顺带修掉「破坏性按钮的 hover 底
        // 是品牌赭色」这处语义错配（写法与上面 variant="ghost" 一致，不是新发明）。
        variant === "danger" && "bg-transparent text-[var(--short-ink)] hover:bg-[var(--surface-2)]",
        className,
      )}
      {...props}
    />
  );
});

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
        <span id={errorId} className="text-[11px] text-[var(--short-ink)]">{error}</span>
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
        tone === "long" && "bg-[color-mix(in_srgb,var(--long)_16%,transparent)] text-[var(--long-ink)]",
        tone === "short" && "bg-[color-mix(in_srgb,var(--short)_16%,transparent)] text-[var(--short-ink)]",
        tone === "accent" && "bg-[var(--accent-soft)] text-[var(--accent-ink)]",
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
        // range-slider：自绘拇指几何 + 两端内缩，避免 value=0/100 拇指溢出（globals.css）。
        "range-slider h-1.5 w-full cursor-pointer appearance-none rounded-full bg-[var(--surface-2)]",
        "accent-[var(--accent)] disabled:cursor-not-allowed disabled:opacity-40",
        className,
      )}
    />
  );
}

// 可见且可聚焦的元素选择器（焦点陷阱用）。
const FOCUSABLE =
  'a[href], button:not([disabled]), textarea:not([disabled]), input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])';

function focusableItems(dialog: HTMLElement): HTMLElement[] {
  return Array.from(dialog.querySelectorAll<HTMLElement>(FOCUSABLE))
    .filter((element) => element.tabIndex >= 0);
}

/**
 * 同时打开的对话框登记表（spec D4）；「栈顶」= 唯一有权响应 Esc / Tab 陷阱的那一个。
 *
 * **为什么不能只按挂载顺序判栈顶**：React 的 effect 是**自底向上**执行的（子先于父）。
 * 外层 `Modal` 与内层 `ConfirmDialog` 同时挂载时（`SymbolGroupModal.tsx:177` 就是这个形状，
 * 打开时若确认框已是 open 状态），内层的 effect 先跑、先入栈，外层反而后入——按纯挂载顺序判定
 * 会得出「外层在顶」，缺陷原样保留。所以真正的判据是**React 树上的嵌套深度**（context 穿透
 * portal，按渲染位置传递），挂载顺序只在同深度（两个互不嵌套的兄弟弹窗）时用作后入者优先的次序。
 *
 * 元素里的 `id` 是 `Symbol`，只用于身份比较，不持有组件引用，因此不会造成泄漏。
 */
type DialogEntry = { id: symbol; depth: number };
const dialogStack: DialogEntry[] = [];

/** 嵌套深度：最外层 `Modal` 的 children 拿到 1，它内部再开一层拿到 2，依此类推。 */
const ModalDepthContext = React.createContext(0);

/** 该对话框是否是当前的栈顶（深度最大者优先；同深度取后入者）。 */
function isTopDialog(id: symbol): boolean {
  let top: DialogEntry | undefined;
  for (const entry of dialogStack) {
    if (top === undefined || entry.depth >= top.depth) top = entry;
  }
  return top?.id === id;
}

/**
 * 可复用对话框原语（P1-Modal）：详情面板与未来任何浮层均复用，避免一次性孤岛。
 *
 * a11y / 行为契约（与 Modal.test.tsx 一致）：
 * - `role="dialog"` + `aria-modal="true"` + `aria-labelledby`（指向调用方渲染的标题 id）。
 * - React portal 到 `document.body`；半透明遮罩 + 居中卡片，配色用既有 CSS 变量、明/暗主题零适配。
 * - `Esc` 与**点击遮罩**触发 `onClose`；挂载时初始焦点入对话框；`Tab`/`Shift+Tab` 焦点陷阱于框内；
 *   卸载时焦点恢复到触发元素；打开期 `body` 滚动锁定。
 * - `initialFocusRef`（可选，spec D6）：显式指定初始焦点落点。**不传即旧语义**（DOM 中第一个
 *   可聚焦元素），11 个既有调用方零改动。破坏性确认框须用它把焦点钉在安全选项上。
 */
export function Modal({
  open,
  onClose,
  labelledBy,
  className,
  initialFocusRef,
  onEscapeKeyDown,
  children,
}: {
  open: boolean;
  onClose: () => void;
  labelledBy?: string;
  className?: string;
  initialFocusRef?: React.RefObject<HTMLElement | null>;
  onEscapeKeyDown?: (event: KeyboardEvent) => void;
  children: React.ReactNode;
}) {
  const dialogRef = useRef<HTMLDivElement>(null);
  const restoreFocusRef = useRef<HTMLElement | null>(null);
  // 嵌套深度在挂载时一次定型：一个 `Modal` 在 React 树上的位置不会在生命周期内变化，
  // 用 `useRef` 的惰性初始化捕获它，就能让下面的入栈 effect 依赖数组保持为**纯 `[open]`**。
  const depth = React.useContext(ModalDepthContext) + 1;
  const entryRef = useRef<DialogEntry>();
  if (entryRef.current === undefined) entryRef.current = { id: Symbol("modal"), depth };

  // 对话框栈（spec D4）：`window` 上的捕获期 keydown 是**每个 Modal 各挂一份**，而
  // `e.stopPropagation()` 按 DOM 规范只阻止事件传到**其它节点**，不阻止**同一节点上的其它监听器**。
  // 于是两个 Modal 同开时，先挂载的外层先执行，Esc 会先把外层关掉——用户想「退出这个删除确认」，
  // 结果整个标的组管理器一起消失、工作上下文丢失（§2.1(c) 已实测复现）。
  //
  // ⚠️ 这个 effect 的依赖数组**只能是 `[open]`**，绝不可并进下面那个 `[open, onClose]` 的 keydown
  // effect。调用方普遍传内联箭头（如 `FactorLab.tsx:301` 的 `onClose={() => setGroupModalOpen(false)}`
  // 经 `SymbolGroupModal.tsx:124` 原样交给 `<Modal onClose={onClose}>`），父组件每次重渲染 `onClose`
  // 都换新身份 ⇒ 挂在那个 effect 上就会「splice 掉自己 → 重新 push 到**栈顶**」，缺陷原样复活，
  // 而静态渲染的用例照样是绿的。`open` 是布尔，身份天然稳定。
  useEffect(() => {
    if (!open) return;
    const entry = entryRef.current!;
    dialogStack.push(entry);
    return () => {
      const at = dialogStack.indexOf(entry);   // 按值移除，不用 pop：卸载顺序未必是入栈顺序
      if (at >= 0) dialogStack.splice(at, 1);
    };
  }, [open]);

  // 挂载：记住触发元素 → 初始焦点入对话框 → body 滚动锁定；卸载：解锁并把焦点交还触发元素。
  useEffect(() => {
    if (!open) return;
    restoreFocusRef.current = (document.activeElement as HTMLElement) ?? null;
    const dialog = dialogRef.current;
    const first = dialog ? focusableItems(dialog)[0] : undefined;
    (initialFocusRef?.current ?? first ?? dialog)?.focus();
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = prevOverflow;
      restoreFocusRef.current?.focus?.();
    };
    // 依赖仍只列 `open`：`initialFocusRef` 是 ref 对象，身份稳定，但把它写进依赖数组会让每个
    // 传内联 `useRef` 以外形态的调用方引发 effect 反复重跑——本仓已为「依赖不稳」复发过一次缺陷。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  // Esc 关闭 + Tab/Shift+Tab 焦点陷阱（捕获期绑定，优先于面板级 Esc 流，避免双重关闭）。
  useEffect(() => {
    if (!open) return;
    const onKeyDown = (e: KeyboardEvent) => {
      // 只有**栈顶**对话框响应 Esc 与 Tab 陷阱。Tab 一并守住是因为：外层的焦点陷阱在内层打开时
      // 仍在跑，目前只是恰好每个分支都不命中——任何一次 FOCUSABLE 选择器调整都会让它开始抢焦点。
      if (!isTopDialog(entryRef.current!.id)) return;
      if (e.key === "Escape") {
        onEscapeKeyDown?.(e);
        if (e.defaultPrevented) return;
        e.stopPropagation();
        // stopPropagation 挡不住**同一个 EventTarget 上的其它监听器**（那需要 stopImmediatePropagation）。
        // 栈顶守卫已经解决了 Modal 之间的相互干扰，这一行是纵深防御：挡住同样挂在 window 捕获期的
        // 非 Modal 监听器（面板级 Esc 流）在同一次按键里再关一层。
        e.stopImmediatePropagation();
        onClose();
        return;
      }
      if (e.key !== "Tab") return;
      const dialog = dialogRef.current;
      if (!dialog) return;
      const items = focusableItems(dialog);
      if (items.length === 0) {
        e.preventDefault();
        dialog.focus();
        return;
      }
      const first = items[0];
      const last = items[items.length - 1];
      const active = document.activeElement;
      if (!dialog.contains(active)) {
        e.preventDefault();
        (e.shiftKey ? last : first).focus();
        return;
      }
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
  }, [open, onClose, onEscapeKeyDown]);

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
        {/* 在这里下发深度：children 是在本组件的子树里渲染的，因此**内部**再开的 Modal
            （典型是 ConfirmDialog）读到的是 depth+1，与 DOM 上二者互为兄弟无关。 */}
        <ModalDepthContext.Provider value={depth}>{children}</ModalDepthContext.Provider>
      </div>
    </div>,
    document.body,
  );
}

/**
 * 共享页签原语（spec D8）。**11 个调用方零改动、视觉零变化**，补的全是语义与键盘行为：
 * 外层 `role="tablist"`、每项 `role="tab"` + `aria-selected` + roving tabindex，
 * ←/→ 环形移动、Home/End 跳首尾（手感照抄 `Strategy/MultiSampleResults.tsx` 的手写实现）。
 *
 * 此前它渲染的是一排裸 `<button>`：读屏用户听不到「第 2 项，共 4 项，已选中」，键盘用户要按 Tab
 * 逐个穿过。而它覆盖的是 RightPanel 主页签、ChartHeaderBar 视图段控等**几乎整个应用的主导航**——
 * 同仓库里三处手写页签反倒都做对了，共享原语是这套代码里 a11y 最差的那一个。
 *
 * **刻意不引入 `aria-controls`**：多数调用方不渲染独立 tabpanel，一个指向不存在 id 的
 * `aria-controls` 比没有更糟。
 */
export function Tabs({
  tabs,
  active,
  onChange,
  variant = "segmented",
  label,
}: {
  tabs: { key: string; label: string }[];
  active: string;
  onChange: (key: string) => void;
  variant?: "segmented" | "underline";
  /** 供多组页签共存时区分（→ `aria-label`）；不传即无此属性，行为不变。 */
  label?: string;
}) {
  const listRef = useRef<HTMLDivElement>(null);

  const onKeyDown = (e: React.KeyboardEvent<HTMLDivElement>) => {
    const nextIdx = rovingTabIndex(e.key, tabs.findIndex((t) => t.key === active), tabs.length);
    if (nextIdx == null) return;
    e.preventDefault();
    onChange(tabs[nextIdx].key);
    listRef.current?.querySelectorAll<HTMLButtonElement>('[role="tab"]')[nextIdx]?.focus();
  };

  // `key` 刻意不放进这里：React 18.3 起把 key 混在 spread 里会 warning（且它本就不是 DOM prop）。
  const tabProps = (t: { key: string; label: string }) => ({
    type: "button" as const,
    role: "tab",
    "aria-selected": active === t.key,
    tabIndex: active === t.key ? 0 : -1,
    onClick: () => onChange(t.key),
  });

  // underline 变体：靠左、内容宽度、下划线高亮（副图页签 + 下区数据页签共用）。
  // 评审 R-2：不加 overflow-x-auto（会令 overflow-y 计算为 auto 而裁掉 -mb-px 下划线）；
  // 两处调用方页签数少，单行不溢出。如未来需横向滚动，把溢出下沉到不承载底边线的内层 wrapper。
  if (variant === "underline") {
    return (
      <div
        ref={listRef}
        role="tablist"
        aria-label={label}
        onKeyDown={onKeyDown}
        className="flex items-center gap-1 border-b border-[var(--border)]"
      >
        {tabs.map((t) => (
          <button
            key={t.key}
            {...tabProps(t)}
            className={clsx(
              "-mb-px shrink-0 whitespace-nowrap border-b-2 px-3 py-1.5 text-xs font-medium transition-colors duration-150",
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
  // —— 既有 segmented 实现，视觉原样保留（默认变体，不传 variant 的调用方零变化）——
  return (
    <div
      ref={listRef}
      role="tablist"
      aria-label={label}
      onKeyDown={onKeyDown}
      className="flex gap-1 rounded-[10px] bg-[var(--surface-2)] p-1"
    >
      {tabs.map((t) => (
        <button
          key={t.key}
          {...tabProps(t)}
          className={clsx(
            "flex-1 whitespace-nowrap rounded-[7px] px-3 py-1.5 text-xs font-medium transition-all duration-150",
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

/**
 * 方向键 → 目标页签下标（纯函数，便于用例直接冻结环绕语义）。
 *
 * @param key `KeyboardEvent.key`。
 * @param current 当前激活项下标（-1 表示 active 不在列表里）。
 * @param count 页签总数。
 * @returns 目标下标；不是导航键或列表为空则返回 `null`（调用方即不 preventDefault）。
 *   导航键即便落回当前项（在首项按 Home）也返回该下标：那一下仍然是「页签自己消费掉的按键」，
 *   放行给页面滚动才是意外行为。
 */
function rovingTabIndex(key: string, current: number, count: number): number | null {
  if (count === 0) return null;
  const base = current < 0 ? 0 : current;
  switch (key) {
    case "ArrowRight":
      return (base + 1) % count;
    case "ArrowLeft":
      return (base - 1 + count) % count;
    case "Home":
      return 0;
    case "End":
      return count - 1;
    default:
      return null;
  }
}
