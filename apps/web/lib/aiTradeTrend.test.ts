import { describe, expect, it } from "vitest";
import {
  describeTrendAlignment,
  describeTrendVerdict,
  missingLabels,
  trendSampleText,
  trendVerdictTone,
} from "./aiTradeTrend";
import { describeDegradeReason } from "./aiTrade";
import { layerLabel } from "./aiTradeFunnel";
import type { AiTradeTrendStats, AiTradeTrendVerdict } from "./aiTradeTypes";

// 执行周期趋势基调的中文化纯函数（execution-trend-verdict §7.5 F-1 / F-3 / F-4）。
//
// 三条守卫：
// ① **降级态不含任何方向词**——「算不出来」与「中性」是两个不同的结论，混起来会让用户以为
//    系统看多空各半，而实情是它根本没有足够的成分去判；
// ② **绝不臆造数字**：`score` 为 null 时写「不可用」，不写 0；
// ③ 降级码中文化与前后端两张层名表同源（漏加不会报错，只会把英文原始码怼到用户脸上）。

function verdict(over: Partial<AiTradeTrendVerdict> = {}): AiTradeTrendVerdict {
  return {
    bar: "15m",
    asOfTs: 1756700000,
    score: -0.32,
    execScore: -0.61,
    direction: "down",
    bias: "short",
    strength: "strong",
    verdictConfidence: 0.59,
    components: [
      { key: "regime", label: "体制结构", score: -0.72, weight: 0.26, detail: "…" },
      { key: "momentum", label: "动量", score: null, weight: 0.16, detail: "不可用" },
      { key: "htf", label: "高周期背景", score: 1.0, weight: 0.18, detail: "…" },
    ],
    missing: ["momentum"],
    availableWeight: 0.86,
    execAvailableWeight: 0.84,
    higherTimeframe: {
      status: "bullish",
      agree: false,
      bullishBars: ["1D", "4H"],
      bearishBars: [],
    },
    tacticalAlignment: "diverged",
    conflict: "htf_conflict",
    bonusWithheld: true,
    counterGate: { armed: true, minConfidence: 0.65, sizeMult: 0.5 },
    degraded: false,
    degradeReason: null,
    ...over,
  };
}

describe("describeTrendVerdict", () => {
  it("把两个分分开说：执行周期分决定强度、合成分决定方向（F-1 正常态）", () => {
    const text = describeTrendVerdict(verdict());
    expect(text).toContain("15m");
    expect(text).toContain("偏空");
    expect(text).toContain("-0.61");
    expect(text).toContain("只由 15m 证据算出");
    expect(text).toContain("-0.32");
    expect(text).toContain("含高周期调制");
  });

  it("冲突态如实写出「只收紧、不放松」（它绝不降强度、绝不豁免逆势门槛）", () => {
    expect(describeTrendVerdict(verdict())).toContain("只收紧、不放松");
    expect(describeTrendVerdict(verdict({ conflict: "none" }))).not.toContain("只收紧");
  });

  it("降级态**不含任何方向词**，且把缺失成分如实列出（F-1 边缘态一）", () => {
    const text = describeTrendVerdict(
      verdict({ degraded: true, direction: "unknown", bias: "neutral", score: null,
                execScore: null, degradeReason: "insufficient_components" }),
    );
    expect(text).toContain("不可用");
    expect(text).toContain("动量");
    expect(text).not.toContain("偏多");
    expect(text).not.toContain("偏空");
    // 绝不把「没有这个数」画成 0。
    expect(text).not.toContain("0.00");
  });

  it("缺失成分的中文标签取自 wire 自带的 label（前端绝不另抄一份表，F-1 边缘态二）", () => {
    expect(missingLabels(verdict())).toEqual(["动量"]);
    expect(missingLabels(null)).toEqual([]);
  });

  it("空输入返回空串（未装配的会话整段不渲染）", () => {
    expect(describeTrendVerdict(null)).toBe("");
    expect(describeTrendVerdict(undefined)).toBe("");
  });
});

