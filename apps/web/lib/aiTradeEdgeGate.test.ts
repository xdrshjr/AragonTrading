import { describe, expect, it } from "vitest";
import { describeDegradeReason, describeEdgeGate } from "./aiTrade";
import type { AiTradeRoundTrace, AiTradeTraceEvent, AiTradeTracePhase } from "./aiTradeTypes";
import { applyAiTradeTracePhase } from "./store";

// 最小 edge 门禁的前端镜像（ai-trade-cost-accounting-and-edge-gate §4.3 / §7）。
// 三条被冻结的性质：三态中文化、降级码中文化（保留参数后缀）、老快照 / 未启用会话恒 null（整段不渲染）。

function ev(
  phase: AiTradeTracePhase,
  round: number,
  extra: Partial<AiTradeTraceEvent> = {},
): AiTradeTraceEvent {
  return { phase, round, ts: 1_700_000_000 + round, ...extra };
}

describe("describeEdgeGate", () => {
  it("未启用门禁（k_min=0，默认）⇒ null ⇒ 组件整段不渲染", () => {
    expect(describeEdgeGate({ edgeGateStatus: null, edgeBps: null, edgeCostBps: null })).toBeNull();
    // 老快照连键都没有（后端先行上线 / 历史会话回放）。
    expect(describeEdgeGate({} as AiTradeRoundTrace)).toBeNull();
  });

  it("放行 ⇒ muted（常态不抢眼）并带 edge/成本读数", () => {
    const view = describeEdgeGate({ edgeGateStatus: "pass", edgeBps: 42.5, edgeCostBps: 13 });
    expect(view).not.toBeNull();
    expect(view!.tone).toBe("muted");
    expect(view!.label).toContain("放行");
    expect(view!.label).toContain("42.5 / 13.0bps");
  });

  it("否决 ⇒ short（红，须显眼——静默的保守会被当成故障）", () => {
    const view = describeEdgeGate({ edgeGateStatus: "veto", edgeBps: 8.1, edgeCostBps: 13 });
    expect(view!.tone).toBe("short");
    expect(view!.label).toContain("否决");
  });

  it("无法估算 ⇒ accent 而非 short：本层 fail-open，此时开仓是**放行**的", () => {
    // 与铁律层的同名态含义正相反（那边 unavailable = 一律否决）。用红色会让用户以为被拦住了。
    const view = describeEdgeGate({ edgeGateStatus: "unavailable", edgeBps: null, edgeCostBps: null });
    expect(view!.tone).toBe("accent");
    expect(view!.label).toContain("放行");
  });

  it("读数缺失时只出状态、不编造数字", () => {
    const view = describeEdgeGate({ edgeGateStatus: "veto", edgeBps: null, edgeCostBps: 13 });
    expect(view!.label).toBe("成本门禁否决 · 观望");
  });

  // ---- 信号先验（signal-prior-and-ev-gate §6.3 / T-18）----
  it("分子来自先验 ⇒ 标签带 p= 读数（量级差一档，不标出来会被误读成门禁突然变严）", () => {
    const view = describeEdgeGate({
      edgeGateStatus: "veto",
      edgeBps: 3.2,
      edgeCostBps: 13,
      edgeSource: "prior",
      priorP: 0.54,
    });
    expect(view!.label).toContain("p=0.54");
    expect(view!.label).toContain("3.2 / 13.0bps");
  });

  it("分子来自 ATR / 止盈上界 ⇒ 标签**不带** p=（老会话与总闸③关闭时逐字不变）", () => {
    for (const edgeSource of ["atr", "take_profit"] as const) {
      const view = describeEdgeGate({
        edgeGateStatus: "pass",
        edgeBps: 42.5,
        edgeCostBps: 13,
        edgeSource,
        priorP: 0.54, // 总闸③关闭：读数照发，但分子不是它 ⇒ 标签里不得出现
      });
      expect(view!.label).toBe("成本门禁 · 放行（42.5 / 13.0bps）");
    }
  });
});

