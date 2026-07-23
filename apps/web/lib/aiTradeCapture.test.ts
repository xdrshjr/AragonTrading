import { describe, expect, it } from "vitest";
import {
  formatProfitCapture,
  PROFIT_CAPTURE_MIN_PAIRS,
  summarizeProfitCapture,
  summarizeProfitCaptureAcross,
} from "./aiTradeCapture";
import type { Trade } from "./types";

function open(ts: number, price = 100): Trade {
  return { ts, side: "long", price, qty: 1, fee: 0, pnl: 0, positionAfter: 1, reason: "open" };
}

function close(ts: number, pnl: number, peakPnlUsd?: number | null): Trade {
  return {
    ts,
    side: "close",
    price: 101,
    qty: 1,
    fee: 0,
    pnl,
    positionAfter: 0,
    reason: "close",
    peakPnlUsd,
  };
}

describe("summarizeProfitCapture", () => {
  it("只把 peak > 0 的往返计入分母，其余单列计数", () => {
    const summary = summarizeProfitCapture([
      open(1), close(2, 0.6, 1),
      open(3), close(4, 0.9, 1),
      open(5), close(6, -0.2, null),      // 保护层未装配 / 快照一路不可用
      open(7), close(8, -0.3, 0),         // 全程未浮盈：peak ≤ 0
    ]);

    expect(summary.peakPnlUsd).toBe(2);
    expect(summary.capturedPnlUsd).toBeCloseTo(1.5, 10);
    expect(summary.captureRatio).toBeCloseTo(0.75, 10);
    expect(summary.capturePairs).toBe(2);
    expect(summary.noPeakCount).toBe(2);
  });

  it("缺 peakPnlUsd 键的老成交不炸也不冒充 0 峰值", () => {
    const legacy: Trade = { ...close(2, 0.5) };
    delete (legacy as { peakPnlUsd?: number | null }).peakPnlUsd;

    const summary = summarizeProfitCapture([open(1), legacy]);

    expect(summary.captureRatio).toBe(0);
    expect(summary.capturePairs).toBe(0);
    expect(summary.noPeakCount).toBe(1);
  });

  it("手续费让捕获率 < 理想值，这是正常态而非缺陷", () => {
    // 理想锚：无手续费、无加仓时捕获率恒 = 1 − 有效回吐阈（这里 0.35 ⇒ 65%）。
    const ideal = summarizeProfitCapture([open(1), close(2, 0.65, 1)]);
    expect(ideal.captureRatio).toBeCloseTo(0.65, 10);

    // 同一笔扣掉双边手续费后必然更低——分母不含费、分子含费。
    const withFees = summarizeProfitCapture([open(1), close(2, 0.6, 1)]);
    expect(withFees.captureRatio).toBeLessThan(ideal.captureRatio);
  });

  it("epoch 内加过仓的往返：峰值是按最终均价重估值，聚合层原样采信不做 rebase", () => {
    // [P1-7] 已知且被接受的口径瑕疵：peak = 当前 qty ×（峰值价 − 漂移后均价），不是账户在峰值
    // 时刻真实拥有过的浮盈。钉住它，避免被后来者当成实现走样「修」掉。
    const summary = summarizeProfitCapture([open(1), close(2, 1.2, 2.4)]);

    expect(summary.peakPnlUsd).toBe(2.4);
    expect(summary.captureRatio).toBeCloseTo(0.5, 10);
    expect(summary.capturePairs).toBe(1);
  });

  it("无成交 / 无往返时全零降级", () => {
    expect(summarizeProfitCapture([])).toEqual({
      peakPnlUsd: 0, capturedPnlUsd: 0, captureRatio: 0, capturePairs: 0, noPeakCount: 0,
    });
    expect(summarizeProfitCapture([open(1)]).capturePairs).toBe(0);   // 悬挂开仓不配对
  });

  it("跨成员合并逐成员配对，绝不跨标的串仓", () => {
    const merged = summarizeProfitCaptureAcross([
      { trades: [open(1), close(2, 0.6, 1)] },
      { trades: [open(3), close(4, 0.4, 1)] },
    ]);

    expect(merged.capturePairs).toBe(2);
    expect(merged.peakPnlUsd).toBe(2);
    expect(merged.captureRatio).toBeCloseTo(0.5, 10);
  });
});

describe("formatProfitCapture", () => {
  it("样本 < 3 显示「样本不足」，不给百分比", () => {
    const few = summarizeProfitCapture([open(1), close(2, 0.6, 1)]);
    expect(few.capturePairs).toBeLessThan(PROFIT_CAPTURE_MIN_PAIRS);
    expect(formatProfitCapture(few)).toContain("样本不足");
    expect(formatProfitCapture(few)).not.toContain("%");
  });

  it("样本充足时给出比率与样本诚实性", () => {
    const summary = summarizeProfitCapture([
      open(1), close(2, 0.6, 1),
      open(3), close(4, 0.7, 1),
      open(5), close(6, 0.8, 1),
      open(7), close(8, -0.1, null),
    ]);

    const text = formatProfitCapture(summary);
    expect(text).toContain("70%");
    expect(text).toContain("3 笔有峰值样本");
    expect(text).toContain("共 4 笔");
  });
});
