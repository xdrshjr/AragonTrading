"use client";

/**
 * 行情断连自愈的恢复卡（ai-trade-feed-self-healing 轴 D）。
 *
 * **独立组件文件**是刻意的：`AiTradePanel.tsx` 现有 797 行、距 CLAUDE.md 的 800 行红线只剩 3 行，
 * 本轮对它的净增被压到「一行 import + 一处挂载」。
 *
 * **入参是 `members` 数组而不是单成员**：`feedRecovery` 是**成员级**的（一组可以有 5 个），而这个
 * 挂载位（横幅之下、可交易性红条之上）是**分组级**上下文；挂一个只吃单成员的卡在这里无法表达
 * 5 个标的，而在面板里补一段 `.map()` 会直接顶穿 800 行红线。
 *
 * 三态（与 `AiTradeExitCard` 同源，不发明第二套视觉语言）：
 *  · `healthy` / 键缺席 / `null` ⇒ **整块不渲染**（连外框都不渲染，不占位——常态多一行只会稀释
 *    真正要紧的信息）。
 *  · `recovering` ⇒ `accent` 边框：系统正在替用户做正确的事，不是一个错误，故不用红色。
 *  · `stuck` ⇒ `danger`（`var(--short)`）+ 【立即重连】主按钮 + 【仅停止该标的】次按钮。
 *
 * **它的渲染条件与 `reason` 解绑**（【R:P1-4】）：REST 吊命期成员会回到 `armed`（横幅消失），而这张
 * 卡**必须**继续在场——把渲染条件绑在 `feed_stale` / `feed_down` 上，正好会在最需要它的那一刻把它
 * 关掉，那正是「把一个看得见的停摆变成看不见的停摆」。
 *
 * `role="status"`（`aria-live="polite"`）而不是 `alert`：`alert` 留给横幅，同一屏两个 `alert` 会让
 * 屏幕阅读器把用户按在原地读两遍。
 */
import { useCallback, useState } from "react";
import { ApiRequestError } from "@/lib/api";
import { aiTradeApi } from "@/lib/aiTradeApi";
import {
  FEED_RELINK_PENDING_TEXT,
  FEED_RELINK_TITLE,
  FEED_STOP_MEMBER_TEXT,
  describeFeedRecoveryRows,
  isFeedRecoveryStuck,
} from "@/lib/aiTradeFeed";
import type { AiTradeGroupMemberState } from "@/lib/aiTradeTypes";

export function AiTradeFeedRecoveryCard({
  groupId,
  members,
}: {
  groupId: string;
  members: AiTradeGroupMemberState[];
}) {
  const rows = describeFeedRecoveryRows(members);
  const [pendingInst, setPendingInst] = useState<string | null>(null);
  const [result, setResult] = useState<string | null>(null);

  const relink = useCallback(
    (instId: string) => {
      // pending 状态机（镜像 `AiTradeExitCard`）：点击后立即禁用，回执到达前不得二次点击。
      setPendingInst(instId);
      setResult(null);
      aiTradeApi
        .relinkMember(groupId, instId)
        .then((res) => setResult(res.message || "已重建行情连接"))
        .catch((err: unknown) => setResult(relinkErrorText(err)))
        .finally(() => setPendingInst(null));
    },
    [groupId],
  );

  const stopMember = useCallback(
    (instId: string) => {
      setPendingInst(instId);
      setResult(null);
      aiTradeApi
        .stopMember(groupId, instId)
        .then(() => setResult(`已停止 ${instId}，其余标的照常交易`))
        .catch((err: unknown) => setResult(relinkErrorText(err)))
        .finally(() => setPendingInst(null));
    },
    [groupId],
  );

  if (rows.length === 0) return null;
  const stuck = isFeedRecoveryStuck(rows);
  const accent = stuck ? "var(--short)" : "var(--accent)";
  const ink = stuck ? "var(--short-ink)" : "var(--accent-ink)";
  return (
    <div
      className="flex flex-col gap-1 rounded border px-2 py-1.5"
      style={{ borderColor: accent }}
      data-testid="ai-trade-feed-recovery-card"
      role="status"
      aria-live="polite"
    >
      {rows.map((row) => (
        <div key={row.instId} className="flex flex-col gap-1">
          <span
            className="text-[10px] font-medium leading-relaxed"
            style={{ color: row.stuck ? "var(--short-ink)" : "var(--accent-ink)" }}
            data-testid="ai-trade-feed-recovery-row"
          >
            {row.stuck ? "⚠ " : ""}
            {row.label}
          </span>
          {!row.stuck && row.nextAttemptInS !== null && (
            // 细活动条：只表达「还在自动重试」这一件事，**刻意不表达百分比**——重试没有分母，
            // 一根 33% 的进度条会被读成「已经完成三分之一」，那是一个我们并不知道的数。
            // `nextAttemptInS === null`（冷却外 / 已升级）时整行不渲染：绝不对着一个不会到来的
            // 时刻读秒（与 `aiTrade.test.ts` 冻结的那条既有规则同极性）。
            <span
              className="block h-px w-full rounded opacity-40"
              style={{ backgroundColor: accent }}
              data-testid="ai-trade-feed-recovery-progress"
            />
          )}
          <span className="flex flex-wrap items-center gap-2">
            <button
              type="button"
              onClick={() => relink(row.instId)}
              disabled={pendingInst !== null}
              className="rounded border px-1.5 py-px text-[10px] transition-colors disabled:opacity-50"
              style={{ borderColor: accent, color: ink }}
              data-testid="ai-trade-feed-relink"
            >
              {pendingInst === row.instId ? FEED_RELINK_PENDING_TEXT : FEED_RELINK_TITLE}
            </button>
            {row.stuck && (
              <button
                type="button"
                onClick={() => stopMember(row.instId)}
                disabled={pendingInst !== null}
                className="rounded border border-[var(--border)] px-1.5 py-px text-[10px] text-[var(--muted)] transition-colors disabled:opacity-50"
                data-testid="ai-trade-feed-stop-member"
              >
                {FEED_STOP_MEMBER_TEXT}
              </button>
            )}
          </span>
        </div>
      ))}
      {result && <span className="truncate text-[10px] text-[var(--muted)]">{result}</span>}
    </div>
  );
}

/** 错误文案：**中文原因在前**，原始码括在后面（沿用 `manualExitErrorText` 的既有纪律）。 */
function relinkErrorText(err: unknown): string {
  if (err instanceof ApiRequestError) {
    return err.code ? `${err.message}（${err.code}）` : err.message;
  }
  return "重连请求失败，请检查网络 / 代理后重试";
}
