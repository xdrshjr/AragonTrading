import { describe, expect, it } from "vitest";
import { describeDegradeReason, describeSizing } from "./aiTrade";
import type { AiTradeRoundTrace, AiTradeTraceEvent, AiTradeTracePhase } from "./aiTradeTypes";
import { applyAiTradeTracePhase } from "./store";

// 仓位 / 刹车层的前端镜像（vol-sizing-and-drawdown-brake §6.3 / T-第 6、4、5 编辑点）。
// 三条被冻结的性质：四态中文化、两条降级码中文化（保留参数后缀）、store 逐键手抄（漏抄即静默缺失）。

function ev(
  phase: AiTradeTracePhase,
  round: number,
  extra: Partial<AiTradeTraceEvent> = {},
): AiTradeTraceEvent {
  return { phase, round, ts: 1_700_000_000 + round, ...extra };
}

// 后端 trace.SIZING_TRACE_KEYS 的前端镜像：store 的 emptyRoundTrace 必须逐键初始化。
const SIZING_KEYS = [
  "brakeLevel",
  "brakeReason",
  "drawdownPct",
  "lossStreak",
  "peakEquityBasis",
  "unrealizedShare",
  "modelFraction",
  "fVol",
  "fKelly",
  "fCap",
  "kellyStatus",
  "fFinalRaw",
  "finalFraction",
  "scaleSource",
  "scaleErased",
  "volSource",
  "stopPct",
  "takePct",
  "stopSource",
  "effectiveExposure",
  "effectiveExposureCap",
  "corrScale",
  "exposureCapUnreachable",
] as const;

describe("describeSizing", () => {
  it("三条规则全未装配（默认）⇒ null ⇒ 组件整段不渲染", () => {
    expect(describeSizing({} as AiTradeRoundTrace)).toBeNull();
    expect(describeSizing({ brakeLevel: null, stopSource: null } as AiTradeRoundTrace)).toBeNull();
  });

  it("第二档「只平不开」⇒ short（红），且文案必须写清只拦开新仓", () => {
    const view = describeSizing({
      brakeLevel: "halt",
      drawdownPct: 0.162,
      brakeReason: "drawdown_brake_halt(dd=16.2%)",
    } as AiTradeRoundTrace);
    expect(view).not.toBeNull();
    expect(view!.tone).toBe("short");
    expect(view!.label).toContain("只平不开");
    expect(view!.label).toContain("16.2%");
    expect(view!.note).toContain("平仓与撤单");
  });

  it("第三档连亏冷却 ⇒ short，与第二档同为阻断档", () => {
    const view = describeSizing({ brakeLevel: "cooldown" } as AiTradeRoundTrace);
    expect(view!.tone).toBe("short");
    expect(view!.label).toContain("冷却");
  });

  it("真实缩减 ⇒ 标签写出 提议→执行 两个数（accent）", () => {
    const view = describeSizing({
      brakeLevel: "none",
      stopSource: "atr",
      modelFraction: 0.75,
      finalFraction: 0.25,
      scaleSource: "vol",
    } as AiTradeRoundTrace);
    expect(view!.tone).toBe("accent");
    expect(view!.label).toBe("仓位层缩减 · 0.75→0.25");
    expect(view!.note).toContain("波动率目标项");
  });

  it("未缩减 ⇒ muted（常态不抢眼）", () => {
    const view = describeSizing({
      brakeLevel: "none",
      stopSource: "atr",
      modelFraction: 0.25,
      finalFraction: 0.25,
      scaleSource: "model",
    } as AiTradeRoundTrace);
    expect(view!.tone).toBe("muted");
    expect(view!.label).toBe("仓位层 · 未缩减");
  });

  it("档位地板吃掉缩减 ⇒ 必须显式说出来（否则运维会据计数认定刹车生效了）", () => {
    const view = describeSizing({
      brakeLevel: "slow",
      stopSource: "atr",
      modelFraction: 0.25,
      finalFraction: 0.25,
      fFinalRaw: 0.125,
      scaleErased: true,
      scaleSource: "brake",
    } as AiTradeRoundTrace);
    expect(view!.note).toContain("0.125");
    expect(view!.note).toContain("实际未生效");
  });

  it("凯利降级一律说成「该项不参与」，绝不说成仓位归零", () => {
    for (const [status, needle] of [
      ["no_prior", "暂无胜率先验"],
      ["no_cost", "成本估算缺席"],
      ["no_bracket", "括号不可用"],
    ] as const) {
      const view = describeSizing({
        brakeLevel: "none",
        stopSource: "atr",
        kellyStatus: status,
      } as AiTradeRoundTrace);
      expect(view!.note).toContain(needle);
      expect(view!.note).not.toContain("归零");
    }
  });

  it("止损来源五态各有中文，三个 skip 态必须讲清「为什么没按 ATR 挂」", () => {
    for (const [source, needle] of [
      ["atr", "按本轮 ATR 定标"],
      ["fixed", "固定百分比"],
      ["skipped_resting", "限价挂单在飞"],
      ["skipped_position_live", "已有持仓"],
      ["skipped_reversal", "反手"],
    ] as const) {
      const view = describeSizing({ brakeLevel: "none", stopSource: source } as AiTradeRoundTrace);
      expect(view!.note).toContain(needle);
    }
  });

  it("敞口上限不可达 ⇒ 如实标注，且明写本层不否决开仓", () => {
    const view = describeSizing({
      brakeLevel: "none",
      stopSource: "atr",
      exposureCapUnreachable: true,
    } as AiTradeRoundTrace);
    expect(view!.note).toContain("不可达");
    expect(view!.note).toContain("不否决");
  });
});

