import { describe, expect, it } from "vitest";
import { describeExecuted } from "./aiTrade";
import { describePreSubmitCheck } from "./aiTradePreSubmit";

/**
 * 执行轨迹说清「为什么没开」（profit-lock C-5）。合入前下单前复检拦下的开仓只显示「已跳过」，
 * 生产最新组 172 次丢弃在界面上一个字都看不出来。
 */
const SKIPPED = { skipped: true, traded: false, positionAfter: "flat" };

describe("describePreSubmitCheck", () => {
  it("成本复检：把上升量与容忍度说出来", () => {
    const view = describePreSubmitCheck({
      ...SKIPPED,
      preSubmitCheck: {
        kind: "cost", frozenBps: 10.41, currentBps: 11.9, deltaBps: 1.49, toleranceBps: 1,
      },
    });
    expect(view?.label).toBe("未开仓：执行前成本上升 1.49 bps > 容忍 1");
    expect(view?.detail).toContain("10.41 bps 升到 11.9 bps");
  });

  it("价格漂移：放弃追单", () => {
    const view = describePreSubmitCheck({
      ...SKIPPED,
      preSubmitCheck: { kind: "drift", driftBps: 31.2, limitBps: 24, refPx: 100, nowPx: 100.312 },
    });
    expect(view?.label).toBe("未开仓：价格已不利移动 31.2 bps > 24，放弃追单");
  });

  it("身份变化：列出变了什么（中文）", () => {
    const view = describePreSubmitCheck({
      ...SKIPPED,
      preSubmitCheck: { kind: "identity", changed: ["positionSeq", "restingClOrdId"] },
    });
    expect(view?.label).toBe("未开仓：研判期间持仓/挂单已变化（持仓已开平、挂单）");
  });

  it("旧会话只有原因码 ⇒ 回落到原因标签", () => {
    const view = describePreSubmitCheck({
      ...SKIPPED, executionContextChanged: "pre_submit_price_drift",
    });
    expect(view?.label).toBe("未开仓：价格已不利移动");
  });

  it("既不是复检也没有原因码 ⇒ null，describeExecuted 仍是「已跳过」逐字不变", () => {
    expect(describePreSubmitCheck(SKIPPED)).toBeNull();
    expect(describePreSubmitCheck(null)).toBeNull();
    expect(describeExecuted("open_long", SKIPPED)).toEqual({ label: "已跳过", tone: "muted" });
  });

  it("describeExecuted 的 skipped 分支委托：label 带读数，完整说明进 title", () => {
    const view = describeExecuted("open_long", {
      ...SKIPPED,
      preSubmitCheck: {
        kind: "cost", frozenBps: 10.41, currentBps: 11.9, deltaBps: 1.49, toleranceBps: 1,
      },
    });
    expect(view.label).toContain("执行前成本上升 1.49 bps");
    expect(view.title).toContain("往返成本");
  });
});
