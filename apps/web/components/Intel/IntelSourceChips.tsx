"use client";

import { isSafeUrl } from "@/lib/intel";
import type { IntelSource } from "@/lib/intelTypes";

/**
 * 来源域名 chip + 外链（ai-trade-market-intel §3.8.3）。
 *
 * 三条纪律：
 * - 只显示**可注册域名**而不是完整 URL：一条情报可能有六个来源，六行长 URL 会把卡片撑爆，而
 *   用户在这一步真正要判断的是「这是谁说的」。
 * - `target="_blank"` 必须配 `rel="noopener noreferrer"`；桌面端另有 `setWindowOpenHandler`
 *   把它交给系统浏览器（否则打包后会开出一个无外壳的 Electron 窗口）。
 * - **非 http(s) 的 URL 不渲染成链接**。后端入库时已拒过一次，这里是纵深防御——一条能点的
 *   `javascript:` 链接只要出现一次就足够了。
 */
export default function IntelSourceChips({ sources }: { sources: IntelSource[] }) {
  if (!sources.length) return null;
  return (
    <span className="flex flex-wrap items-center gap-1.5" data-testid="intel-source-chips">
      <span className="text-[10px] text-[var(--muted)]">来源</span>
      {sources.map((source, index) => {
        const label = source.domain || source.publisher || "未知来源";
        const key = `${source.url}-${index}`;
        if (!isSafeUrl(source.url)) {
          return (
            <span
              key={key}
              title="该来源链接协议不受支持，已停用跳转"
              className="rounded border border-[var(--border)] px-1 py-px text-[10px] text-[var(--muted)]"
            >
              {label}
            </span>
          );
        }
        return (
          <a
            key={key}
            href={source.url}
            target="_blank"
            rel="noopener noreferrer"
            title={source.title || source.url}
            className="rounded border border-[var(--border)] px-1 py-px text-[10px] text-[var(--accent-ink)] hover:bg-[var(--accent-soft)]"
          >
            {label} ↗
          </a>
        );
      })}
    </span>
  );
}
