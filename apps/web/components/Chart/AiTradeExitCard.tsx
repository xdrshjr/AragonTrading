"use client";

/**
 * 持仓卡的离场三态 + 【立即离场】兜底（ai-trade-exit-priority-and-commitment §4.6.3）。
 *
 * **独立组件文件**是刻意的（【R:P1-7】）：`AiTradePanel.tsx` 现有 798 行、距 CLAUDE.md 的 800 行
 * 红线只剩 2 行，本轮对它的净增被压到「一行 import + 一处挂载」。
 *
 * 三态（`exitCommitment` 键缺席 ⇒ 整块不渲染）：
 *  · `armed` 且未受阻 ⇒ accent + 细进度条：系统正在替用户做正确的事，不是一个错误，故不用红色。
 *  · `stuck` ⇒ danger + 【立即离场】：自动重试已到顶，主动权交回用户。
 *  · 未武装 ⇒ 不渲染（也不占位——常态多一行只会稀释真正要紧的信息）。
 *
 * 【立即离场】是本轮的人机交互兜底：**无论模型怎么想、无论哪一层怎么判，用户永远有一个不经模型
 * 的离场按钮**。二次确认弹窗的三句话由 `lib/aiTradeExit.ts` 单点持有——尤其第三句（交易所侧止损
 * 止盈会被一并撤销）少说一次，用户就会以为「先手动平掉、止损还留着」，而那是一个不存在的状态。
 */
import { useCallback, useState } from "react";
import { ApiRequestError } from "@/lib/api";
import { aiTradeApi } from "@/lib/aiTradeApi";
import {
  MANUAL_EXIT_CANCEL_TEXT,
  MANUAL_EXIT_CONFIRM_LINES,
  MANUAL_EXIT_CONFIRM_TEXT,
  MANUAL_EXIT_TITLE,
  describeExitCommitment,
} from "@/lib/aiTradeExit";
import type { AiTradeGroupMemberState } from "@/lib/aiTradeTypes";

export function AiTradeExitCard({
  groupId,
  member,
}: {
  groupId: string;
  member: AiTradeGroupMemberState;
}) {
  const view = describeExitCommitment(member.session?.horizon?.exitCommitment);
  const [confirming, setConfirming] = useState(false);
  const [pending, setPending] = useState(false);
  const [result, setResult] = useState<string | null>(null);
  const instId = member.instId;

  const exitNow = useCallback(() => {
    setPending(true);
    setResult(null);
    aiTradeApi
      .exitMember(groupId, instId)
      .then((res) => setResult(res.message || "已按市价平掉本会话自管持仓，会话继续运行"))
      .catch((err: unknown) => setResult(manualExitErrorText(err)))
      .finally(() => {
        setPending(false);
        setConfirming(false);
      });
  }, [groupId, instId]);

  if (!view) return null;
  const accent = view.stuck ? "var(--short)" : "var(--accent)";
  const ink = view.stuck ? "var(--short-ink)" : "var(--accent-ink)";
  return (
    <div
      className="flex flex-col gap-1 rounded border px-2 py-1"
      style={{ borderColor: accent }}
      data-testid="ai-trade-exit-card"
    >
      <span className="text-[10px] font-medium leading-relaxed" style={{ color: ink }}>
        {view.stuck ? "⚠ " : ""}
        {view.label}
      </span>
      <span className="tabular truncate text-[10px] text-[var(--muted)]">{view.detail}</span>
      {!view.stuck && (
        // 细进度条：只表达「已经试了几次 / 还剩几次」，不表达成功概率——后者不可知。
        <span className="h-0.5 w-full overflow-hidden rounded bg-[var(--surface-2)]">
          <span
            className="block h-full rounded"
            style={{ width: `${Math.round(view.progress * 100)}%`, backgroundColor: accent }}
          />
        </span>
      )}
      {view.stuck && !confirming && (
        <button
          type="button"
          onClick={() => setConfirming(true)}
          disabled={pending}
          className="self-start rounded border px-1.5 py-px text-[10px] transition-colors disabled:opacity-50"
          style={{ borderColor: accent, color: ink }}
          data-testid="ai-trade-manual-exit"
        >
          {MANUAL_EXIT_TITLE}
        </button>
      )}
      {confirming && (
        <div className="flex flex-col gap-1 rounded bg-[var(--surface-2)] px-1.5 py-1">
          <span className="text-[10px] font-medium text-[var(--text)]">
            {MANUAL_EXIT_TITLE}·请确认
          </span>
          {MANUAL_EXIT_CONFIRM_LINES.map((line) => (
            <span key={line} className="text-[10px] leading-relaxed text-[var(--muted)]">
              · {line}
            </span>
          ))}
          <span className="flex items-center gap-2">
            <button
              type="button"
              onClick={exitNow}
              disabled={pending}
              className="rounded border px-1.5 py-px text-[10px] transition-colors disabled:opacity-50"
              style={{ borderColor: accent, color: ink }}
              data-testid="ai-trade-manual-exit-confirm"
            >
              {pending ? "离场中…" : MANUAL_EXIT_CONFIRM_TEXT}
            </button>
            <button
              type="button"
              onClick={() => setConfirming(false)}
              disabled={pending}
              className="rounded border border-[var(--border)] px-1.5 py-px text-[10px] text-[var(--muted)] transition-colors disabled:opacity-50"
            >
              {MANUAL_EXIT_CANCEL_TEXT}
            </button>
          </span>
        </div>
      )}
      {result && <span className="truncate text-[10px] text-[var(--muted)]">{result}</span>}
    </div>
  );
}

/** 错误文案：**中文原因在前**，原始码括在后面（沿用 `repairErrorText` 的既有纪律）。 */
function manualExitErrorText(err: unknown): string {
  if (err instanceof ApiRequestError) {
    return err.code ? `${err.message}（${err.code}）` : err.message;
  }
  return "立即离场请求失败，请到下方实盘面板手动平仓";
}
