"use client";

import {
  BINDING_MODE_LABEL,
  intelBindingText,
  intelLastRoundText,
} from "@/lib/intel";
import type { AiTradeIntelSummary } from "@/lib/intelTypes";

/**
 * 情报弹窗顶部的「此刻在不在喂给 Agent」绑定条（ai-trade-intel-session-binding 轴 C-3）。
 *
 * 界面上的渐进式披露与 prompt 里的渐进式披露是同一条原则的两端：**一眼可辨的状态 →
 * 悬停可得的解释 → 点开可查的明细**。这里是第一层与第二层：一行结论 + 一个默认收起的
 * `<details>`。
 *
 * 两条刻意的设计：
 *
 * 1. **未采信时不给一个此刻点不动的开关。** 会话中途改变证据面会让同一次会话的前后两半不可比
 *    ——本仓库对「运行中不可变契约」的既有处置就是如此。这里给的是一句「下次启动时可以在弹窗
 *    里勾选」，而不是一个假按钮。
 * 2. **未绑定 ⇒ 整条不渲染。** 对一个没在跑自动交易的标的说「未采信情报」是噪音；未装配 /
 *    运维关的解释走既有 `degrade` 卡片的入口，不新增第二条路径。
 */
export default function IntelBindingStrip({
  summary,
}: {
  summary: AiTradeIntelSummary | null;
}) {
  const binding = summary?.binding ?? null;
  if (!binding?.bound) return null;

  const headline = intelBindingText(summary);
  const lastRound = binding.injecting ? intelLastRoundText(binding) : "";
  const modeLabel = BINDING_MODE_LABEL[binding.mode] ?? binding.mode;

  return (
    <section
      data-testid="intel-binding-strip"
      className="flex flex-col gap-1 rounded-md border border-[var(--border)] bg-[var(--surface-2)] px-3 py-2"
    >
      <div className="flex items-start justify-between gap-2">
        <p className="flex items-center gap-1.5 text-[11px] leading-relaxed text-[var(--text)]">
          <span
            aria-hidden="true"
            className={
              binding.injecting
                ? "mt-[3px] h-1.5 w-1.5 shrink-0 rounded-full bg-[var(--accent)]"
                : "mt-[3px] h-1.5 w-1.5 shrink-0 rounded-full border border-[var(--muted)]"
            }
          />
          <span data-testid="intel-binding-headline">
            {headline}
            {lastRound && <span className="text-[var(--muted)]">{` · ${lastRound}`}</span>}
          </span>
        </p>
        {binding.injecting && (
          <details className="shrink-0 text-[10px] text-[var(--muted)]">
            <summary className="cursor-pointer list-none text-[var(--accent-ink)] underline-offset-2 hover:underline">
              什么是{modeLabel}？
            </summary>
            <div className="mt-1 max-w-[22rem] leading-relaxed">
              {binding.mode === "index" ? (
                <>
                  <p>索引档：每一轮只把情报的标题与性质（类别、立场、影响、来源数、置信度、时效）
                    交给 Agent，不含摘要正文。</p>
                  <p>它真正要据某一条下单时，下单前的复核环节会自动展开那几条的完整摘要。</p>
                </>
              ) : (
                <>
                  <p>全文档：每一轮都把完整摘要交给 Agent，上下文开销更大。</p>
                  <p>同一根 K 线内的重复唤醒只列新增情报，重复的那些会被压成一行说明。</p>
                </>
              )}
            </div>
          </details>
        )}
      </div>
      {!binding.injecting && binding.reason === "session_off" && (
        <p className="text-[10px] leading-relaxed text-[var(--muted)]"
           data-testid="intel-binding-session-off">
          情报仍在正常采集，下次启动 AI 自动交易时可以在弹窗里勾选「让 Agent 参考市场情报」。
        </p>
      )}
    </section>
  );
}
