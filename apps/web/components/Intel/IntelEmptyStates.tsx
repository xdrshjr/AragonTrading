"use client";

import { Button } from "@/components/ui";
import { degradeText, nextDueText } from "@/lib/intel";
import type { AiTradeIntelSummary, IntelDegrade } from "@/lib/intelTypes";

/**
 * 五种空态 + 降级 banner（ai-trade-market-intel §3.8.3；第五种是「已归档」页签的，v2 补）。
 *
 * **任何一种都不显示「暂无情报」这四个字。** 一个假装没有新闻的情报面板，比一个明说「我这两
 * 小时没拿到任何东西」的面板危险得多——用户会把前者读成「市场很平静」，并据此下注。
 *
 * 文案取后端下发的 `message`，**前端不二次编**：两处各写一份必漂移，而这块面板的全部价值就在
 * 于「失败要出声」。
 */
export function IntelDegradeBanner({
  degrade,
  onOpenSettings,
  onRetry,
}: {
  degrade: IntelDegrade | null;
  onOpenSettings: () => void;
  onRetry: () => void;
}) {
  if (!degrade) return null;
  const isNoProvider = degrade.code === "no_provider" || degrade.code === "disabled";
  return (
    <div
      role="status"
      data-testid="intel-degrade-banner"
      className="flex items-center justify-between gap-2 border-b border-[var(--border)] bg-[color-mix(in_srgb,var(--warn)_12%,transparent)] px-3 py-1.5"
    >
      <span className="min-w-0 truncate text-[11px] text-[var(--text)]">
        ⚠ {degradeText(degrade)}
      </span>
      <Button variant="ghost" size="sm" onClick={isNoProvider ? onOpenSettings : onRetry}>
        {isNoProvider ? "去设置" : "立即重试"}
      </Button>
    </div>
  );
}

export type IntelEmptyKind =
  | "no-provider"
  | "not-collected"
  | "other-tier"
  | "no-match"
  | "none-archived";

/**
 * 空态分派。每种情况说一句不同的话，并各给一条可执行的出路——一个只说「没有数据」的空态
 * 会把用户留在原地。
 */
export function IntelEmptyState({
  kind,
  summary,
  nowMs,
  onOpenSettings,
  onCollect,
  onClearFilters,
}: {
  kind: IntelEmptyKind;
  summary: AiTradeIntelSummary | null;
  nowMs: number;
  onOpenSettings: () => void;
  onCollect: () => void;
  onClearFilters: () => void;
}) {
  const due = nextDueText(summary, nowMs);
  const body: Record<IntelEmptyKind, { title: string; hint: string; action?: React.ReactNode }> = {
    "no-provider": {
      title: "尚未配置情报检索源",
      // JSX 里写 `**粗体**` 只会显示字面星号（本仓库已复发两次），故强调一律用真实标签或措辞承担。
      hint: "情报由后端联网检索、模型摘要后逐条校验；没有检索源时本系统一条情报都不会产出，也绝不让模型凭空生成。",
      action: (
        <Button variant="primary" size="sm" onClick={onOpenSettings}>
          去设置检索源
        </Button>
      ),
    },
    "not-collected": {
      title: "已配置，但这个标的还没有完成过一轮采集",
      hint: due ? `${due}。也可以现在就手动跑一轮。` : "可以现在就手动跑一轮。",
      action: (
        <Button variant="primary" size="sm" onClick={onCollect}>
          立即采集
        </Button>
      ),
    },
    "other-tier": {
      title: "本层还没有条目",
      hint: "其它层级里已经有情报了——新采集的条目一律先进「超短」，由整理轮决定要不要升进中期或长期。",
    },
    "none-archived": {
      title: "还没有被归档或静音的情报",
      hint: "过期、被整理轮判为噪声、以及你手动静音的条目都会落在这里；每一条都还能恢复回在库。",
    },
    "no-match": {
      title: "没有符合当前筛选的情报",
      hint: "试着清空搜索词或把类别切回「全部」。",
      action: (
        <Button variant="ghost" size="sm" onClick={onClearFilters}>
          清除筛选
        </Button>
      ),
    },
  };
  const entry = body[kind];
  return (
    <div
      data-testid={`intel-empty-${kind}`}
      className="flex flex-col items-center gap-2 px-6 py-10 text-center"
    >
      <span className="text-xs font-medium text-[var(--text)]">{entry.title}</span>
      <span className="max-w-md text-[11px] leading-relaxed text-[var(--muted)]">
        {entry.hint}
      </span>
      {entry.action}
    </div>
  );
}
