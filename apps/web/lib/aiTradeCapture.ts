// 利润捕获率纯函数（ai-trade-profit-protection-adaptive §2.4 / F4）。
//
// 本轮迭代循环要回答的验收问题是：**峰值浮盈里，最终落袋了几成**。现有观测只有 profitGuardExits
// （触发次数）与 profitGuardArmedRounds（武装轮数），两者都答不了它，第 3 轮就只能继续拍脑袋调参。
//
// 口径与后端 `db/ai_auto_repositories._summarize_pairs` **公式逐字对应**（两份实现是既有惯例：
// 该函数的 docstring 就写着「口径同前端 summarizeRoundTrips」）：
// - 分子 realized = exit.pnl：executor 的净已实现盈亏，**含**手续费。
// - 分母 peak = exit.peakPnlUsd：保护层本地派生的峰值浮盈，**不含**手续费与资金费。
// - 仅 `peak > 0` 的往返进分母；`peak` 缺失或 ≤0（全程未浮盈）计入 noPeakCount 单列。
//
// 由此口径本身让**小额往返的捕获率系统性偏低**（手续费占比大），UI 必须标注；同时它给出一条可验证的
// 锚：无手续费、无加仓的理想情形下，保护层触发的往返捕获率恒 `= 1 − 有效回吐阈`。

import { buildTradeTrailSegments } from "./tradeTrails";
import type { Trade } from "./types";

export interface ProfitCaptureSummary {
  /** Σ 有效峰值浮盈（仅计 peak > 0 的往返）。 */
  peakPnlUsd: number;
  /** 对应那些往返的 Σ 已实现盈亏（含手续费）。 */
  capturedPnlUsd: number;
  /** capturedPnlUsd / peakPnlUsd；无有效样本 → 0。 */
  captureRatio: number;
  /** 进分母的往返数（样本诚实性：UI 据此在样本不足时不给百分比）。 */
  capturePairs: number;
  /** peak 缺失或 ≤0 的往返数（保护层未装配 / 老库缺列 / 全程未浮盈）。 */
  noPeakCount: number;
}

const EMPTY: ProfitCaptureSummary = {
  peakPnlUsd: 0,
  capturedPnlUsd: 0,
  captureRatio: 0,
  capturePairs: 0,
  noPeakCount: 0,
};

/**
 * 单个标的成交流 → 捕获率汇总。配对复用 `buildTradeTrailSegments` 的**因果配对**（按落库/到达序、
 * 单持仓 open→close），与 `autoTradePnl.buildRoundTrips`、后端 `_pair_round_trips` 同一条规则——
 * 绝不按 ts 排序（AI 自动交易的 trades.ts 由两套时钟写入，按 ts 配对会丢交易，`5173d88` 的教训）。
 */
export function summarizeProfitCapture(trades: Trade[]): ProfitCaptureSummary {
  const real = trades.filter((t) => !t.seeded);
  const segments = buildTradeTrailSegments(real);
  if (segments.length === 0) return { ...EMPTY };
  return segments.reduce<ProfitCaptureSummary>((acc, seg) => {
    const peak = seg.exit.peakPnlUsd;
    if (peak == null || !Number.isFinite(peak) || peak <= 0) {
      return { ...acc, noPeakCount: acc.noPeakCount + 1 };
    }
    const peakPnlUsd = acc.peakPnlUsd + peak;
    const capturedPnlUsd = acc.capturedPnlUsd + seg.exit.pnl;
    return {
      peakPnlUsd,
      capturedPnlUsd,
      captureRatio: peakPnlUsd > 0 ? capturedPnlUsd / peakPnlUsd : 0,
      capturePairs: acc.capturePairs + 1,
      noPeakCount: acc.noPeakCount,
    };
  }, { ...EMPTY });
}

/** 跨成员合并（逐成员配对、绝不跨 instId，与 autoTradePnl.buildLedger 同一不变式）。 */
export function summarizeProfitCaptureAcross(
  members: { trades: Trade[] }[],
): ProfitCaptureSummary {
  return members
    .map((m) => summarizeProfitCapture(m.trades))
    .reduce<ProfitCaptureSummary>((acc, part) => {
      const peakPnlUsd = acc.peakPnlUsd + part.peakPnlUsd;
      const capturedPnlUsd = acc.capturedPnlUsd + part.capturedPnlUsd;
      return {
        peakPnlUsd,
        capturedPnlUsd,
        captureRatio: peakPnlUsd > 0 ? capturedPnlUsd / peakPnlUsd : 0,
        capturePairs: acc.capturePairs + part.capturePairs,
        noPeakCount: acc.noPeakCount + part.noPeakCount,
      };
    }, { ...EMPTY });
}