describe("describeDegradeReason（刹车两码）", () => {
  it("中文化命中前缀并原样保留参数后缀（供排查）", () => {
    expect(describeDegradeReason("drawdown_brake_halt(dd=16.2%)")).toBe(
      "回撤刹车：会话回撤越线，暂停开新仓（平仓不受影响）(dd=16.2%)",
    );
    expect(describeDegradeReason("loss_streak_cooldown(4,3600s)")).toBe(
      "连亏熔断：连续亏损达阈，冷却期内暂停开新仓（平仓不受影响）(4,3600s)",
    );
  });

  it("既有降级码的展示逐字不变（未命中一律原样回退）", () => {
    expect(describeDegradeReason("low_consensus(1/2)")).toBe("low_consensus(1/2)");
    expect(describeDegradeReason("edge_below_cost(edge=8.1bps,cost=13.0bps,k=2.0)")).toContain(
      "期望收益不足以覆盖交易成本",
    );
  });
});

describe("applyAiTradeTracePhase / sizing 相位", () => {
  it("emptyRoundTrace 逐键初始化全部 sizing 键（漏抄即后端发什么前端都收不到）", () => {
    const [trace] = applyAiTradeTracePhase([], ev("round_start", 1));
    for (const key of SIZING_KEYS) {
      expect(key in trace, `emptyRoundTrace 漏抄了 ${key}`).toBe(true);
      expect(trace[key as keyof AiTradeRoundTrace], `${key} 初值应为 null`).toBeNull();
    }
  });

  it("sizing 相位把全部键归约进轮次（含 fFinalRaw 与 finalFraction 两者）", () => {
    let traces = applyAiTradeTracePhase([], ev("round_start", 1));
    traces = applyAiTradeTracePhase(
      traces,
      ev("sizing", 1, {
        brakeLevel: "slow",
        brakeReason: null,
        drawdownPct: 0.092,
        lossStreak: 0,
        peakEquityBasis: "markToMarket",
        unrealizedShare: 0.62,
        modelFraction: 0.75,
        fVol: 0.28,
        fKelly: 0.21,
        fCap: 0.21,
        kellyStatus: "ok",
        fFinalRaw: 0.135,
        finalFraction: 0.25,
        scaleSource: "brake",
        scaleErased: true,
        volSource: "realized",
        stopPct: 0.021,
        takePct: 0.042,
        stopSource: "atr",
        effectiveExposure: 12500,
        effectiveExposureCap: 18000,
        corrScale: 1,
        exposureCapUnreachable: false,
      }),
    );
    const t = traces[0];
    expect(t.brakeLevel).toBe("slow");
    expect(t.fFinalRaw).toBe(0.135);
    expect(t.finalFraction).toBe(0.25);
    expect(t.scaleErased).toBe(true);
    expect(t.stopSource).toBe("atr");
    expect(t.corrScale).toBe(1);
    expect(t.exposureCapUnreachable).toBe(false);
    expect(t.volSource).toBe("realized");
    expect(t.peakEquityBasis).toBe("markToMarket");
  });

  it("丢过 round_start 时以 sizing 事件补建桩轮次（容乱序）", () => {
    const traces = applyAiTradeTracePhase([], ev("sizing", 7, { brakeLevel: "halt" }));
    expect(traces).toHaveLength(1);
    expect(traces[0].round).toBe(7);
    expect(traces[0].brakeLevel).toBe("halt");
  });

  it("未装配的会话收不到该相位 ⇒ 轮次恒为初值 ⇒ describeSizing 返回 null", () => {
    const [trace] = applyAiTradeTracePhase([], ev("round_start", 1));
    expect(describeSizing(trace)).toBeNull();
  });
});