describe("describeDegradeReason（edge_below_cost 前缀）", () => {
  it("中文化命中前缀并原样保留参数后缀（供排查）", () => {
    expect(describeDegradeReason("edge_below_cost(edge=8.1bps,cost=13.0bps,k=2.0)")).toBe(
      "期望收益不足以覆盖交易成本，本轮观望(edge=8.1bps,cost=13.0bps,k=2.0)",
    );
  });

  it("既有降级码的展示逐字不变（未命中一律原样回退）", () => {
    // `low_consensus` 自 ai-trade-directional-symmetry 起已登记中文（评审 P0-4，**预期变更**）；
    // 这条契约要守的是「未命中一律原样回退」，故改用一个仍然未登记的码。
    expect(describeDegradeReason("some_unregistered_code(1/2)")).toBe("some_unregistered_code(1/2)");
    expect(describeDegradeReason("decision_timeout")).toBe("decision_timeout");
  });
});

describe("applyAiTradeTracePhase / edge_gate 相位", () => {
  it("落 4 个字段；未收到该相位前恒为初值 null", () => {
    let t = applyAiTradeTracePhase([], ev("round_start", 1));
    expect(t[0].edgeGateStatus).toBeNull();
    expect(t[0].edgeBps).toBeNull();
    expect(t[0].edgeCostBps).toBeNull();
    expect(t[0].edgeGateReason).toBeNull();

    t = applyAiTradeTracePhase(
      t,
      ev("edge_gate", 1, {
        status: "veto",
        edgeBps: 8.1,
        costBps: 13,
        reason: "edge_below_cost(edge=8.1bps,cost=13.0bps,k=2.0)",
      }),
    );
    expect(t[0].edgeGateStatus).toBe("veto");
    expect(t[0].edgeBps).toBe(8.1);
    expect(t[0].edgeCostBps).toBe(13);
    expect(t[0].edgeGateReason).toBe("edge_below_cost(edge=8.1bps,cost=13.0bps,k=2.0)");
  });

  it("先验三键经 store 逐键手抄落轮次（漏抄即后端发什么前端都收不到，§6.3 P0-1）", () => {
    let t = applyAiTradeTracePhase([], ev("round_start", 1, { signalPriors: { horizonBars: 8 } }));
    expect(t[0].signalPriors).toEqual({ horizonBars: 8 });
    expect(t[0].edgeSource).toBeNull();
    expect(t[0].priorP).toBeNull();
    expect(t[0].priorEvBps).toBeNull();

    t = applyAiTradeTracePhase(
      t,
      ev("edge_gate", 1, { status: "veto", edgeBps: 3.2, costBps: 13, edgeSource: "prior", priorP: 0.54, priorEvBps: 3.2 }),
    );
    expect(t[0].edgeSource).toBe("prior");
    expect(t[0].priorP).toBe(0.54);
    expect(t[0].priorEvBps).toBe(3.2);
  });

  it("round_start 无 signalPriors 键（三总闸全关）⇒ 轮次上恒为 null，零回归", () => {
    const t = applyAiTradeTracePhase([], ev("round_start", 1));
    expect(t[0].signalPriors).toBeNull();
  });

  it("未识别的 costSnapshot 加法键被忽略，不污染轮次对象", () => {
    let t = applyAiTradeTracePhase([], ev("round_start", 1));
    t = applyAiTradeTracePhase(
      t,
      ev("edge_gate", 1, { status: "pass", costSnapshot: { cost: { roundTrips: 3 } } }),
    );
    expect(t[0].edgeGateStatus).toBe("pass");
    expect("costSnapshot" in t[0]).toBe(false);
  });

  it("老前端遇 edge_gate 相位不崩、不误改其它字段（Z-6 的后端先行上线保证）", () => {
    let t = applyAiTradeTracePhase([], ev("round_start", 1));
    t = applyAiTradeTracePhase(t, ev("analysis", 1, { delta: "X" }));
    // @ts-expect-error 模拟老前端：把 edge_gate 当未知 phase 走 default 分支。
    const out = applyAiTradeTracePhase(t, ev("totally_unknown_gate", 1, { delta: "Y" }));
    expect(out[0].analysis).toBe("X");
    expect(out[0].edgeGateStatus).toBeNull();
  });
});
