import { describe, expect, it } from "vitest";
import {
  describeSource,
  describeTradability,
  firstFeasibleAlternative,
  formatBps,
  formatRate,
  readVetoRate,
} from "./aiTradeTradability";
import type { AiTradeTradabilityResponse } from "./aiTradeTypes";

function wire(over: Partial<AiTradeTradabilityResponse> = {}): AiTradeTradabilityResponse {
  return {
    instId: "BTC-USDT-SWAP",
    bar: "1m",
    preset: "balanced",
    level: "infeasible",
    samples: 200,
    source: "live",
    asOfTs: 1786600000,
    atrBps: { p10: 2.9, p50: 5.6, p90: 11.3 },
    costBps: { market: 13.0, limit: 8.5 },
    costConfidence: { market: "degraded", limit: "degraded" },
    degradedMultApplied: 1.2,
    requiredBps: { market: 23.4, limit: 15.3 },
    vetoRate: { market: 0.986, limit: 0.926 },
    stopBps: { p50: 11.2 },
    stopInsideCostRate: { market: 0.623, limit: 0.287 },
    alternatives: [],
    headline: "后端下发的一句话结论",
    advice: "后端下发的建议",
    disclaimer: "以上是确定性统计，不是收益预测",
    ...over,
  };
}

describe("describeTradability", () => {
  it("四档 level 各有中文与色调", () => {
    expect(describeTradability(wire({ level: "ok" }))?.label).toBe("可交易");
    expect(describeTradability(wire({ level: "marginal" }))?.tone).toBe("accent");
    expect(describeTradability(wire({ level: "infeasible" }))?.tone).toBe("short");
    expect(describeTradability(wire({ level: "unknown" }))?.tone).toBe("muted");
  });

  // G-5：**前端绝不重算判据**。给一个与数字明显矛盾的 level，渲染必须服从后端。
  it("给一个与数字矛盾的 level，渲染仍服从后端下发的那一份", () => {
    // 否决率 0（数字上应当是 ok）却下发 infeasible ⇒ 前端照样按 infeasible 渲染。
    const view = describeTradability(wire({ level: "infeasible", vetoRate: { market: 0, limit: 0 } }));
    expect(view?.level).toBe("infeasible");
    expect(view?.label).toBe("不可行");
    expect(view?.needsConfirm).toBe(true);
    // 反向：否决率 99% 却下发 ok ⇒ 同样服从后端，且**不要求二次确认**。
    const ok = describeTradability(wire({ level: "ok", vetoRate: { market: 0.99, limit: 0.99 } }));
    expect(ok?.level).toBe("ok");
    expect(ok?.needsConfirm).toBe(false);
  });

  it("headline / advice 一个字都不重写", () => {
    const view = describeTradability(wire());
    expect(view?.headline).toBe("后端下发的一句话结论");
    expect(view?.advice).toBe("后端下发的建议");
  });

  it("只有 infeasible 需要二次确认（启动按钮永不禁用是组件侧的纪律）", () => {
    for (const level of ["ok", "marginal", "unknown"] as const) {
      expect(describeTradability(wire({ level }))?.needsConfirm).toBe(false);
    }
  });

  it("缺席 wire ⇒ null ⇒ 整段不渲染", () => {
    expect(describeTradability(null)).toBeNull();
    expect(describeTradability(undefined)).toBeNull();
  });

  it("status 瘦身投影与完整响应读的是同一个否决率", () => {
    expect(readVetoRate(wire())).toBe(0.986);
    expect(readVetoRate({ level: "infeasible", vetoRateMarket: 0.5 })).toBe(0.5);
    expect(readVetoRate(null)).toBeNull();
  });
});

describe("格式化：算不出一律 null，绝不写 0%", () => {
  it("formatRate / formatBps", () => {
    expect(formatRate(0.986)).toBe("98.6%");
    expect(formatRate(null)).toBeNull();
    expect(formatRate(Number.NaN)).toBeNull();
    expect(formatBps(13)).toBe("13.0 bps");
    expect(formatBps(undefined)).toBeNull();
  });

  it("缓存来源必须说出来（探针无条件读缓存是一次有意偏离）", () => {
    expect(describeSource("cache", 1786600000)).toContain("本地缓存");
    expect(describeSource("live", null)).toContain("实时行情");
    expect(describeSource(null, 1)).toBeNull();
  });
});

describe("firstFeasibleAlternative", () => {
  it("取第一个可行的更长周期；全不可行 ⇒ null（绝不推荐一个同样开不出单的周期）", () => {
    expect(
      firstFeasibleAlternative(
        wire({
          alternatives: [
            { bar: "5m", vetoRateMarket: 0.9, costBpsMarket: 13, level: "infeasible" },
            { bar: "15m", vetoRateMarket: 0.2, costBpsMarket: 13, level: "marginal" },
            { bar: "1H", vetoRateMarket: 0.01, costBpsMarket: 13.2, level: "ok" },
          ],
        }),
      ),
    ).toEqual({ bar: "15m", vetoRateMarket: 0.2 });
    expect(
      firstFeasibleAlternative(
        wire({
          alternatives: [
            { bar: "5m", vetoRateMarket: 0.9, costBpsMarket: 13, level: "infeasible" },
          ],
        }),
      ),
    ).toBeNull();
    expect(firstFeasibleAlternative(null)).toBeNull();
  });
});
