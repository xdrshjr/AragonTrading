"use client";

import clsx from "clsx";
import { absoluteTime, relativeTime } from "@/lib/intel";
import {
  alertRowState,
  alertRowText,
  alertRowTone,
  type AlertGroup,
} from "@/lib/intelActionability";

/**
 * 动态时间线上的一行告警（ai-trade-intel-actionability §3.4.1）。
 *
 * **三态的文案与色调必须分开**，这是本轮最容易做成「看不见的停摆」的地方：
 *
 * - 未告警（灰）：连续同码的多行**折叠成一行 + 计数**（否则一个配置偏严的用户会看到几十行
 *   「未告警 · 影响力未达门槛」把真正的事件淹掉）；
 * - 已告警但还没有结果（常规色）：说「等待会话响应」，**超过时限**改说「会话未在时限内响应」
 *   ——这是**渲染期判定，不回写库**；
 * - 已有结果：带色调，`woke` 用 accent 并**可点击跳到那一轮决策轨迹**。
 *
 * 抑制码与结果码的中文**由后端下发**（`suppressedText` / `outcomeText`），前端一个字都不编。
 */
export default function IntelAlertRow({
  group,
  nowMs,
  pendingTtlS,
  onOpenRound,
}: {
  group: AlertGroup;
  nowMs: number;
  /** 待处理告警的时限（秒），来自设置；用于把「还没回」与「再也不会回」分开。 */
  pendingTtlS: number;
  /** 跳到那一轮决策轨迹；缺省 ⇒ 该行不可点（`woke` 行也只显示，不做假的可点样式）。 */
  onOpenRound?: (runId: number, roundNo: number) => void;
}) {
  const alert = group.head;
  const state = alertRowState(alert, nowMs, pendingTtlS);
  const tone = alertRowTone(alert, state);
  const jumpable =
    state === "settled" && alert.outcome === "woke" && !!alert.runId && !!onOpenRound;
  const text = alertRowText(alert, state);

  return (
    <div
      data-testid="intel-alert-row"
      data-state={state}
      className={clsx(
        "rounded-[10px] border px-2.5 py-1.5",
        state === "suppressed"
          ? "border-[var(--border)] opacity-70"
          : "border-[var(--border)]",
      )}
    >
      <div className="flex items-center justify-between gap-2">
        <span className="flex min-w-0 items-center gap-1.5">
          <span
            aria-hidden="true"
            className="h-1.5 w-1.5 shrink-0 rounded-full"
            style={{ backgroundColor: tone }}
          />
          <span className="truncate text-[11px]" style={{ color: tone }}>
            {text}
            {group.count > 1 && (
              <span data-testid="intel-alert-fold" className="ml-1 text-[var(--muted)]">
                ×{group.count}
              </span>
            )}
          </span>
        </span>
        <span
          className="shrink-0 text-[10px] tabular text-[var(--muted)]"
          title={absoluteTime(alert.createdTs)}
        >
          {relativeTime(alert.createdTs, nowMs)}
        </span>
      </div>

      {!!alert.headline && (
        <div className="mt-0.5 flex items-center justify-between gap-2">
          <span className="min-w-0 truncate text-[11px] text-[var(--text)]" title={alert.headline}>
            {alert.ref ? `[${alert.ref}] ` : ""}
            {alert.headline}
          </span>
          {/* 「唤醒了第几轮」是 G-11 的验收事实，**没有跳转能力时也必须说出来**：不给回调就
              整段不渲染，等于把台账上已有的那个事实从界面上抹掉。可点时才画成按钮——不做
              假的可点样式（本组件 docstring 的成文纪律）。 */}
          {jumpable ? (
            <button
              type="button"
              data-testid="intel-alert-jump"
              onClick={() => onOpenRound?.(alert.runId, alert.roundNo)}
              className="shrink-0 rounded px-1 py-0.5 text-[10px] text-[var(--accent-ink)] outline-none hover:bg-[var(--accent-soft)] focus-visible:ring-2 focus-visible:ring-[var(--accent)]"
            >
              第 {alert.roundNo} 轮 →
            </button>
          ) : (
            !!alert.roundNo && (
              <span
                data-testid="intel-alert-round"
                className="shrink-0 px-1 py-0.5 text-[10px] text-[var(--muted)]"
              >
                第 {alert.roundNo} 轮
              </span>
            )
          )}
        </div>
      )}

      {alert.fired && (
        <span className="mt-0.5 block text-[10px] tabular text-[var(--muted)]">
          严重度 {alert.severity.toFixed(2)} · {alert.domainCount} 源 · 置信{" "}
          {alert.confidence.toFixed(2)}
          {alert.quoted ? " · 有原文引证" : ""}
          {/* 「已告警 · 未进入情报段」是 R-15 的可见落点：高危告警**可能根本不在情报段里**
              （超短层排在最后 + prompt 长度上限），唤醒句会如实内联摘要，而这一行让用户
              发现「该调高情报段长度上限了」。
              ⚠️ 判据取 `inDigest` 而**不是** `!ref`：`cite` 关时全库都没有编号，用「没编号」
              冒充「不在情报段里」，会让每一条告警都挂上这句话——那正是后端 P0-3 花了整节去
              禁止的那次混淆，只是搬到了界面上。 */}
          {state !== "suppressed" && !alert.inDigest ? " · 未进入情报段" : ""}
        </span>
      )}
    </div>
  );
}
