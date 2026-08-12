"use client";

import { describeSource, describeTradability, formatRate } from "@/lib/aiTradeTradability";
import type { AiTradeTradabilitySnapshot } from "@/lib/aiTradeTypes";

/**
 * 运行期可交易性卡（ai-trade-tradability-and-open-funnel 轴 A · §3.1.3 落点三）。
 *
 * 显示的是**本会话启动时的断面**，启动后不变——用户回看时想问的是「我当时是在什么条件下开的这
 * 一局」，而不是「现在的行情如何」；而且它是一个窗口统计量，分钟级重算只会制造抖动。
 *
 * 与「开仓漏斗」卡并排时**必须写清口径差**：本卡的 `否决率` 分母是窗口内**全部** K 线断面
 * （条件概率：「若在这一根上想开仓，会不会被否」），而漏斗的 `blocked.edgeGate / intents` 分母是
 * **模型真的提出过的开仓意图数**。两个数会不一样，而且都对——绝不能互相替代。
 */
export default function TradabilityCard({
  tradability,
}: {
  tradability: AiTradeTradabilitySnapshot | null | undefined;
}) {
  const view = describeTradability(tradability);
  // 未探测 / 老会话 ⇒ 整卡不渲染（渲染门控：绝不在没有数据的会话上挂一排「—」）。
  if (!view || !tradability) return null;
  const source = describeSource(tradability.source, tradability.asOfTs);
  return (
    <div
      className="flex flex-col gap-1 rounded-md border border-[var(--border)] bg-[var(--surface)] px-3 py-2"
      data-testid="ai-tradability-card"
    >
      <div className="flex items-baseline justify-between gap-2">
        <span className="text-xs text-[var(--muted)]">
          可交易性预检{tradability.bar ? ` · ${tradability.bar}` : ""}
        </span>
        <span className="tabular text-[10px]" style={{ color: toneVar(view.tone) }}>
          ● {view.label}
          {view.vetoText ? ` · 预计否决 ${view.vetoText}` : ""}
        </span>
      </div>
      {view.headline && (
        <p
          className="text-[10px] leading-relaxed text-[var(--muted)]"
          data-testid="ai-tradability-card-headline"
        >
          {view.headline}
        </p>
      )}
      {view.advice && (
        <p
          className="rounded border px-2 py-1 text-[10px] leading-relaxed"
          style={{ color: toneVar(view.tone), borderColor: toneVar(view.tone) }}
          data-testid="ai-tradability-card-advice"
        >
          {view.advice}
        </p>
      )}
      <p className="text-[10px] leading-relaxed text-[var(--muted)]">
        这是<strong className="text-[var(--text)]">本会话启动时</strong>的断面
        {tradability.samples ? `（最近 ${tradability.samples} 根 K 线）` : ""}
        {source ? `，${source}` : ""}。 否决率的分母是窗口内
        <strong className="text-[var(--text)]">全部 K 线断面</strong>
        （「若在这一根上想开仓会不会被否」），与「开仓漏斗」卡里
        <strong className="text-[var(--text)]">模型真的提出过的开仓意图</strong>
        不是同一个分母，两个数都对但不可互相替代。
      </p>
    </div>
  );
}

/** 供其它卡片复用的一句摘要（例如面板顶部的红条）。 */
export function tradabilitySummary(
  tradability: AiTradeTradabilitySnapshot | null | undefined,
): string | null {
  const view = describeTradability(tradability);
  if (!view || view.level !== "infeasible") return null;
  const rate = formatRate(tradability?.vetoRateMarket ?? null);
  return rate ? `当前周期上约 ${rate} 的开仓意图会被最小 edge 门禁否决` : view.headline || null;
}

function toneVar(tone: string): string {
  if (tone === "long") return "var(--long)";
  if (tone === "short") return "var(--short)";
  if (tone === "accent") return "var(--accent)";
  return "var(--muted)";
}
