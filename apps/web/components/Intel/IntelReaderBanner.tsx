"use client";

import { useState } from "react";
import { describeReaderHealth } from "@/lib/intelEvidence";
import type { IntelDocumentHealth } from "@/lib/intelTypes";

/**
 * 情报正文抓取的零命中横幅（ai-trade-silent-degradation-repair 轴 B-2）。
 *
 * 它要消灭的形态与第 1 轮的活性看门狗同源：**一个功能完整、配置正确、每 20 分钟准时跑一轮、
 * 持续消耗 token 的子系统，产出率恒为零，而它自己不知道、用户也不知道。** 真机实测 17 轮采集、
 * `reader_ok = 0`、`ai_intel_documents` 30 行全部 `blocked`，40 分钟又烧掉 4,083 输入 token
 * 而采纳 0 条。
 *
 * 三条呈现纪律（与 `AiTradeLivenessBanner` 逐字同源）：
 *
 * 1. **只在连续失败达到阈值时出现。** 一轮抓不到是常态（新闻站限流、付费墙），把它做成常驻
 *    横幅只会在两天内变成背景噪声；真正值钱的是「连续 N 轮一篇都没有」这个跨轮结论。
 * 2. **读数与成因分开说。** 第一行是事实（`0/96`），第二行才是猜测（「常见成因：…」）——
 *    一句被当成结论的猜测，比不说话更贵。
 * 3. **被拦域名抽屉默认收起。** 它是排查用的证据，不是第一屏该占的视觉预算；展开后逐条列出
 *    `status != 'ok'` 的分布与本次运行以来的拒因，并**如实标注哪一份会随重启归零**。
 *
 * 老后端（两个键缺席）/ 总闸关 ⇒ `health` 为 `null` 或读数缺席 ⇒ 整段不渲染。
 */
export default function IntelReaderBanner({
  health,
  streakWarn = 3,
}: {
  health: IntelDocumentHealth | null;
  /** 后端 `ai_trade_intel_reader_zero_streak_warn` 的生效值（默认 3）。 */
  streakWarn?: number;
}) {
  const [open, setOpen] = useState(false);
  const view = describeReaderHealth(health);
  const streak = health?.zeroOkStreak ?? 0;
  if (!view || view.tone !== "danger" || streak < Math.max(1, streakWarn)) return null;

  const byStatus = Object.entries(health?.blockedByStatus ?? {});
  const byReject = Object.entries(health?.rejectCodesSinceStart ?? {});

  return (
    <div
      role="alert"
      data-testid="intel-reader-banner"
      className="flex flex-col gap-1 rounded border border-[var(--accent)] bg-[var(--accent-soft)] px-2 py-1.5 text-[11px] leading-relaxed"
    >
      <span className="font-medium text-[var(--accent-ink)]">
        情报正文已连续 {streak} 轮一篇都没抓到
      </span>
      <span className="text-[var(--text)]">{view.text}</span>
      <span className="text-[var(--muted)]">{view.hint}</span>
      {(byStatus.length > 0 || byReject.length > 0) && (
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          aria-expanded={open}
          className="self-start rounded border border-[var(--border)] px-1.5 py-0.5 text-[10px] text-[var(--muted)] transition-colors hover:bg-[var(--surface-2)]"
        >
          {open ? "收起被拦明细" : "查看被拦明细"}
        </button>
      )}
      {open && (
        <div className="flex flex-col gap-0.5 text-[10px] text-[var(--muted)]">
          {byStatus.length > 0 && (
            <span data-testid="intel-reader-blocked-status">
              缓存里的文档状态：
              {byStatus.map(([code, n]) => `${code} ${n}`).join(" · ")}
            </span>
          )}
          {byReject.length > 0 && (
            <span data-testid="intel-reader-reject-codes">
              本次运行以来的拒因：
              {byReject.map(([code, n]) => `${code} ${n}`).join(" · ")}
              {health?.sinceProcessStart ? "（重启后归零）" : ""}
            </span>
          )}
        </div>
      )}
    </div>
  );
}
