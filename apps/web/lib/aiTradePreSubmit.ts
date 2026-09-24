// 下单前复检的「为什么没开」文案（ai-trade-profit-lock-and-entry-unblock 轴 C-5）。
//
// 合入前，下单前被丢弃的开仓在执行轨迹上只显示「已跳过」——原因码与读数都不显示。生产最新组 181
// 次开仓意图里 172 次死在这里，而界面上一个字都看不出来：这是「静默丢弃」的最后一环。
// 本文件是纯函数；`describeExecuted` 的 skipped 分支只委托一行。

import { executionReasonLabel } from "./aiTradeIntegrity";
import type { AiTradeDecision, AiTradePreSubmitCheck } from "./aiTradeTypes";

export interface AiPreSubmitView {
  label: string;
  detail: string;
  tone: "short" | "muted";
}

const IDENTITY_LABELS: Record<string, string> = {
  positionSeq: "持仓已开平",
  side: "持仓方向",
  qty: "持仓数量",
  restingClOrdId: "挂单",
};

function bps(value: number): string {
  return Number.isFinite(value) ? `${+value.toFixed(2)}` : "—";
}

function describeCheck(check: AiTradePreSubmitCheck): AiPreSubmitView {
  if (check.kind === "cost") {
    return {
      label: `未开仓：执行前成本上升 ${bps(check.deltaBps)} bps > 容忍 ${bps(check.toleranceBps)}`,
      detail:
        `研判期间这笔开仓的往返成本从 ${bps(check.frozenBps)} bps 升到 ${bps(check.currentBps)} bps，`
        + "超过容忍度，放弃本次开仓并等待下一轮重新研判。",
      tone: "short",
    };
  }
  if (check.kind === "drift") {
    return {
      label: `未开仓：价格已不利移动 ${bps(check.driftBps)} bps > ${bps(check.limitBps)}，放弃追单`,
      detail:
        `模型看到的价格是 ${check.refPx}，下单前已到 ${check.nowPx}。市价单追进去等于用更差的价格`
        + "执行一个按旧价格做出的判断。",
      tone: "short",
    };
  }
  const changed = check.changed.map((key) => IDENTITY_LABELS[key] ?? key).join("、") || "持仓";
  return {
    label: `未开仓：研判期间持仓/挂单已变化（${changed}）`,
    detail: "模型做出判断时看到的持仓或挂单，在下单前已经不同，按旧事实下单不安全。",
    tone: "muted",
  };
}

/**
 * 下单前复检拦下的开仓 → 执行段文案；不是复检拦截 ⇒ `null`（调用方走原逻辑，「已跳过」逐字不变）。
 *
 * 老会话没有 `preSubmitCheck` 但有原因码 ⇒ 回落到原因标签，旧会话同样能看懂。
 */
export function describePreSubmitCheck(
  executed: AiTradeDecision["executed"],
): AiPreSubmitView | null {
  if (!executed) return null;
  const check = executed.preSubmitCheck;
  if (check && (check.kind === "cost" || check.kind === "drift" || check.kind === "identity")) {
    return describeCheck(check);
  }
  const reason = typeof executed.executionContextChanged === "string"
    ? executed.executionContextChanged
    : null;
  if (!reason) return null;
  return {
    label: `未开仓：${executionReasonLabel(reason)}`,
    detail: "下单前复检未通过，本次开仓未提交到交易所。",
    tone: "muted",
  };
}
