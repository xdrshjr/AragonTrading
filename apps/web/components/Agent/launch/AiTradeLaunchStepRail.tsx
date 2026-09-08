"use client";

import { useRef } from "react";
import type {
  AiTradeLaunchStepDescriptor,
  AiTradeLaunchStepId,
} from "@/lib/aiTradeLaunchSteps";

/**
 * 引导式启动的**步骤轨**（ai-trade-guided-launch §4.3）。
 *
 * ── 只渲染一棵树（【v2 · P1-2】）────────────────────────────────────────
 * 响应式**只改同一棵树的 CSS**（容器 `flex-col md:flex-row`、轨 `w-full md:w-[200px]`、条目
 * `overflow-x-auto md:overflow-visible`），**绝不**写成 `hidden md:flex` 的轨 + `md:hidden` 的
 * 第二份进度条那种两棵树的形态：前端用例跑在 jsdom，媒体查询不求值，两棵树会同时存在于用例
 * DOM 里，导航用例的 `findByRole("tab", { name })` 当场抛 `found multiple elements`。
 *
 * ── 键位映射写在本文件内（【v2 · P1-6】）───────────────────────────────
 * `rovingTabIndex`（`components/ui/index.tsx`）是**模块私有**函数、没有 `export`，而且只认
 * `←/→`；纵向轨的键盘期望是 `↑/↓`。改那个共享函数去支持 `↑/↓` 会连带改变 11 个既有 `Tabs`
 * 调用方对这两个键的消费行为，明显超出本轮范围。故这里自带一个 6 行的纯函数。
 *
 * ── 零折行契约（Type A）────────────────────────────────────────────────
 * 行容器 `flex items-center gap-2 whitespace-nowrap` + 一处 Type A 锚点；序号徽标 `shrink-0`；
 * 标题与摘要**恰好一处** `min-w-0 truncate` + `title`。本文件是全轮**唯一**允许带这个锚点的新文件
 * （六个 `Step*.tsx` 一律不带），这样 `noWrapContract` 里那个等值冻结的数才可对账。
 * ⚠️ 上面这段说明刻意**不写出那个属性的字面量**：`noWrapContract` 的 G3 是按 `split()` 数出现
 * 次数的，注释里写一次就会把计数顶成 2（这条规则解释自己时反而违反自己，是本仓库的成文教训）。
 */

export type AiTradeLaunchStepState = "default" | "edited" | "warn" | "error";

export interface AiTradeLaunchStepView {
  id: AiTradeLaunchStepId;
  title: string;
  index: number; // 1-based，供「第 N 步 / 共 M 步」
  state: AiTradeLaunchStepState;
  /** 是否是当前步（只影响徽标形状与底色，不占 `data-state` 的取值）。 */
  current: boolean;
  summary: string;
  error: string | null;
}

/**
 * 状态点的**形状 + 文字**双通道（不靠颜色单独承载语义，仓库既有可达性纪律）。
 *
 * 五种形态：未访问＝空心序号 / 已访问无错＝勾 / 当前＝实心圆 / 有警示＝三角 / 有错＝叉。
 * 形状先于颜色：三角与叉在任何主题、任何色觉条件下都读得出来，而"红色的圆"读不出来。
 */
const STATE_GLYPH: Record<AiTradeLaunchStepState, string> = {
  default: "○",
  edited: "✓",
  warn: "△",
  error: "✕",
};

const STATE_LABEL: Record<AiTradeLaunchStepState, string> = {
  default: "未访问",
  edited: "已访问",
  warn: "有提示",
  error: "有错误",
};

/**
 * 轨内键位映射：认 `↑/↓/←/→` 与 `Home/End`，其余按键回 `null`（调用方据此不 `preventDefault`）。
 *
 * 纵向轨上 `←/→` 作为等价别名保留 —— 窄档折成横向条时同一份映射天然仍然正确。
 */
export function railKeyToIndex(key: string, current: number, count: number): number | null {
  if (key === "ArrowDown" || key === "ArrowRight") return (current + 1) % count;
  if (key === "ArrowUp" || key === "ArrowLeft") return (current - 1 + count) % count;
  if (key === "Home") return 0;
  if (key === "End") return count - 1;
  return null;
}

