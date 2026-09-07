import { describe, expect, it } from "vitest";
import fs from "node:fs";
import path from "node:path";
import {
  REALIZED_MIN_TRIPS,
  VERDICT_TONES,
  costBasisText,
  coverageText,
  fmtBps,
  fmtOrDash,
  fmtRate,
  holdDurationText,
  payoffText,
  riskDistributionText,
  readRMultiple,
  realizedCoverageText,
  realizedRows,
  travelToCostText,
  type AiTradePooled,
  type AiTradeRealizedStats,
} from "./aiTradeProfitability";

const POOLED: AiTradePooled = {
  roundTrips: 193,
  netPnlUsd: -7.4185,
  grossPnlUsd: -3.5437,
  feeUsd: 3.8748,
  winRate: 0.4041,
  avgWinUsd: 0.2092,
  avgLossUsd: -0.2064,
  payoff: 1.014,
  breakevenPayoff: 1.474,
  expectancyUsd: -0.0384,
  expectancyR: -0.011,
  rSamples: 191,
  tStat: -1.446,
  medianHoldSeconds: 1271,
  medianMoveBps: 13.9,
  medianCostBps: 7.01,
  travelToCostRatio: 1.98,
  feeCoverage: 0.1088,
  feeTrips: 21,
  moveSamples: 193,
  travelComparisonTrips: 21,
  travelComparisonCoverage: 0.1088,
  travelMedianMoveBps: 13.9,
  travelMedianFeeBps: 7.01,
  costBasisAssumedTrips: 172,
};

const REALIZED: AiTradeRealizedStats = {
  trips: 12,
  medianMoveBps: 13.9,
  medianRealizedNetBps: -1.2,
  medianRMultiple: -0.01,
  reachedOneRShare: 0,
  medianStopAtrMult: 17.8,
  edgeErrorMedianBps: null,
  costErrorMedianBps: 0.4,
  estimateCoverage: 0.83,
  feeCoverage: 1,
};

describe("算不出一律「—」，绝不显示 0", () => {
  it("四个格式化器在 null / NaN 上都退化为破折号", () => {
    for (const fmt of [fmtOrDash, fmtBps, fmtRate]) {
      expect(fmt(null)).toBe("—");
      expect(fmt(undefined)).toBe("—");
      expect(fmt(Number.NaN)).toBe("—");
    }
    expect(fmtOrDash(0)).toBe("0.00"); // 真实的 0 照常显示——它与「算不出」是两个结论
  });
});

describe("覆盖率必显（G-8）", () => {
  it("coverage < 1 时给出带分母的一句话", () => {
    expect(coverageText(21, 0.1088, 193)).toBe("覆盖 21 / 193 笔（10.9%）");
  });

  it("coverage == 1 时返回 null（不写「覆盖 100%」这种噪音）", () => {
    expect(coverageText(193, 1, 193)).toBeNull();
  });

  it("coverage 为 null 时仍要说——「不知道覆盖率」也是信息", () => {
    expect(coverageText(0, null, 193)).toContain("覆盖 0 / 193 笔");
  });
});

describe("赔率成对、位移/成本带分母（G-9）", () => {
  it("赔率必须与盈亏平衡赔率同时出现", () => {
    expect(payoffText(POOLED)).toBe("1.014 / 需 1.474");
  });

  it("travelToCostRatio 与 feeCoverage 同行下发", () => {
    const text = travelToCostText(POOLED);
    expect(text).toContain("1.98×");
    expect(text).toContain("21 笔同笔尺度");
    expect(text).toContain("往返手续费");
    expect(text).toContain("不含资金费");
    expect(text).toContain("10.9%");
  });

  it("比值算不出时说「成本样本不足」，绝不显示一个 0.0 bps 的成本", () => {
    const text = travelToCostText({ ...POOLED, travelToCostRatio: null, feeTrips: 2 });
    expect(text).toContain("手续费比较样本不足");
    expect(text).not.toContain("0.0 bps");
  });
});

describe("中位持仓时长带分母上屏（D3b）", () => {
  it("有样本时给出人话时长与它的分母", () => {
    const text = holdDurationText(POOLED);
    expect(text).toContain("21 分钟");
    expect(text).toContain("（193 笔）");
  });

  it("medianHoldSeconds 为 null ⇒ 破折号，绝不编一个 0 秒", () => {
    expect(holdDurationText({ ...POOLED, medianHoldSeconds: null })).toContain("—");
  });
});

