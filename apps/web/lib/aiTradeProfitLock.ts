// 成本锚定收益锁的展示纯函数（ai-trade-profit-lock-and-entry-unblock 轴 C-1 / C-2）。
//
// 收益锁以**价格 bps** 计、以本笔往返成本为锚：浮盈覆盖约 1.5 倍成本后把离场线抬到含费保本之上，
// 浮盈继续扩大后锁住峰值的一半。它只会平仓，不会开仓或加仓。本文件只负责把后端快照说成人话，
// 所有判定都在后端 `profit_lock.py`；这里的阈值预览与后端公式逐项对应（双向用例锁住）。

import type { AiTradeProfitLockRule, AiTradeProfitLockSnapshot } from "./aiTradeTypes";
import { fmtPrice } from "./format";

/** 与后端 `config.py` 的 `ai_trade_profit_lock_*` 默认值逐项一致（只用于预览与表单初值）。 */
export const AI_TRADE_PROFIT_LOCK_DEFAULTS = {
  enabled: true,
  armCostMult: 1.5,
  armAtrMult: 0.35,
  bufferBps: 2,
  trailCostMult: 3,
  trailAtrMult: 0.8,
  keepRatio: 0.5,
  minHoldSeconds: 60,
  confirmSeconds: 8,
} as const;

/** 武装瞬间 mark 与锁线的最小间距（bps），与后端 `profit_lock.MIN_GAP_BPS` 相同。 */
export const PROFIT_LOCK_MIN_GAP_BPS = 3;

export type ProfitLockTone = "muted" | "accent" | "success" | "warn";

export interface ProfitLockView {
  tone: ProfitLockTone;
  text: string;
  title: string;
}

const UNAVAILABLE_TEXT: Record<string, string> = {
  stale_px: "行情暂停更新",
  cost_unknown: "成本暂不可估",
};

function bps(value: number | null | undefined): string {
  return value == null || !Number.isFinite(value) ? "—" : `${+value.toFixed(1)}`;
}

/** 价格：极小价（如 SATS 1e-8 量级）用科学计数，否则沿用全站价格格式。 */
export function fmtLockPx(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return "—";
  return value !== 0 && Math.abs(value) < 1e-4 ? value.toExponential(3) : fmtPrice(value);
}

function formulaTitle(snap: AiTradeProfitLockSnapshot, exits?: number | null): string {
  const lines = [
    `往返成本约 ${bps(snap.costBps)} bps · ATR 约 ${bps(snap.atrBps)} bps`,
    `浮盈到 ${bps(snap.armBps)} bps 时抬到含费保本；到 ${bps(snap.trailArmBps)} bps 时锁住峰值的一半`,
    `当前峰值浮盈 ${bps(snap.mfeBps)} bps · 越线须持续 ${snap.confirmSeconds}s 才平仓`,
    "目标净额按锁线计；市价离场会叠加确认窗口内的不利移动与滑点，实际可能略低",
  ];
  if (snap.unavailableReason === "min_hold") lines.push("开仓不久，最短持有期内只记峰值、不平仓");
  if (exits != null) lines.push(`本会话已锁平 ${exits} 次`);
  return lines.join("\n");
}

/**
 * 收益锁快照 → 持仓行的一行文案 + 色调 + 悬停读数。
 *
 * @param snapshot 后端 `position.profitLock`。
 * @param exits status 顶层 `profitLock.exits`（本会话锁平次数，只进悬停说明）；缺席 ⇒ 不写。
 */
export function describeProfitLock(
  snapshot: AiTradeProfitLockSnapshot,
  exits?: number | null,
): ProfitLockView {
  const title = formulaTitle(snapshot, exits);
  if (snapshot.status === "unavailable") {
    const why = UNAVAILABLE_TEXT[snapshot.unavailableReason ?? ""] ?? "暂不可判定";
    return { tone: "warn", text: `收益锁 · 暂停判定（${why}）`, title };
  }
  if (snapshot.stage === 2) {
    return {
      tone: "success",
      text: `收益锁 · 已锁 ${bps(snapshot.lockNetBps)} bps · 线 ${fmtLockPx(snapshot.lockPx)}`,
      title,
    };
  }
  if (snapshot.stage === 1) {
    return {
      tone: "accent",
      text: `收益锁 · 保本锁 ${fmtLockPx(snapshot.lockPx)}（目标净 +${bps(snapshot.lockNetBps)} bps）`,
      title,
    };
  }
  return {
    tone: "muted",
    text: `收益锁 · 浮盈 ${bps(snapshot.mfeBps)} / 需 ${bps(snapshot.armBps)} bps 武装`,
    title,
  };
}

export interface LockThresholdPreview {
  armBps: number;
  trailArmBps: number;
}

/**
 * 按当前标的与周期的成本、ATR 预估两级门槛（与后端 `_thresholds` + `MIN_GAP_BPS` 同式）。
 * 成本不可得 ⇒ `null`（调用方只显示公式，不编数字）。
 */
export function previewLockThresholds(
  costBps: number | null | undefined,
  atrBps: number | null | undefined,
  rule: AiTradeProfitLockRule = { enabled: true },
): LockThresholdPreview | null {
  if (costBps == null || !Number.isFinite(costBps)) return null;
  const d = AI_TRADE_PROFIT_LOCK_DEFAULTS;
  const atr = atrBps != null && Number.isFinite(atrBps) ? Math.max(0, atrBps) : 0;
  const armCost = rule.armCostMult ?? d.armCostMult;
  const floor = costBps + (rule.bufferBps ?? d.bufferBps);
  const armBps = Math.max(armCost * costBps, (rule.armAtrMult ?? d.armAtrMult) * atr,
    floor + PROFIT_LOCK_MIN_GAP_BPS);
  // 阶段 2 只在阶段 1 已武装之后才可能生效（后端 `derive_lock` 先判阶段 1），故不低于 armBps。
  const trailArmBps = Math.max((rule.trailCostMult ?? d.trailCostMult) * costBps,
    (rule.trailAtrMult ?? d.trailAtrMult) * atr, armBps);
  return { armBps, trailArmBps };
}
