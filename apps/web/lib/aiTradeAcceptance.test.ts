import { describe, expect, it } from "vitest";
import {
  ACCEPTANCE_VERDICT_META,
  describeFundingBasis,
  exitSourceLabel,
  formatExitHold,
  formatExitWinRate,
  formatSeconds,
  hasAcceptance,
  hasExitAttribution,
  orderedExitRows,
  verdictColor,
} from "./aiTradeAcceptance";
import type { AiTradeAcceptance, AiTradeExitMix } from "./aiTradeTypes";

// 周期验收闭环的前端共用件（ai-trade-horizon-acceptance-loop §6.2）。
// 守的是三条呈现层纪律：`insufficient` 不许绿、`null` 不许渲染成 0、只有「未记录来源」不出卡。

const CARD: AiTradeAcceptance = {
  profileId: "short_swing",
  title: "短波段",
  verdict: "fail",
  sampleFloor: 20,
  roundTrips: 16,
  truncatedSamples: 1,
  sampleNote: "样本 16 / 20 —— 未达样本下限",
  rows: [
    {
      key: "avgHold", label: "平均持仓时间", verdict: "fail", value: 2140.5,
      unit: "seconds", display: "35m 41s", target: "≥ 12h", note: "低于目标下界",
    },
  ],
  diagnostics: [],
};

describe("aiTradeAcceptance — 三态徽标", () => {
  it("insufficient 用灰色而不是绿色（样本不足不是「快通过了」）", () => {
    expect(verdictColor("insufficient")).toContain("--muted");
    expect(verdictColor("insufficient")).not.toContain("--long");
    expect(verdictColor("pass")).not.toContain("--muted");
  });

  it("三态各有中文标签与图标", () => {
    expect(ACCEPTANCE_VERDICT_META.pass.label).toBe("已通过");
    expect(ACCEPTANCE_VERDICT_META.fail.label).toBe("未通过");
    expect(ACCEPTANCE_VERDICT_META.insufficient.label).toBe("样本不足");
  });
});

describe("aiTradeAcceptance — 渲染门控", () => {
  it("无卡 / 空行 ⇒ 不渲染（绝不在没有周期契约的会话上挂空卡）", () => {
    expect(hasAcceptance(null)).toBe(false);
    expect(hasAcceptance(undefined)).toBe(false);
    expect(hasAcceptance({ ...CARD, rows: [] })).toBe(false);
    expect(hasAcceptance(CARD)).toBe(true);
  });

  it("只有「未记录来源」⇒ 退出来源卡不渲染", () => {
    const onlyUnknown: AiTradeExitMix = {
      totalExits: 3,
      bySource: [{ source: "unknown", count: 3, share: 1, netUsd: 0 }],
    };
    expect(hasExitAttribution(onlyUnknown)).toBe(false);
    expect(hasExitAttribution(null)).toBe(false);
    expect(
      hasExitAttribution({
        totalExits: 4,
        bySource: [
          { source: "agent", count: 1, share: 0.25, netUsd: 1 },
          ...onlyUnknown.bySource,
        ],
      }),
    ).toBe(true);
  });

  it("unknown 恒排最后一段（其余保持后端给的 count 降序）", () => {
    const mix: AiTradeExitMix = {
      totalExits: 6,
      bySource: [
        { source: "agent", count: 3, share: 0.5, netUsd: 1 },
        { source: "unknown", count: 2, share: 0.33, netUsd: 0 },
        { source: "profitGuard", count: 1, share: 0.17, netUsd: 2 },
      ],
    };
    expect(orderedExitRows(mix).map((r) => r.source)).toEqual([
      "agent",
      "profitGuard",
      "unknown",
    ]);
  });
});

describe("aiTradeAcceptance — 诚实性文案", () => {
  it("胜率样本不足 ⇒ 明说「样本不足」，绝不渲染成 0%", () => {
    expect(formatExitWinRate({ source: "agent", count: 2, netUsd: 0, winRate: null }))
      .toBe("样本不足");
    expect(formatExitWinRate({ source: "agent", count: 9, netUsd: 0, winRate: 0.2667 }))
      .toBe("26.7%");
  });

  it("无时长样本 ⇒ 「—」而不是 0s", () => {
    expect(formatExitHold({ source: "agent", count: 1, netUsd: 0, avgHoldSeconds: null }))
      .toBe("—");
    expect(formatExitHold({ source: "agent", count: 1, netUsd: 0, avgHoldSeconds: 2141 }))
      .toBe("35m 41s");
  });

  it("资金费 null 与 0 是两个结论", () => {
    expect(describeFundingBasis(null, "unavailable")).toBe("口径不可得");
    expect(describeFundingBasis(0, "accrued")).toBe("未跨结算点");
    expect(describeFundingBasis(1.2, "settled+accrued")).toBe("交易所结算口径");
    expect(describeFundingBasis(1.2, "accrued")).toBe("本地计提口径");
  });

  it("时长格式化与后端规则一致（秒 / 分 / 时 / 天）", () => {
    expect(formatSeconds(45)).toBe("45s");
    expect(formatSeconds(120)).toBe("2m");
    expect(formatSeconds(2141)).toBe("35m 41s");
    expect(formatSeconds(43200)).toBe("12h");
    expect(formatSeconds(45000)).toBe("12h 30m");
    expect(formatSeconds(172800)).toBe("2d");
    expect(formatSeconds(180000)).toBe("2d 2h");
  });

  it("未知来源枚举照原样显示（绝不静默吞掉后端新增的一类）", () => {
    expect(exitSourceLabel("agent")).toBe("Agent 主动平仓");
    expect(exitSourceLabel("brandNewSource")).toBe("brandNewSource");
  });
});