/** 最小样本门禁（沿用统计看板既有范式）：样本不足时不给百分比，绝不用 1~2 笔冒充统计结论。 */
export const PROFIT_CAPTURE_MIN_PAIRS = 3;

/**
 * 捕获率展示串：`68.2%（12 笔有峰值样本 / 共 15 笔）`；样本 < 3 → `样本不足`。
 * 口径提示（分母不含手续费、加过仓的往返为重估值）由渲染层以 hint 承载，不塞进这一行。
 */
export function formatProfitCapture(summary: ProfitCaptureSummary): string {
  const total = summary.capturePairs + summary.noPeakCount;
  if (summary.capturePairs < PROFIT_CAPTURE_MIN_PAIRS) {
    return total === 0 ? "样本不足" : `样本不足（${summary.capturePairs}/${total} 笔有峰值）`;
  }
  return `${+(summary.captureRatio * 100).toFixed(1)}%（${summary.capturePairs} 笔有峰值样本 / 共 ${total} 笔）`;
}

/** 捕获率口径提示（UI 必须显示；不写就是误导）。 */
export const PROFIT_CAPTURE_HINT =
  "捕获率 = 已实现盈亏 / 峰值浮盈。分母不含手续费、分子含，故小额往返会偏低；"
  + "持仓期间加过仓的往返，峰值为按最终均价重估值。";

// ---------- 盈转亏与收益锁（ai-trade-profit-lock-and-entry-unblock 轴 C-3）----------

/** 锁平后「立即同向重开」的判定窗口（秒）。后端 `ai_auto_repositories.LOCK_REENTRY_WINDOW_S` 同值。 */
export const LOCK_REENTRY_WINDOW_S = 1800;

export interface GreenToRedSummary {
  /** 曾浮盈（peak > 0）却以亏损（pnl ≤ 0）离场的往返数。 */
  greenToRedCount: number;
  /** 这些往返的 Σ(peak − pnl)：从峰值一路回吐掉的钱。 */
  greenToRedGivebackUsd: number;
  /** 收益锁离场的往返数（reason 以 `profit_lock_` 开头）。 */
  lockExitCount: number;
  /** 锁平后下一笔同向、且开仓距锁平 ≤ 窗口的次数（手续费失血的观测量）。 */
  lockReentryCount: number;
}

const EMPTY_G2R: GreenToRedSummary = {
  greenToRedCount: 0, greenToRedGivebackUsd: 0, lockExitCount: 0, lockReentryCount: 0,
};

/**
 * 单标的成交流 → 盈转亏四键。判据与后端 `_green_to_red_keys` 逐条对应；实时页的回吐额沿用
 * 本文件 `summarizeProfitCapture` 的分子口径（`exit.pnl`），历史页一律取后端聚合值。
 */
export function summarizeGreenToRed(trades: Trade[]): GreenToRedSummary {
  const segments = buildTradeTrailSegments(trades.filter((t) => !t.seeded));
  const out = { ...EMPTY_G2R };
  segments.forEach((seg, idx) => {
    const peak = seg.exit.peakPnlUsd;
    if (peak != null && Number.isFinite(peak) && peak > 0 && seg.exit.pnl <= 0) {
      out.greenToRedCount += 1;
      out.greenToRedGivebackUsd += peak - seg.exit.pnl;
    }
    if (!(seg.exit.reason || "").startsWith("profit_lock_")) return;
    out.lockExitCount += 1;
    const next = segments[idx + 1];
    const gap = next ? next.entry.ts - seg.exit.ts : -1;
    if (next && next.entry.side === seg.entry.side && gap >= 0 && gap <= LOCK_REENTRY_WINDOW_S) {
      out.lockReentryCount += 1;
    }
  });
  return out;
}

/** 跨成员合并（逐成员判定、绝不跨标的相邻）。 */
export function summarizeGreenToRedAcross(members: { trades: Trade[] }[]): GreenToRedSummary {
  return members.map((m) => summarizeGreenToRed(m.trades)).reduce((acc, part) => ({
    greenToRedCount: acc.greenToRedCount + part.greenToRedCount,
    greenToRedGivebackUsd: acc.greenToRedGivebackUsd + part.greenToRedGivebackUsd,
    lockExitCount: acc.lockExitCount + part.lockExitCount,
    lockReentryCount: acc.lockReentryCount + part.lockReentryCount,
  }), { ...EMPTY_G2R });
}
