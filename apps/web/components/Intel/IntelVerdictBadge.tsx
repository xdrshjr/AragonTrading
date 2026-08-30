"use client";

import { useState } from "react";
import clsx from "clsx";
import { absoluteTime, relativeTime } from "@/lib/intel";
import { verdictLabel, verdictTone } from "@/lib/intelClosedLoop";
import type { IntelItem } from "@/lib/intelTypes";

/**
 * 判定徽章（ai-trade-intel-closed-loop §3.7.1 右半）。
 *
 * 四态四色四文案；`verdict === ""`（尚未核查过）⇒ **整个徽章不渲染**，而不是渲染一个「未知」
 * ——一排「未知」徽章会把真正有判定的那几条淹掉。
 *
 * 点开徽章给两样东西：`verdictNote` 全文，以及**「我不同意」**。后者是本设计里唯一一处
 * 「自动逻辑压过用户显式意愿」的撤销出口（评审裁决 Q2 以它可用为前提）：点一次，注入立刻恢复，
 * 且该条目**永久**移出自动候选池，不会在下一轮被默默判回去（评审 P0-4）。
 *
 * `verdictNote` 是模型写的自由文本：这里**只以纯文本渲染**（React 默认转义），源码扫描门禁
 * `intelNoDangerousHtml.source.test.ts` 另有一道。
 */
export default function IntelVerdictBadge({
  item,
  onClearVerdict,
  onVerifyNow,
}: {
  item: IntelItem;
  onClearVerdict: (item: IntelItem) => void;
  onVerifyNow?: (item: IntelItem) => void;
}) {
  const [open, setOpen] = useState(false);
  const label = verdictLabel(item.verdict);
  if (!label) return null;
  const tone = verdictTone(item.verdict);
  const when = item.verdictTs ? relativeTime(item.verdictTs, Date.now()) : "";

  return (
    <span className="relative shrink-0">
      <button
        type="button"
        data-testid="intel-verdict-badge"
        aria-label={`${label}${when ? `，${when}` : ""}：查看核查理由`}
        aria-expanded={open}
        title={item.verdictNote || label}
        onClick={() => setOpen((v) => !v)}
        className={clsx(
          "rounded-full border px-1.5 py-px text-[10px] outline-none transition-colors",
          "focus-visible:ring-2 focus-visible:ring-[var(--accent)]",
        )}
        style={{ borderColor: tone, color: tone }}
      >
        {label}
        {when ? ` · ${when}` : ""}
      </button>
      {open && (
        <span
          data-testid="intel-verdict-popover"
          className="absolute right-0 top-full z-10 mt-1 flex w-64 flex-col gap-1.5 rounded-md border border-[var(--border)] bg-[var(--surface)] p-2 shadow-soft"
        >
          <span className="text-[10px] leading-relaxed text-[var(--text)]">
            {item.verdictNote || "核查员没有给出理由。"}
          </span>
          {item.verdictTs > 0 && (
            <span className="text-[10px] text-[var(--muted)]">
              判定于 {absoluteTime(item.verdictTs)}（已核查 {item.verifyCount} 次）
            </span>
          )}
          <span className="flex items-center gap-1.5">
            <button
              type="button"
              onClick={() => {
                setOpen(false);
                onClearVerdict(item);
              }}
              className="rounded px-1.5 py-0.5 text-[10px] text-[var(--accent-ink)] outline-none hover:bg-[var(--accent-soft)] focus-visible:ring-2 focus-visible:ring-[var(--accent)]"
            >
              {item.verdict === "refuted" ? "我不同意，恢复注入" : "我不同意"}
            </button>
            {onVerifyNow && (
              <button
                type="button"
                onClick={() => {
                  setOpen(false);
                  onVerifyNow(item);
                }}
                className="rounded px-1.5 py-0.5 text-[10px] text-[var(--muted)] outline-none hover:bg-[var(--surface-2)] hover:text-[var(--text)] focus-visible:ring-2 focus-visible:ring-[var(--accent)]"
              >
                重新核查这一条
              </button>
            )}
          </span>
        </span>
      )}
    </span>
  );
}