describe("describeTrendAlignment", () => {
  it("冲突时必须写出那条硬边界：高周期不能反转本级别的方向", () => {
    const text = describeTrendAlignment(verdict());
    expect(text).toContain("bullish");
    expect(text).toContain("1D/4H");
    expect(text).toContain("不能反转本级别的方向");
  });

  it("一致 / 不表态 / 不可用三态各有自己的说法", () => {
    expect(
      describeTrendAlignment(
        verdict({ conflict: "none", higherTimeframe: { status: "bearish", agree: true,
                                                       bullishBars: [], bearishBars: ["1D"] } }),
      ),
    ).toContain("与本级别基调一致");
    expect(
      describeTrendAlignment(
        verdict({ conflict: "none", higherTimeframe: { status: "mixed", agree: null,
                                                       bullishBars: [], bearishBars: [] } }),
      ),
    ).toContain("不表态");
    expect(
      describeTrendAlignment(
        verdict({ higherTimeframe: { status: null, agree: null, bullishBars: [],
                                     bearishBars: [] } }),
      ),
    ).toContain("不可用");
  });
});

describe("trendVerdictTone", () => {
  it("只有明确表态的基调才借用方向色；降级 / 中性一律中性色", () => {
    expect(trendVerdictTone(verdict())).toBe("short");
    expect(trendVerdictTone(verdict({ bias: "long" }))).toBe("long");
    expect(trendVerdictTone(verdict({ bias: "neutral" }))).toBe("muted");
    expect(trendVerdictTone(verdict({ degraded: true }))).toBe("muted");
    expect(trendVerdictTone(null)).toBe("muted");
  });
});

describe("trendSampleText", () => {
  function stats(over: Partial<AiTradeTrendStats> = {}): AiTradeTrendStats {
    return {
      armed: true,
      bar: "15m",
      verdictRounds: { up: 31, down: 44, range: 18, unknown: 2 },
      intents: { aligned: 9, counter: 4, unknown: 3 },
      counterBlocked: { confidence: 3, size: 0 },
      counterPassed: 1,
      bonusLanes: { voteApplied: 5, allowanceApplied: 2 },
      filled: { aligned: 6, counter: 1, unknown: 2 },
      netPnlUsd: { aligned: 41.2, counter: -8.7, unknown: 3.1 },
      minSamples: 8,
      ...over,
    };
  }

  it("F-5：样本不足写「样本 a/b」而**不是** 0%（分母只数顺势 + 逆势，unknown 不进）", () => {
    expect(trendSampleText(stats({ filled: { aligned: 2, counter: 1, unknown: 9 } }))).toBe(
      "样本 3/8，尚不足以下结论",
    );
  });

  it("样本足够时返回 null（调用方据此才渲染成绩行）", () => {
    expect(trendSampleText(stats({ filled: { aligned: 6, counter: 3, unknown: 0 } }))).toBeNull();
  });
});

describe("降级码与层名的两处镜像", () => {
  it("F-3：两个细分码中文化且**原样保留参数后缀**，裸码兜底排在最后", () => {
    expect(describeDegradeReason("counter_trend_confidence(conf=0.4/0.65)")).toBe(
      "逆势开仓：与本级别趋势基调相反，置信度未达门槛，本轮观望（平仓不受影响）(conf=0.4/0.65)",
    );
    expect(describeDegradeReason("counter_trend_size(score=-0.32)")).toContain("强制降档后仓位不合法");
    // 裸码兜底今天永不命中（后端只产两个细分码），但它必须排在两个细分码**之后**，
    // 否则 `find(startsWith)` 会让细分码全部退化成兜底文案。
    expect(describeDegradeReason("counter_trend")).toBe(
      "逆势开仓未通过趋势基调门槛，本轮观望（平仓不受影响）",
    );
  });

  it("F-4：`trendBias` 的中文层名在前端表里也在（与后端 LAYER_LABELS 同源）", () => {
    expect(layerLabel("trendBias")).toBe("趋势基调·逆势门槛");
  });
});
