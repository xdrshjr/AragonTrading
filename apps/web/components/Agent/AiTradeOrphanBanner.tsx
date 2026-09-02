"use client";

import { useState } from "react";

import type { AiTradeGroupMemberState } from "@/lib/aiTradeTypes";

/**
 * 孤儿持仓横幅（ai-trade-fill-truth-and-effective-controls 轴 B / §7.3）。
 *
 * 它要消灭的形态是：一笔因「撤单与成交竞态」「限价单部分成交」「用户手工下单」而出现在交易所的
 * 持仓，对整条自动交易链**完全不可见**——不受持仓周期闸管、不受利润保护管、不受活性看门狗管。
 * `LiveExecutor._on_reconcile_ok` 今天全文只有「本地有仓、交易所没仓」这一个方向，没有对称的
 * 那一半；这笔仓只会在对账的 `coverageMismatch` 上留下一个数字。生产实测已经出现过两次。
 *
 * 四条呈现纪律（前三条与 `AiTradeLivenessBanner` 同源，第四条是本组件独有的）：
 *
 * 1. **判据一个字都不在前端算。** 是否有孤儿仓、张数 / 均价 / 浮盈是多少，全部取后端 `orphan`
 *    wire 的现成读数，本组件只负责摆出来（`orphanMessage` 里没有任何一个数是前端推出来的）。
 *    那句带三条补救的完整说明由后端 `orphan.orphan_message` 单点下发、走告警通道，与这块**持续
 *    挂在屏幕上的现状**分工不同：告警回答「出了什么事、能怎么办」，这里回答「现在还有这件事」。
 * 2. **逐标的呈现，绝不整组与门。** 三个标的里只有一个有孤儿仓时，把另外两个也说成有，是另一种
 *    谎话。
 * 3. **`pending` 档不渲染。** 「看见了但还没连续够轮数」是一个尚未成立的判定，把它画出来会在
 *    正常的成交时序上闪一下——而 `confirm_rounds` 存在的全部理由就是压掉那种闪动。
 * 4. **「接管」必须二次确认。** 它是本轮唯一一个会改写真实资金立场的按钮，而它接管的是一笔来路
 *    不明的持仓。确认态就地展开、绝不弹第二层模态：这一屏上用户已经有足够的上下文做决定了。
 *
 * 未装配（运维总闸关）/ 老后端 ⇒ 成员行上整键缺席 ⇒ 本组件整段不渲染。
 */
export default function AiTradeOrphanBanner({
  members,
  onAdopt,
}: {
  members: AiTradeGroupMemberState[];
  /** 接管回调（由容器接 `POST /ai-trade/{gid}/member/{inst}/adopt-orphan`）；未接 ⇒ 只显示不给按钮。 */
  onAdopt?: (instId: string) => Promise<void> | void;
}) {
  const rows = members
    .map((m) => ({ instId: m.instId, orphan: m.session?.orphan }))
    .filter((row) => row.orphan && row.orphan.state === "confirmed");
  if (rows.length === 0) return null;

  return (
    <div className="flex flex-col gap-1" data-testid="ai-orphan-banner">
      {rows.map(({ instId, orphan }) => (
        <OrphanRow key={instId} instId={instId} orphan={orphan!} onAdopt={onAdopt} />
      ))}
    </div>
  );
}

function OrphanRow({
  instId,
  orphan,
  onAdopt,
}: {
  instId: string;
  orphan: NonNullable<AiTradeGroupMemberState["session"]>["orphan"];
  onAdopt?: (instId: string) => Promise<void> | void;
}) {
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  if (!orphan) return null;

  const adopt = async () => {
    if (!onAdopt) return;
    setBusy(true);
    setError(null);
    try {
      await onAdopt(instId);
      setConfirming(false);
    } catch (e) {
      // 失败必须留在屏幕上：这是一个改真实资金立场的动作，静默失败等于让用户以为已经接管了。
      setError(e instanceof Error ? e.message : "接管失败，请重试或到实盘面板手动处理");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div
      role="alert"
      data-testid={`ai-orphan-row-${instId}`}
      className="flex flex-col gap-1 rounded border border-[var(--short)] bg-[var(--short-soft)] px-2 py-1.5 text-[10px] leading-relaxed"
    >
      <span className="font-medium text-[var(--short-ink)]">
        {instId} · 交易所侧存在未接管持仓
      </span>
      {/* 后端下发的整句（含张数 / 均价 / 浮盈 + 三条补救；封锁开仓时逐字写明）。 */}
      <span className="text-[var(--text)]" data-testid={`ai-orphan-message-${instId}`}>
        {orphanMessage(orphan)}
      </span>
      {onAdopt && !confirming && (
        <button
          type="button"
          onClick={() => setConfirming(true)}
          data-testid={`ai-orphan-adopt-${instId}`}
          className="self-start rounded border border-[var(--border)] bg-[var(--surface-2)] px-2 py-0.5 text-[10px] text-[var(--text)] hover:border-[var(--accent)]"
        >
          接管这笔持仓
        </button>
      )}
      {onAdopt && confirming && (
        <span className="flex flex-col gap-1">
          <span className="text-[var(--warn)]">
            接管会把这笔持仓纳入本会话的账本与风控，并改变本会话的真实资金立场。确认吗？
          </span>
          <span className="flex items-center gap-2">
            <button
              type="button"
              disabled={busy}
              onClick={adopt}
              data-testid={`ai-orphan-confirm-${instId}`}
              className="rounded border border-[var(--short)] bg-[var(--surface-2)] px-2 py-0.5 text-[10px] text-[var(--short-ink)] disabled:opacity-50"
            >
              {busy ? "接管中…" : "确认接管"}
            </button>
            <button
              type="button"
              disabled={busy}
              onClick={() => setConfirming(false)}
              className="rounded border border-[var(--border)] px-2 py-0.5 text-[10px] text-[var(--muted)] disabled:opacity-50"
            >
              取消
            </button>
          </span>
        </span>
      )}
      {error && <span className="text-[var(--short-ink)]">{error}</span>}
    </div>
  );
}

/**
 * 读数 → 一行现状。
 *
 * 这里**只拼读数、不拼结论**：后端的 `orphan_message` 才是那句带三条补救的完整说明，它经
 * `error` 事件走告警通道；成员行上的 `orphan` wire 只带原始数字，故本行如实把它们摆出来。
 * 两者不重复也不冲突——一个是「出事那一刻的告警」，一个是「持续挂在屏幕上的现状」。
 */
function orphanMessage(orphan: NonNullable<AiTradeGroupMemberState["session"]>["orphan"]): string {
  if (!orphan) return "";
  const parts = [`${orphan.qty} 张`];
  if (orphan.avgPx != null) parts.push(`开仓均价 ${orphan.avgPx}`);
  if (orphan.uplUsd != null) {
    parts.push(`当前浮盈 ${orphan.uplUsd >= 0 ? "+" : ""}${+orphan.uplUsd.toFixed(4)} USDT`);
  }
  return (
    `${parts.join(" · ")}。它不受持仓周期、利润保护与活性看门狗的任何管辖，` +
    "止盈止损只由交易所侧已挂的委托承担。"
  );
}
