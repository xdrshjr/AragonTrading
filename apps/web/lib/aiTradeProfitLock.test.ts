import { describe, expect, it } from "vitest";
import {
  AI_TRADE_PROFIT_LOCK_DEFAULTS,
  describeProfitLock,
  fmtLockPx,
  previewLockThresholds,
} from "./aiTradeProfitLock";
import type { AiTradeProfitLockSnapshot } from "./aiTradeTypes";

/**
 * 收益锁展示纯函数（profit-lock §7.4）：四态文案 / 色调、悬停读数、极小价格式，以及阈值预览与
 * 后端 `profit_lock._thresholds` 的逐项对应（B-T1 同一组数：cost=10、ATR=40 ⇒ 武装 15 bps）。
 */
const BASE: AiTradeProfitLockSnapshot = {
  stage: 0,
  status: "watching",
  unavailableReason: null,
  side: "long",
  entryPx: 77414.9,
  peakPx: 77500,
  mfeBps: 11,
  costBps: 10.4,
  atrBps: 48,
  armBps: 16.8,
  trailArmBps: 38.4,
  lockPx: null,
  lockNetBps: null,
  breachSeconds: 0,
  confirmSeconds: 8,
};

describe("describeProfitLock", () => {
  it("观察态：说清离武装还差多少，muted", () => {
    const view = describeProfitLock(BASE);
    expect(view.tone).toBe("muted");
    expect(view.text).toBe("收益锁 · 浮盈 11 / 需 16.8 bps 武装");
  });

  it("保本锁 / 利润锁 / 暂停判定三态", () => {
    const breakeven = describeProfitLock({
      ...BASE, stage: 1, status: "breakeven", lockPx: 77514, lockNetBps: 2,
    });
    expect(breakeven.tone).toBe("accent");
    expect(breakeven.text).toContain("保本锁 77,514.0");
    expect(breakeven.text).toContain("目标净 +2 bps");

    const trailing = describeProfitLock({
      ...BASE, stage: 2, status: "trailing", lockPx: 77600, lockNetBps: 14.5,
    });
    expect(trailing.tone).toBe("success");
    expect(trailing.text).toBe("收益锁 · 已锁 14.5 bps · 线 77,600.0");

    const stale = describeProfitLock({
      ...BASE, status: "unavailable", unavailableReason: "stale_px",
    });
    expect(stale.tone).toBe("warn");
    expect(stale.text).toBe("收益锁 · 暂停判定（行情暂停更新）");
  });

  it("悬停说明给出公式读数、目标净额的诚实说明与本会话锁平次数", () => {
    const view = describeProfitLock({ ...BASE, unavailableReason: "min_hold" }, 3);
    expect(view.title).toContain("往返成本约 10.4 bps");
    expect(view.title).toContain("浮盈到 16.8 bps");
    expect(view.title).toContain("实际可能略低");
    expect(view.title).toContain("最短持有期");
    expect(view.title).toContain("本会话已锁平 3 次");
    expect(describeProfitLock(BASE).title).not.toContain("锁平");
  });

  it("极小价（SATS 1e-8 量级）用科学计数，不显示成 0.0000", () => {
    expect(fmtLockPx(2.345e-8)).toBe("2.345e-8");
    expect(fmtLockPx(null)).toBe("—");
    const view = describeProfitLock({
      ...BASE, stage: 1, status: "breakeven", lockPx: 2.345e-8, lockNetBps: 2,
    });
    expect(view.text).toContain("2.345e-8");
  });
});

describe("previewLockThresholds", () => {
  it("与后端公式同式：cost=10、ATR=40 ⇒ 武装 max(15, 14, 15)=15，锁利 max(30, 32)=32", () => {
    const preview = previewLockThresholds(10, 40);
    expect(preview?.armBps).toBeCloseTo(15, 10);
    expect(preview?.trailArmBps).toBeCloseTo(32, 10);
  });

  it("武装阈值不低于「含费保本 + 3 bps」（武装瞬间 mark 必在锁线上方）", () => {
    const preview = previewLockThresholds(2, null, { enabled: true, bufferBps: 10 });
    expect(preview?.armBps).toBeCloseTo(15, 10);
  });

  it("锁利阈值不低于保本武装点（阶段 2 必须先经阶段 1）", () => {
    const preview = previewLockThresholds(2, null);
    expect(preview?.armBps).toBeCloseTo(7, 10);
    expect(preview?.trailArmBps).toBeCloseTo(7, 10);
  });

  it("成本不可得 ⇒ null（只说公式，不编数字）", () => {
    expect(previewLockThresholds(null, 40)).toBeNull();
    expect(previewLockThresholds(Number.NaN, 40)).toBeNull();
  });

  it("默认值与后端 settings 逐项一致", () => {
    expect(AI_TRADE_PROFIT_LOCK_DEFAULTS).toEqual({
      enabled: true, armCostMult: 1.5, armAtrMult: 0.35, bufferBps: 2, trailCostMult: 3,
      trailAtrMult: 0.8, keepRatio: 0.5, minHoldSeconds: 60, confirmSeconds: 8,
    });
  });
});
