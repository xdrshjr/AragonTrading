"use client";

import { Button } from "@/components/ui";
import { degradeAction, degradeText, nextDueText } from "@/lib/intel";
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
  onRearm,
}: {
  degrade: IntelDegrade | null;
  onOpenSettings: () => void;
  onRetry: () => void;
  /** 重新装配（`arm_failed` 专用）。缺省 ⇒ 该分支退化成无按钮，绝不误导。 */
  onRearm?: () => void;
}) {
  if (!degrade) return null;
  // 按钮按降级码三分叉（ai-trade-intel-arm-state §3.4.2）。此前是一句
  // `code === "no_provider" || code === "disabled"` 的二分：两个语义完全不同的码共用同一个
  // 按钮，而「总闸关着」的出路和「没选源」并不是同一个动作。
  const action = degradeAction(degrade);
  const handler =
    action === "settings" ? onOpenSettings : action === "rearm" ? onRearm : onRetry;
  const label = action === "settings" ? "去设置" : action === "rearm" ? "重新装配" : "立即重试";
  return (
    <div
      role="status"
      data-testid="intel-degrade-banner"
      className="flex items-center justify-between gap-2 border-b border-[var(--border)] bg-[color-mix(in_srgb,var(--warn)_12%,transparent)] px-3 py-1.5"
    >
      <span className="min-w-0 truncate text-[11px] text-[var(--text)]">
        ⚠ {degradeText(degrade)}
      </span>
      {/* `none` 与「拿不到 onRearm」两种情况都不渲染按钮：一个点了什么也不发生的按钮，
          比不给按钮更坏。 */}
      {action !== "none" && handler ? (
        <Button variant="ghost" size="sm" onClick={handler}>
          {label}
        </Button>
      ) : null}
    </div>
  );
}

export type IntelEmptyKind =
  | "no-provider"
  | "intel-off"
  | "not-armed"
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
  onRearm,
}: {
  kind: IntelEmptyKind;
  summary: AiTradeIntelSummary | null;
  nowMs: number;
  onOpenSettings: () => void;
  onCollect: () => void;
  onClearFilters: () => void;
  /** 重新装配（只在 `arm_failed` 下渲染）。缺省 ⇒ 该按钮整个不出现。 */
  onRearm?: () => void;
}) {
  const due = nextDueText(summary, nowMs);
  // `not-armed` 的正文**取后端下发的 `degrade.message`**：它区分 `not_running` /
  // `arm_failed` / `arm_pending` 三种处境，而前端再编一份必漂移。
  const armHint =
    summary?.degrade?.message ?? "情报流水线目前没有在为这个标的采集。";
  const canRearm = summary?.degrade ? degradeAction(summary.degrade) === "rearm" : false;
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
    "intel-off": {
      title: "市场情报采集已关闭",
      hint: "检索源已经配好了。打开设置里的「启用市场情报采集」，这个标的就会按节律开始采集；已经存下来的情报一条都不会丢。",
      action: (
        <Button variant="primary" size="sm" onClick={onOpenSettings}>
          去设置里开启
        </Button>
      ),
    },
    "not-armed": {
      title: "检索源已配好，但这个标的的情报流水线没有在跑",
      hint: armHint,
      action:
        canRearm && onRearm ? (
          <Button variant="primary" size="sm" onClick={onRearm}>
            重新装配
          </Button>
        ) : undefined,
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
