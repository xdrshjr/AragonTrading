import { describe, expect, it } from "vitest";
import {
  blockedRows,
  formatConversion,
  funnelStages,
  hasFunnelSamples,
  isFunnelClosed,
  layerLabel,
  sumBlocked,
} from "./aiTradeFunnel";
import type { AiTradeOpenFunnel } from "./aiTradeTypes";

const LAYERS = [
  "baseRuleMaBand",
  "horizon",
  "profitCooldown",
  "turnover",
  "exchangeBackstop",
  "drawdownBrake",
  "consensus",
  "confidence",
  "edgeGate",
  "reflection",
  "other",
];

function funnel(over: Partial<AiTradeOpenFunnel> = {}): AiTradeOpenFunnel {
  return {
    intents: 12,
    blocked: { edgeGate: 11, consensus: 1 },
    topBlocker: { key: "edgeGate", label: "最小 edge 门禁", count: 11 },
    llmDegraded: 0,
    sizingZeroed: 0,
    submitted: 0,
    filled: 0,
    conversion: 0,
    layers: LAYERS,
    scope: "本会话全程累计",
    ...over,
  };
}

describe("零样本绝不造百分比（G-12）", () => {
  it("intents == 0 ⇒ 没有样本、没有比例、conversion 为 null", () => {
    const empty = funnel({ intents: 0, blocked: {}, conversion: null });
    expect(hasFunnelSamples(empty)).toBe(false);
    expect(formatConversion(empty)).toBeNull();
    expect(funnelStages(empty).every((s) => s.ratio === null)).toBe(true);
  });

  it("有样本后才给比例", () => {
    expect(hasFunnelSamples(funnel())).toBe(true);
    expect(formatConversion(funnel({ conversion: 0.25 }))).toBe("25.0%");
  });

  it("缺席 funnel ⇒ 空几何（整卡不渲染由组件门控）", () => {
    expect(funnelStages(null)).toEqual([]);
    expect(blockedRows(undefined)).toEqual([]);
    expect(formatConversion(null)).toBeNull();
  });
});

describe("漏斗几何", () => {
  it("五格顺序固定：意愿 → 被拦 → 解到 0 → 委托 → 成交", () => {
    expect(funnelStages(funnel()).map((s) => s.key)).toEqual([
      "intents",
      "blocked",
      "sizingZeroed",
      "submitted",
      "filled",
    ]);
  });

  it("「被拦」是各层之和，且 sizingZeroed 单列（它不是一道闸）", () => {
    const rows = funnel({ sizingZeroed: 2, blocked: { edgeGate: 5 } });
    expect(sumBlocked(rows)).toBe(5);
    const stages = funnelStages(rows);
    expect(stages.find((s) => s.key === "blocked")?.count).toBe(5);
    expect(stages.find((s) => s.key === "sizingZeroed")?.count).toBe(2);
  });

  it("比例夹在 [0,1]（后端异常也不该画出一条溢出屏幕的条）", () => {
    const stages = funnelStages(funnel({ intents: 2, blocked: { edgeGate: 99 } }));
    expect(stages.find((s) => s.key === "blocked")?.ratio).toBe(1);
  });
});

describe("闭合不变式", () => {
  it("正常数据闭合", () => {
    expect(isFunnelClosed(funnel({ intents: 12, blocked: { edgeGate: 11 }, submitted: 1 }))).toBe(
      true,
    );
  });

  it("和超过分母 ⇒ 判为不闭合（呈现层据此警示，绝不悄悄画一张错图）", () => {
    expect(
      isFunnelClosed(funnel({ intents: 2, blocked: { edgeGate: 3 }, submitted: 1 })),
    ).toBe(false);
  });

  it("缺席 funnel 视为闭合（没有图就没有不闭合的图）", () => {
    expect(isFunnelClosed(null)).toBe(true);
  });
});

describe("逐层明细", () => {
  it("按后端下发的决策链顺序排列，只列真的拦过的层", () => {
    const rows = blockedRows(funnel({ blocked: { edgeGate: 3, baseRuleMaBand: 1, horizon: 0 } }));
    expect(rows.map((r) => r.key)).toEqual(["baseRuleMaBand", "edgeGate"]);
    expect(rows[0].label).toBe("基础策略·MA 通道");
  });

  it("未知层 key 原样显示（后端新增层时不至于变成空白）", () => {
    expect(layerLabel("brandNewGate")).toBe("brandNewGate");
    expect(layerLabel("edgeGate")).toBe("最小 edge 门禁");
  });
});
