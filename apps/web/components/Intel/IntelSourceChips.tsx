"use client";

import { isSafeUrl } from "@/lib/intel";
import type { IntelSource } from "@/lib/intelTypes";

/**
 * 来源域名 chip + 外链（ai-trade-market-intel §3.8.3）。
 *
 * 四条纪律：
 * - 只显示**可注册域名**而不是完整 URL：一条情报可能有六个来源，六行长 URL 会把卡片撑爆，而
 *   用户在这一步真正要判断的是「这是谁说的」。
 * - `target="_blank"` 必须配 `rel="noopener noreferrer"`；桌面端另有 `setWindowOpenHandler`
 *   把它交给系统浏览器（否则打包后会开出一个无外壳的 Electron 窗口）。
 * - **非 http(s) 的 URL 不渲染成链接**。后端入库时已拒过一次，这里是纵深防御——一条能点的
 *   `javascript:` 链接只要出现一次就足够了。
 * - **「不再采纳此源」的 `✕` 必须是 `<a>` 的兄弟节点，不是子节点**（closed-loop 评审 P2-3）：
 *   chip 本体是一个 `<a target="_blank">`，把 `<button>` 塞进去就是「可交互元素套可交互元素」，
 *   与 `AiTradeIntelButton` 顶着的那条 `aiTradePanelNoNestedButton` 纪律同形。布局由外面新包的
 *   一层 `group/chip` 容器承担。
 *   键盘可达性同样照抄那颗按钮：**Tab 聚焦即可见**——只在 hover 时出现的破坏性操作，对键盘
 *   用户等于不存在。
 */
export default function IntelSourceChips({
  sources,
  onBlock,
}: {
  sources: IntelSource[];
  /** 缺省 ⇒ 不渲染 `✕`（编辑器预览等只读场景）。 */
  onBlock?: (domain: string) => void;
}) {
  if (!sources.length) return null;
  return (
    <span className="flex flex-wrap items-center gap-1.5" data-testid="intel-source-chips">
      <span className="text-[10px] text-[var(--muted)]">来源</span>
      {sources.map((source, index) => {
        const label = source.domain || source.publisher || "未知来源";
        const key = `${source.url}-${index}`;
        const chip = !isSafeUrl(source.url) ? (
          <span
            title="该来源链接协议不受支持，已停用跳转"
            className="rounded border border-[var(--border)] px-1 py-px text-[10px] text-[var(--muted)]"
          >
            {label}
          </span>
        ) : (
          <a
            href={source.url}
            target="_blank"
            rel="noopener noreferrer"
            title={source.title || source.url}
            className="rounded border border-[var(--border)] px-1 py-px text-[10px] text-[var(--accent-ink)] hover:bg-[var(--accent-soft)]"
          >
            {label} ↗
          </a>
        );
        return (
          <span key={key} className="group/chip inline-flex items-center gap-0.5">
            {chip}
            {onBlock && source.domain && (
              <button
                type="button"
                aria-label={`不再采纳 ${source.domain}`}
                title={`不再采纳 ${source.domain}`}
                onClick={() => onBlock(source.domain)}
                className="rounded px-0.5 text-[10px] text-[var(--muted)] opacity-0 outline-none transition-opacity duration-150 ease-smooth hover:text-[var(--short-ink)] focus-visible:opacity-100 focus-visible:ring-2 focus-visible:ring-[var(--accent)] group-hover/chip:opacity-100 motion-reduce:transition-none [@media(hover:none)]:opacity-100"
              >
                ✕
              </button>
            )}
          </span>
        );
      })}
    </span>
  );
}