describe("口径推定计数（[P1-12]）", () => {
  it("有推定样本时如实说出分子分母", () => {
    expect(costBasisText(172, 193)).toContain("172 / 193");
  });

  it("零推定 ⇒ null（不写「0 笔靠推定」）", () => {
    expect(costBasisText(0, 193)).toBeNull();
  });
});

describe("会话级已实现三项（[P1-2] 两处读点共用同一套格式化器）", () => {
  it("样本不足即整段不渲染", () => {
    expect(realizedRows({ ...REALIZED, trips: REALIZED_MIN_TRIPS - 1 })).toBeNull();
    expect(realizedRows(null)).toBeNull();
  });

  it("样本足够时三项齐备且首项自带分母", () => {
    const rows = realizedRows(REALIZED)!;
    expect(rows).toHaveLength(3);
    expect(rows[0].label).toContain("12 笔");
    expect(rows[1].value).toBe("-0.010");
  });

  it("两个覆盖率并排出现", () => {
    expect(realizedCoverageText(REALIZED)).toBe("带门禁估计 83.0% · 带费率记录 100.0%");
  });
});

describe("R 倍数的唯一读点（两条通道落点不同）", () => {
  it("历史通道读顶层 rMultiple", () => {
    expect(readRMultiple({ rMultiple: -0.008 })).toBe(-0.008);
  });

  it("实时通道读 explain.meta.rMultiple —— executor 成交 dict 的顶层键集是冻结的", () => {
    expect(readRMultiple({ explain: { meta: { rMultiple: 0.21 } } })).toBe(0.21);
  });

  it("两边都没有 / 非有限 ⇒ null（绝不是 0）", () => {
    expect(readRMultiple({})).toBeNull();
    expect(readRMultiple({ rMultiple: null, explain: { meta: {} } })).toBeNull();
    expect(readRMultiple({ rMultiple: Number.NaN })).toBeNull();
    expect(readRMultiple({ rMultiple: 0 })).toBe(0); // 真实的 0 是一个结论，照常返回
  });
});

describe("结论表色调映射齐备", () => {
  it("五个结论码各有一个色调", () => {
    for (const code of [
      "positive_significant",
      "positive_not_significant",
      "no_edge_yet",
      "negative_significant",
      "insufficient_samples",
      "insufficient_evidence",
    ]) {
      expect(VERDICT_TONES[code]).toBeDefined();
    }
  });
});

it("旧后端缺少同笔分母时不展示旧混合比值", () => {
  const old = { ...POOLED, travelComparisonTrips: undefined };
  expect(travelToCostText(old)).toContain("旧版未提供同笔尺度");
  expect(travelToCostText(old)).not.toContain("1.98×");
});

it("风险子样本分别保留自己的分母", () => {
  const text = riskDistributionText({
    rSamples: 3, rP10: -.1, rP50: .1, rP90: .2,
    reachedOneRShare: 0, withinFifthRShare: .5,
    plannedRrMedian: 2, plannedRrSamples: 2,
    stopUtilizationP50: .2, medianStopAtrMult: 1,
    stopAtrCoverage: .25, stopAtrSamples: 1,
  });
  expect(text).toContain("计划盈亏比中位 2.00（2 笔）");
  expect(text).toContain("1.0×ATR（1 笔，覆盖 25.0%）");
});

describe("文案禁令（G-10）", () => {
  // core.autocrlf=true：正则一律写 \r?\n，否则干净检出上恒红。
  const BANNED = /(已校准|经回测验证|预期年化|稳定盈利|保证)/;
  const FILES = [
    "lib/aiTradeProfitability.ts",
    "components/Agent/aiTradeStats/ProfitabilityCard.tsx",
    "components/Agent/aiTradeStats/CalibrationCard.tsx",
  ];

  it("三个前端产物都不得出现收益承诺词", () => {
    const root = path.resolve(__dirname, "..");
    for (const rel of FILES) {
      const source = fs.readFileSync(path.join(root, rel), "utf8").replace(/\r?\n/g, "\n");
      expect(BANNED.test(source), `${rel} 含禁用词`).toBe(false);
    }
  });
});
