"use client";

import { useId, useState } from "react";
import clsx from "clsx";
import {
  CATEGORY_LABEL,
  IMPACT_LABEL,
  STANCE_LABEL,
  TIER_LABEL,
  absoluteTime,
  confidenceBand,
  isUnread,
  relativeTime,
  stanceColor,
} from "@/lib/intel";
import { REFUTED_WARNING } from "@/lib/intelClosedLoop";
import type {
  AiTradeIntelSummary,
  IntelConflict,
  IntelEvidence,
  IntelItem,
  IntelTier,
} from "@/lib/intelTypes";
import IntelAttributionRail from "./IntelAttributionRail";
import IntelConflictBadge from "./IntelConflictBadge";
import IntelEvidenceDrawer from "./IntelEvidenceDrawer";
import IntelEvidenceTrigger from "./IntelEvidenceTrigger";
import IntelSourceChips from "./IntelSourceChips";
import IntelVerdictBadge from "./IntelVerdictBadge";

export interface IntelCardActions {
  onTogglePin: (item: IntelItem) => void;
  onMove: (item: IntelItem, tier: IntelTier) => void;
  onEdit: (item: IntelItem) => void;
  onMute: (item: IntelItem) => void;
  onDelete: (item: IntelItem) => void;
  /** 把已归档 / 已静音的条目放回在库（`PATCH {status:"active"}`）。 */
  onRestore: (item: IntelItem) => void;
  /** 「我不同意」：清判定、恢复注入，并**永久**把它移出自动候选池（closed-loop P0-4）。 */
  onClearVerdict: (item: IntelItem) => void;
  /** 只核查这一条（绕过全部候选过滤，是一条人类显式指令）。 */
  onVerifyItem: (item: IntelItem) => void;
  /** 「不再采纳此源」：由卡片上的来源 chip 发起，走确认框。 */
  onBlockSource: (domain: string) => void;
  /**
   * 展开证据抽屉时的**懒加载**（evidence-grounding §3.3.2）。
   *
   * 结果按 `itemId` 缓存在弹窗里、收起不清缓存 ⇒ 同一次弹窗生命周期内只发一次。
   * 缺省 ⇒ 卡片不渲染任何证据入口（编辑器预览等只读场景）。
   */
  onLoadEvidence?: (item: IntelItem) => void;
}

const MOVE_TARGETS: IntelTier[] = ["ultra", "mid", "long"];

/**
 * 单条情报卡片（ai-trade-market-intel §3.8.3）。
 *
 * 信息层级是刻意的：**方向色点 → 类别 → 影响力**在左（这三者决定「要不要读下去」），
 * 时间 / 来源数 / 置信度靠右次要化（这三者决定「信不信」）。摘要默认两行截断，点击展开——
 * 展开是纯本地 state，**不发任何请求**。
 *
 * 动作区（置顶 / 移层 / 编辑 / 静音 / 删除）在 DOM 里紧随标题，hover 显形但键盘可达；每个动作的
 * `aria-label` 都带标题前 12 字，否则读屏用户会听到一排一模一样的「置顶」。
 *
 * **已归档 / 已静音的条目换一套动作**（恢复 / 删除）：对一条不在库的情报做「置顶」「移层」既无
 * 意义，也会让「静音」变成一条没有回头路的单行道——用户唯一能撤销它的办法就成了永久删除。
 */
