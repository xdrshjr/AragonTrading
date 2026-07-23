import { describe, expect, it } from "vitest";
import type { FactorContribution, Trade } from "@/lib/types";
import {
  contributionBars,
  explainKindLabel,
  explainKindTone,
  resolveExplain,
} from "./tradeExplain";

function factor(over: Partial<FactorContribution>): FactorContribution {
  return {
    factorKey: "mom_roc",
    name: "动量 ROC",
    direction: "long",
    value: 1.0,
    zscore: 1.0,
    weight: 0.5,
    contribution: 0.5,
    share: 0.5,
    isTopDriver: false,
    ...over,
  };
}

function trade(over: Partial<Trade>): Trade {
  return {
    ts: 1000,
    side: "long",
    price: 30000,
    qty: 0.03,
    fee: 0,
    pnl: 0,
    positionAfter: 0.03,
    reason: "复合得分多头",
    ...over,
  };
}

describe("explainKindLabel / explainKindTone", () => {
  it("maps known kinds to stable Chinese labels", () => {
    expect(explainKindLabel("factor_score")).toBe("因子组合");
    expect(explainKindLabel("risk_exit")).toBe("时间止盈");
    expect(explainKindLabel("session_stop")).toBe("会话止盈/止损");
    expect(explainKindLabel("external_close")).toBe("外部平仓");
  });

  it("falls back to a generic label for unknown / missing kind (no blank)", () => {
    expect(explainKindLabel(undefined)).toBe("自动交易");
    // @ts-expect-error 故意传未知 kind 验证回落
    expect(explainKindLabel("mystery")).toBe("自动交易");
  });

  it("uses warn tone for risk / session / external close, neutral otherwise", () => {
    expect(explainKindTone("risk_exit")).toBe("warn");
    expect(explainKindTone("session_stop")).toBe("warn");
    expect(explainKindTone("external_close")).toBe("warn");
    expect(explainKindTone("factor_score")).toBe("neutral");
    expect(explainKindTone(undefined)).toBe("neutral");
  });
});

describe("contributionBars", () => {
  it("sorts by absolute contribution and normalizes the max to pct=100", () => {
    const bars = contributionBars([
      factor({ factorKey: "a", contribution: 0.2 }),
      factor({ factorKey: "b", contribution: -0.8 }),
      factor({ factorKey: "c", contribution: 0.4 }),
    ]);
    expect(bars.map((b) => b.factorKey)).toEqual(["b", "c", "a"]);
    expect(bars[0].pct).toBe(100); // 最大 |贡献| 者归一化到 100
    expect(bars[0].tone).toBe("short"); // 负贡献 → 空头色
    expect(bars[0].signedPct).toBe(-100);
    expect(bars[2].pct).toBeCloseTo(25); // 0.2 / 0.8 * 100
  });

  it("marks top drivers (top 3 by |contribution| with share>=0.15)", () => {
    const bars = contributionBars([
      factor({ factorKey: "a", contribution: 0.6, share: 0.6, isTopDriver: true }),
      factor({ factorKey: "b", contribution: 0.02, share: 0.02, isTopDriver: false }),
    ]);
    expect(bars.find((b) => b.factorKey === "a")?.isTopDriver).toBe(true);
    expect(bars.find((b) => b.factorKey === "b")?.isTopDriver).toBe(false);
  });

  it("returns an empty array for missing or empty factors", () => {
    expect(contributionBars(undefined)).toEqual([]);
    expect(contributionBars([])).toEqual([]);
  });

  it("handles all-zero contributions without dividing by zero", () => {
    const bars = contributionBars([factor({ contribution: 0 })]);
    expect(bars[0].pct).toBe(0);
    expect(bars[0].signedPct).toBe(0);
  });
});

describe("resolveExplain", () => {
  it("returns the structured explain when present", () => {
    const explain = {
      schema: 1 as const,
      kind: "factor_score" as const,
      side: "long" as const,
      headline: "复合得分多头",
      metrics: [],
    };
    expect(resolveExplain(trade({ explain }))).toBe(explain);
  });

  it("synthesizes a fallback explain from reason for legacy trades (no explain)", () => {
    const resolved = resolveExplain(trade({ explain: undefined, reason: "复合得分多头" }));
    expect(resolved.kind).toBe("atomic");
    expect(resolved.headline).toBe("复合得分多头");
    expect(resolved.metrics.some((m) => m.label === "成交价")).toBe(true);
  });

  it("infers risk_exit / session_stop / external_close kind from legacy close reason", () => {
    expect(resolveExplain(trade({ side: "close", reason: "max_hold" })).kind).toBe("risk_exit");
    expect(resolveExplain(trade({ side: "close", reason: "session_stop" })).kind).toBe(
      "session_stop",
    );
    expect(resolveExplain(trade({ side: "close", reason: "external_close" })).kind).toBe(
      "external_close",
    );
  });

  it("adds a pnl metric for legacy close trades", () => {
    const resolved = resolveExplain(trade({ side: "close", reason: "得分落入退出带", pnl: 12.5 }));
    const pnl = resolved.metrics.find((m) => m.label === "本笔盈亏");
    expect(pnl?.value).toContain("+12.50");
    expect(pnl?.tone).toBe("long");
  });
});

describe("profit_guard kind (ai-trade-profit-protection §2.7)", () => {
  it("labels and tones the new kind（与 risk_exit 分列：一个是持仓根数到了，一个是浮盈在回吐）", () => {
    expect(explainKindLabel("profit_guard")).toBe("利润保护");
    expect(explainKindTone("profit_guard")).toBe("warn");
  });

  it("infers profit_guard from the four reason codes (含 commitment 承诺退出线)", () => {
    for (const reason of [
      "profit_guard_trailing",
      "profit_guard_breakeven",
      "profit_guard_hard_take_profit",
      "profit_guard_committed_exit",
    ]) {
      expect(resolveExplain(trade({ side: "close", explain: undefined, reason })).kind).toBe(
        "profit_guard",
      );
    }
  });

  it("keeps other close reasons untouched（新前缀不吞既有归类）", () => {
    expect(resolveExplain(trade({ side: "close", explain: undefined, reason: "max_hold" })).kind).toBe(
      "risk_exit",
    );
    expect(resolveExplain(trade({ side: "close", explain: undefined, reason: "profit" })).kind).toBe(
      "atomic",
    );
  });
});

