import { describe, expect, it } from "vitest";
import {
  AXIS_LABEL_CN,
  CLIP_MODE_CN,
  backtestsPerIteration,
  blockingGate,
  columnsForKind,
  formatParamValue,
  gateRows,
  isStrategyAxis,
  shouldShowCoverage,
  sideRows,
  yieldSentence,
  yieldSentences,
} from "./discoveryStrategy";
import type { DiscoveryCandidateRow, DiscoveryGateRow } from "./discoveryTypes";

function row(over: Partial<DiscoveryCandidateRow> = {}): DiscoveryCandidateRow {
  return {
    kind: "strategy_param",
    genomeHash: "h",
    exprText: "",
    describe: "暴涨动量突破 · 突破窗口 34",
    mechanism: "breakout",
    source: "seed",
    verdict: "kept",
    rejectReason: "",
    createdTs: 0,
    metrics: {
      is: { sharpe: 1.43, totalReturn: 0.31, maxDrawdown: -0.18, tradeCount: 96 },
      oos: { sharpe: 0.92 },
    },
    ...over,
  } as DiscoveryCandidateRow;
}

describe("轴与口径", () => {
  it("三条策略轴被识别，因子轴不是", () => {
    expect(isStrategyAxis("strategy_param")).toBe(true);
    expect(isStrategyAxis("factor_combo")).toBe(true);
    expect(isStrategyAxis("strategy_combo")).toBe(true);
    expect(isStrategyAxis("factor")).toBe(false);
  });

  it("两种切片口径各有一段说明，且措辞不同（BL-3：不得用一套话术盖住两种口径）", () => {
    expect(CLIP_MODE_CN.gated).toContain("只暖机不交易");
    expect(CLIP_MODE_CN.rebased).toContain("权益重基");
    expect(CLIP_MODE_CN.rebased).toContain("不可直接比较");
    expect(CLIP_MODE_CN.gated).not.toBe(CLIP_MODE_CN.rebased);
  });
});

describe("指标格式化", () => {
  it("缺字段一律显示破折号，绝不显示 NaN 或 0", () => {
    const rows = sideRows(undefined);
    expect(rows.every((item) => item.value === "—")).toBe(true);
  });

  it("百分比与小数各按各的位数", () => {
    const rows = sideRows({ sharpe: 1.4321, totalReturn: 0.3149, tradeCount: 96 });
    expect(rows.find((item) => item.label === "夏普")?.value).toBe("1.43");
    expect(rows.find((item) => item.label === "总收益")?.value).toBe("31.49%");
    expect(rows.find((item) => item.label === "平仓笔数")?.value).toBe("96");
  });

  it("参数值格式化覆盖布尔 / 整数 / 小数 / 缺失", () => {
    expect(formatParamValue(true)).toBe("是");
    expect(formatParamValue(34)).toBe("34");
    expect(formatParamValue(0.0250001)).toBe("0.025");
    expect(formatParamValue(undefined)).toBe("—");
  });
});

describe("闸位阶梯", () => {
  const gates: DiscoveryGateRow[] = [
    { id: "S1", label: "样本量", value: 40, gate: 20, passed: true },
    { id: "S6", label: "样本外留存", value: -0.2, gate: 0.57, passed: false },
  ];

  it("卡住的那条被找出来，且中文原因非空", () => {
    expect(blockingGate(gates)?.id).toBe("S6");
    const rows = gateRows(gates);
    expect(rows[0].reasonCn).toBe("");
    expect(rows[1].reasonCn).toBe("样本外崩塌");
    expect(rows[1].reasonCn).not.toBe("S6");
  });

  it("空数组不炸", () => {
    expect(blockingGate(undefined)).toBeNull();
    expect(gateRows(undefined)).toEqual([]);
  });
});

describe("按轴列定义", () => {
  it("因子轴给 IC/IR 口径，策略轴给夏普/回撤口径", () => {
    expect(columnsForKind("factor").map((c) => c.key)).toEqual([
      "isIc", "oosIc", "ir", "turnover",
    ]);
    expect(columnsForKind("strategy_param").map((c) => c.key)).toEqual([
      "isSharpe", "isReturn", "oosSharpe", "drawdown", "trades",
    ]);
  });

  it("混排时逐行标注口径——IC 与夏普不可比，不能当成同一列读", () => {
    const columns = columnsForKind("");
    expect(columns[0].value(row())).toBe("夏普 1.43");
    const factorRow = row({ kind: "factor" });
    factorRow.metrics = { is: { rankIc: 0.0612 } } as DiscoveryCandidateRow["metrics"];
    expect(columns[0].value(factorRow)).toBe("IC 0.0612");
  });
});

describe("让渡文案（BL-9 的可见性）", () => {
  it("一句话里必须同时有：让了多少、让给了谁、为什么", () => {
    const text = yieldSentence("strategy_combo", {
      count: 12,
      reason: "upstream_pool_too_small",
      toAxis: "strategy_param",
    });
    expect(text).toContain("12");
    expect(text).toContain(AXIS_LABEL_CN.strategy_combo);
    expect(text).toContain(AXIS_LABEL_CN.strategy_param);
    expect(text).toContain("常规策略池不足");
    expect(text).not.toContain("upstream_pool_too_small");
  });

  it("没有接收方时也如实说清，而不是留一句半截话", () => {
    const text = yieldSentence("factor_combo", { count: 5, reason: "member_catalog_too_small", toAxis: "" });
    expect(text).toContain("本轮无可接收的轴");
  });

  it("批量转换保留轴 key 供 React key 使用", () => {
    const list = yieldSentences({ strategy_combo: { count: 1, reason: "x", toAxis: "factor" } });
    expect(list).toHaveLength(1);
    expect(list[0].axis).toBe("strategy_combo");
  });

  it("空输入返回空数组", () => {
    expect(yieldSentences(undefined)).toEqual([]);
  });
});

describe("覆盖图可见性（P2-11）", () => {
  it("只有两条组合轴时整块隐藏——画出来就是一根 100% 的柱子", () => {
    expect(shouldShowCoverage(["factor_combo", "strategy_combo"])).toBe(false);
    expect(shouldShowCoverage(["factor"])).toBe(true);
    expect(shouldShowCoverage(["strategy_param"])).toBe(true);
    expect(shouldShowCoverage([])).toBe(true);
  });
});

describe("回测次数预估", () => {
  it("按轴代价加总（因子轴不产生回测）", () => {
    const info = {
      factor: { backtestsPerCandidate: 0 },
      strategy_param: { backtestsPerCandidate: 2 },
    };
    expect(backtestsPerIteration(["factor", "strategy_param"], 40, info)).toBe(40);
    expect(backtestsPerIteration(["factor"], 40, info)).toBe(0);
    expect(backtestsPerIteration([], 40, info)).toBe(0);
  });
});