export default function IntelCard({
  item,
  summary,
  actions,
  nowMs,
  evidence,
  evidenceLoading = false,
  evidenceError = "",
  blockedDomains = [],
  conflict,
  conflictPartner,
}: {
  item: IntelItem;
  summary: AiTradeIntelSummary | null;
  actions: IntelCardActions;
  nowMs: number;
  /** 这一条所属的矛盾对；缺省 ⇒ **徽标整枚不渲染**（老后端 / 总闸关时逐字节回退）。 */
  conflict?: IntelConflict;
  /** 矛盾对的另一端（只用于悬浮说明里那句「与「…」冲突」）。 */
  conflictPartner?: IntelItem;
  /** 该条目的证据（`undefined` = 还没取过）。 */
  evidence?: IntelEvidence | null;
  evidenceLoading?: boolean;
  evidenceError?: string;
  /** 已被用户拉黑的域名：抽屉里那几条外链据此变灰。 */
  blockedDomains?: string[];
}) {
  const [expanded, setExpanded] = useState(false);
  // ⚠️ **与摘要展开是两个独立的 state**：用户想读原文时，不该被迫先展开摘要。
  const [evidenceOpen, setEvidenceOpen] = useState(false);
  const drawerId = useId();
  const refuted = item.verdict === "refuted";
  const [moveOpen, setMoveOpen] = useState(false);
  const unread = isUnread(item, summary);
  const shortTitle = item.headline.slice(0, 12);
  const inactive = item.status !== "active";

  return (
    <div
      data-testid="intel-card"
      className={clsx(
        "group/card flex flex-col gap-1 rounded-[10px] border px-2.5 py-2 transition-colors",
        unread
          ? "border-[var(--accent)] bg-[var(--accent-soft)]/30"
          : "border-[var(--border)] bg-[var(--bg)]",
        // 被证伪 ⇒ 整卡降透明度：它已经不再进决策 prompt，视觉上必须与在用的那些区分开。
        refuted && "opacity-60",
      )}
    >
      {refuted && (
        <span
          data-testid="intel-refuted-warning"
          className="text-[10px] leading-relaxed text-[var(--danger)]"
        >
          {REFUTED_WARNING}
        </span>
      )}
      <div className="flex items-center justify-between gap-2">
        <span className="flex min-w-0 items-center gap-1.5">
          <span
            aria-hidden="true"
            className="h-1.5 w-1.5 shrink-0 rounded-full"
            style={{ backgroundColor: stanceColor(item.stance) }}
          />
          <span className="truncate text-[10px] text-[var(--muted)]">
            {STANCE_LABEL[item.stance]} · {CATEGORY_LABEL[item.category]} ·{" "}
            {IMPACT_LABEL[item.impact]}
            {item.pinned && " · 已置顶"}
            {item.userEdited && " · 用户编辑"}
            {item.isScheduled && " · 已排期"}
          </span>
          {/* 矛盾徽标紧跟在「方向 · 类别 · 影响力」之后：它回答的是同一个问题的另一半——
              「这条的方向可信吗」。挂在动作区旁边会被 hover 显形的那组按钮盖住。 */}
          <IntelConflictBadge item={item} pair={conflict} partner={conflictPartner} />
        </span>
        <span
          className="shrink-0 text-[10px] tabular text-[var(--muted)]"
          title={absoluteTime(item.eventTs)}
        >
          {relativeTime(item.eventTs, nowMs)} · {item.domainCount} 源 · 置信{" "}
          {item.confidence.toFixed(2)}（{confidenceBand(item.confidence)}）
        </span>
      </div>

      <div className="flex items-start justify-between gap-2">
        <button
          type="button"
          onClick={() => setExpanded((v) => !v)}
          aria-expanded={expanded}
          className="min-w-0 flex-1 text-left outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]"
        >
          <span className="block truncate text-xs font-medium text-[var(--text)]">
            {item.headline}
          </span>
          <span
            className={clsx(
              "block text-[11px] leading-relaxed text-[var(--muted)]",
              expanded ? "" : "line-clamp-2",
            )}
          >
            {item.summary}
          </span>
        </button>
        <span className="flex shrink-0 items-center gap-0.5 opacity-0 transition-opacity duration-150 ease-smooth focus-within:opacity-100 group-hover/card:opacity-100 motion-reduce:transition-none [@media(hover:none)]:opacity-100">
          {inactive ? (
            <>
              <CardAction
                label={`恢复：${shortTitle}…`}
                text="恢复"
                onClick={() => actions.onRestore(item)}
              />
              <CardAction
                label={`删除：${shortTitle}…`}
                text="删除"
                danger
                onClick={() => actions.onDelete(item)}
              />
            </>
          ) : (
            <>
              <CardAction
                label={`${item.pinned ? "取消置顶" : "置顶"}：${shortTitle}…`}
                text={item.pinned ? "取消置顶" : "置顶"}
                onClick={() => actions.onTogglePin(item)}
              />
              <span className="relative">
                <CardAction
                  label={`移层：${shortTitle}…`}
                  text="移层"
                  onClick={() => setMoveOpen((v) => !v)}
                />
                {moveOpen && (
                  <span className="absolute right-0 top-full z-10 mt-0.5 flex flex-col rounded-md border border-[var(--border)] bg-[var(--surface)] p-0.5 shadow-soft">
                    {MOVE_TARGETS.filter((t) => t !== item.tier).map((tier) => (
                      <button
                        key={tier}
                        type="button"
                        onClick={() => {
                          setMoveOpen(false);
                          actions.onMove(item, tier);
                        }}
                        className="whitespace-nowrap rounded px-2 py-1 text-left text-[10px] text-[var(--text)] hover:bg-[var(--surface-2)]"
                      >
                        移到{TIER_LABEL[tier]}
                      </button>
                    ))}
                  </span>
                )}
              </span>
              <CardAction
                label={`编辑：${shortTitle}…`}
                text="编辑"
                onClick={() => actions.onEdit(item)}
              />
              <CardAction
                label={`静音：${shortTitle}…`}
                text="静音"
                onClick={() => actions.onMute(item)}
              />
              <CardAction
                label={`删除：${shortTitle}…`}
                text="删除"
                danger
                onClick={() => actions.onDelete(item)}
              />
            </>
          )}
        </span>
      </div>

      {/* 归因轨与判定徽章插在来源 chips **之上**：它们回答的是「这条到底有没有用 / 还真不真」，
          比「谁说的」更靠近用户在这一步的判断。零引用 / 无判定时两者各自整块不渲染。 */}
      <div className="flex items-center justify-between gap-2">
        <IntelAttributionRail item={item} />
        <IntelVerdictBadge
          item={item}
          onClearVerdict={actions.onClearVerdict}
          onVerifyNow={actions.onVerifyItem}
          verifyQuoteCount={
            evidence?.quotes.filter((quote) => quote.role === "verify").length ?? 0
          }
        />
      </div>

      {actions.onLoadEvidence && (
        <>
          <IntelEvidenceTrigger
            item={item}
            open={evidenceOpen}
            controls={drawerId}
            onToggle={() => {
              const next = !evidenceOpen;
              setEvidenceOpen(next);
              // 收起不清缓存：再次展开时**不重发**（§3.3.2 的懒加载纪律）。
              if (next && evidence === undefined) actions.onLoadEvidence?.(item);
            }}
          />
          {evidenceOpen && (
            <IntelEvidenceDrawer
              id={drawerId}
              evidence={evidence ?? null}
              loading={evidenceLoading}
              error={evidenceError}
              blockedDomains={blockedDomains}
              nowMs={nowMs}
              onBlockSource={actions.onBlockSource}
            />
          )}
        </>
      )}

      <IntelSourceChips sources={item.sources} onBlock={actions.onBlockSource} />
      {item.curationNote && (
        <span className="truncate text-[10px] text-[var(--muted)]" title={item.curationNote}>
          整理记录：{item.curationNote}
        </span>
      )}
    </div>
  );
}

function CardAction({
  label,
  text,
  danger,
  onClick,
}: {
  label: string;
  text: string;
  danger?: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      onClick={onClick}
      className={clsx(
        "rounded px-1 py-0.5 text-[10px] outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]",
        danger
          ? "text-[var(--short-ink)] hover:bg-[var(--surface-2)]"
          : "text-[var(--muted)] hover:bg-[var(--surface-2)] hover:text-[var(--text)]",
      )}
    >
      {text}
    </button>
  );
}