export default function AiTradeLaunchStepRail({
  steps,
  views,
  currentId,
  onSelect,
}: {
  steps: readonly AiTradeLaunchStepDescriptor[];
  views: readonly AiTradeLaunchStepView[];
  currentId: AiTradeLaunchStepId;
  onSelect: (id: AiTradeLaunchStepId) => void;
}) {
  const listRef = useRef<HTMLDivElement>(null);
  const currentIndex = Math.max(
    0,
    steps.findIndex((s) => s.id === currentId),
  );
  const total = steps.length;

  const onKeyDown = (e: React.KeyboardEvent<HTMLDivElement>) => {
    const next = railKeyToIndex(e.key, currentIndex, total);
    if (next == null) return;
    e.preventDefault();
    const target = steps[next];
    if (!target) return;
    onSelect(target.id);
    // 焦点跟随选中项（roving tabindex：轨内只有当前项可 Tab 到）。
    const items = listRef.current?.querySelectorAll<HTMLButtonElement>('[role="tab"]');
    items?.[next]?.focus();
  };

  return (
    <div className="flex w-full shrink-0 flex-col gap-2 border-b border-[var(--border)] pb-2 md:w-[200px] md:min-h-0 md:overflow-y-auto md:border-b-0 md:border-r md:pb-0 md:pr-3">
      <div className="flex items-center justify-between gap-2 px-0.5 text-[10px] text-[var(--muted)]">
        <span>
          第 {currentIndex + 1} 步 / 共 {total} 步
        </span>
        <span className="tabular">{Math.round(((currentIndex + 1) / total) * 100)}%</span>
      </div>
      <div
        role="progressbar"
        aria-valuenow={currentIndex + 1}
        aria-valuemin={1}
        aria-valuemax={total}
        aria-label="启动向导进度"
        className="h-0.5 w-full overflow-hidden rounded bg-[var(--surface-2)]"
      >
        <div
          className="h-full bg-[var(--accent)] transition-[width] duration-150"
          style={{ width: `${((currentIndex + 1) / total) * 100}%` }}
        />
      </div>

      <div
        ref={listRef}
        role="tablist"
        aria-orientation="vertical"
        aria-label="启动步骤"
        onKeyDown={onKeyDown}
        className="flex gap-1 overflow-x-auto md:flex-col md:overflow-visible"
      >
        {views.map((view, i) => {
          const active = view.id === currentId;
          return (
            <button
              key={view.id}
              type="button"
              role="tab"
              id={`ai-launch-tab-${view.id}`}
              aria-selected={active}
              aria-controls={`ai-launch-panel-${view.id}`}
              tabIndex={active ? 0 : -1}
              data-state={view.state}
              data-nowrap="row"
              onClick={() => onSelect(view.id)}
              title={view.error ?? view.summary}
              className={`flex min-w-0 shrink-0 items-center gap-2 whitespace-nowrap rounded-md border-l-2 px-2 py-1.5 text-left transition-colors md:shrink ${
                active
                  ? "border-l-[var(--accent)] bg-[var(--accent-soft)] text-[var(--accent-ink)]"
                  : "border-l-transparent text-[var(--text)] hover:bg-[var(--surface-2)]"
              }`}
            >
              <span
                aria-hidden
                className={`flex h-4 w-4 shrink-0 items-center justify-center rounded-full border text-[9px] leading-none ${
                  active
                    ? "border-[var(--accent)] bg-[var(--accent)] text-[var(--on-accent)]"
                    : "border-current"
                }`}
              >
                {view.state === "default" && !view.current ? i + 1 : STATE_GLYPH[view.state]}
              </span>
              <span className="flex min-w-0 flex-col">
                <span className="min-w-0 truncate text-[11px] font-medium">
                  {view.title}
                  <span className="sr-only">（{STATE_LABEL[view.state]}）</span>
                </span>
                <span className="hidden min-w-0 truncate text-[10px] text-[var(--muted)] md:block">
                  {view.summary}
                </span>
              </span>
            </button>
          );
        })}
      </div>
    </div>
  );
}
