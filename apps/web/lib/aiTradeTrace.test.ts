import { describe, expect, it } from "vitest";
import type { AiTradeRoundTrace, AiTradeTraceEvent, AiTradeTracePhase } from "./aiTradeTypes";
import { applyAiTradeTracePhase } from "./store";

// 构造一条 agent_trace 事件 data（ts 由 round 派生，稳定可断言）。
function ev(
  phase: AiTradeTracePhase,
  round: number,
  extra: Partial<AiTradeTraceEvent> = {},
): AiTradeTraceEvent {
  return { phase, round, ts: 1_700_000_000 + round, ...extra };
}

describe("applyAiTradeTracePhase", () => {
  it("round_start 新建一轮 deciding 态", () => {
    const out = applyAiTradeTracePhase([], ev("round_start", 1, { lastPx: 100, signalBoard: [] }));
    expect(out).toHaveLength(1);
    expect(out[0].round).toBe(1);
    expect(out[0].state).toBe("deciding");
    expect(out[0].analysis).toBe("");
    expect(out[0].lastPx).toBe(100);
  });

  it("round_start and later phases preserve multi-timeframe context", () => {
    const context = {
      executionBar: "1H", asOfTs: 1, frames: [],
      trendAlignment: { status: "insufficient" as const, bullishBars: [], bearishBars: [], rangeBars: [] },
      rsiOverboughtBars: [], rsiOversoldBars: [], nearestSupport: null, nearestResistance: null,
      missingBars: ["1D", "12H", "4H"], staleBars: [],
    };
    let traces = applyAiTradeTracePhase(
      [], ev("round_start", 1, { multiTimeframeContext: context }),
    );
    traces = applyAiTradeTracePhase(traces, ev("analysis", 1, { delta: "继续研判" }));
    expect(traces[0].multiTimeframeContext).toBe(context);

    traces = applyAiTradeTracePhase(
      traces, ev("round_start", 1, { lastPx: 200, multiTimeframeContext: context }),
    );
    expect(traces[0].multiTimeframeContext).toBe(context);
    expect(traces[0].analysis).toBe("继续研判");
  });

  it("analysis / thinking 增量追加到对应轮", () => {
    let t = applyAiTradeTracePhase([], ev("round_start", 1));
    t = applyAiTradeTracePhase(t, ev("analysis", 1, { delta: "多头" }));
    t = applyAiTradeTracePhase(t, ev("analysis", 1, { delta: "占优" }));
    t = applyAiTradeTracePhase(t, ev("thinking", 1, { delta: "深思" }));
    expect(t[0].analysis).toBe("多头占优");
    expect(t[0].thinking).toBe("深思");
  });

  it("乱序增量（先到 analysis 后到 round_start）以桩轮次容错并保留累积文本", () => {
    let t = applyAiTradeTracePhase([], ev("analysis", 3, { delta: "先到" }));
    expect(t).toHaveLength(1);
    expect(t[0].round).toBe(3);
    expect(t[0].analysis).toBe("先到");
    expect(t[0].state).toBe("deciding");
    // 随后 round_start 到达：去重刷新输入快照但不丢累积文本（幂等）。
    t = applyAiTradeTracePhase(t, ev("round_start", 3, { lastPx: 200 }));
    expect(t).toHaveLength(1);
    expect(t[0].analysis).toBe("先到");
    expect(t[0].lastPx).toBe(200);
  });

  it("tool_call_start 置 pending，tool_call 写入 input 并清 pending", () => {
    let t = applyAiTradeTracePhase([], ev("round_start", 1));
    t = applyAiTradeTracePhase(t, ev("tool_call_start", 1, { name: "submit_trade_decision" }));
    expect(t[0].toolCallPending).toBe(true);
    t = applyAiTradeTracePhase(
      t,
      ev("tool_call", 1, {
        input: { action: "open_long", sizeFraction: 0.4, confidence: 0.7, reason: "r" },
      }),
    );
    expect(t[0].toolCall?.action).toBe("open_long");
    expect(t[0].toolCallPending).toBe(false);
  });

  // ---- 「提议 X → 被 Y 拦下」的四个兄弟键（directional-symmetry B-3）----
  // `emptyRoundTrace` 与 `reduceRoundPhase` 的 `case "tool_call"` 是**两处逐键手抄**点：
  // 漏抄任一处的表现是「后端发了、前端收不到、跑不出错」。
  it("tool_call 归约必须收得到 layerLabel（漏抄即静默丢数据）", () => {
    let t = applyAiTradeTracePhase([], ev("round_start", 1));
    expect(t[0].layerLabel).toBeNull();      // 初始化也必须有这个键
    t = applyAiTradeTracePhase(
      t,
      ev("tool_call", 1, {
        input: { action: "hold", reason: "观望" },
        proposedAction: "open_short",
        proposedReason: "净立场偏多但结构已破",
        vetoedBy: "consensus",
        layerLabel: "同向共识门槛",
      }),
    );
    expect(t[0].proposedAction).toBe("open_short");
    expect(t[0].vetoedBy).toBe("consensus");
    expect(t[0].layerLabel).toBe("同向共识门槛");
  });

  it("老快照（无 layerLabel）⇒ 该键为 null，其余三键逐字回退", () => {
    let t = applyAiTradeTracePhase([], ev("round_start", 1));
    t = applyAiTradeTracePhase(
      t,
      ev("tool_call", 1, { input: { action: "hold", reason: "观望" }, proposedAction: "close", vetoedBy: "horizon" }),
    );
    expect(t[0].layerLabel).toBeNull();
    expect(t[0].vetoedBy).toBe("horizon");
  });

  it("result 写 executed/degrade 并据 skipped 派生 state", () => {
    let t = applyAiTradeTracePhase([], ev("round_start", 1));
    t = applyAiTradeTracePhase(
      t,
      ev("result", 1, { executed: { traded: true, positionAfter: "long" }, degradeReason: null }),
    );
    expect(t[0].state).toBe("done");
    expect(t[0].executed?.traded).toBe(true);

    let s = applyAiTradeTracePhase([], ev("round_start", 2));
    s = applyAiTradeTracePhase(s, ev("result", 2, { executed: { skipped: true } }));
    expect(s[0].state).toBe("skipped");
  });

  it("summary 附着、error 置 error 态并写 message", () => {
    let t = applyAiTradeTracePhase([], ev("round_start", 1));
    t = applyAiTradeTracePhase(t, ev("summary", 1, { summary: "截至第1轮：偏多" }));
    expect(t[0].summary).toBe("截至第1轮：偏多");

    let e = applyAiTradeTracePhase([], ev("round_start", 2));
    e = applyAiTradeTracePhase(e, ev("error", 2, { message: "本轮决策异常" }));
    expect(e[0].state).toBe("error");
    expect(e[0].errorMessage).toBe("本轮决策异常");
  });

  it("retry 阶段重置累积研判并计数（清工具挂起指示）", () => {
    let t = applyAiTradeTracePhase([], ev("round_start", 1));
    t = applyAiTradeTracePhase(t, ev("tool_call_start", 1, { name: "submit_trade_decision" }));
    t = applyAiTradeTracePhase(t, ev("analysis", 1, { delta: "A" }));
    expect(t[0].toolCallPending).toBe(true);
    expect(t[0].analysis).toBe("A");
    // 失败尝试后重试：重置本轮累积（研判文本 + 工具挂起指示）、复位 deciding、计数 +1、记原因。
    t = applyAiTradeTracePhase(
      t,
      ev("retry", 1, { attempt: 1, maxAttempts: 3, reason: "llm_error" }),
    );
    expect(t[0].analysis).toBe("");
    expect(t[0].thinking).toBe("");
    expect(t[0].toolCallPending).toBe(false); // R-1：失败尝试的工具挂起指示已清
    expect(t[0].state).toBe("deciding");
    expect(t[0].retries).toBe(1);
    expect(t[0].lastRetryReason).toBe("llm_error");
    // 重试后成功尝试的新研判独立累积（非与失败尝试拼接）。
    t = applyAiTradeTracePhase(t, ev("analysis", 1, { delta: "B" }));
    expect(t[0].analysis).toBe("B");
  });

  it("reflection 三阶段：start 置 pending、reflection 累积、verdict 落裁决并清 pending", () => {
    let t = applyAiTradeTracePhase([], ev("round_start", 1));
    // reflection_start：进入二次反思，置在飞指示。
    t = applyAiTradeTracePhase(
      t,
      ev("reflection_start", 1, {
        input: { action: "open_long", sizeFraction: 0.5, confidence: 0.8, reason: "顺势" },
      }),
    );
    expect(t[0].reflectionPending).toBe(true);
    // reflection：复核推理流式累积。
    t = applyAiTradeTracePhase(t, ev("reflection", 1, { delta: "高周期" }));
    t = applyAiTradeTracePhase(t, ev("reflection", 1, { delta: "顶背离" }));
    expect(t[0].reflection).toBe("高周期顶背离");
    // reflection_verdict：落 abort 裁决、理由、收缩后仓位并清 pending。
    t = applyAiTradeTracePhase(
      t,
      ev("reflection_verdict", 1, { verdict: "abort", reason: "追多风险高", adjustedSizeFraction: null }),
    );
    expect(t[0].reflectionPending).toBe(false);
    expect(t[0].reflectionVerdict).toBe("abort");
    expect(t[0].reflectionReason).toBe("追多风险高");
    expect(t[0].reflectionAdjustedSize).toBeNull();
  });

  it("reflection_verdict confirm 携收缩后仓位", () => {
    let t = applyAiTradeTracePhase([], ev("round_start", 1));
    t = applyAiTradeTracePhase(
      t,
      ev("reflection_verdict", 1, { verdict: "confirm", reason: "减仓更稳", adjustedSizeFraction: 0.2 }),
    );
    expect(t[0].reflectionVerdict).toBe("confirm");
    expect(t[0].reflectionAdjustedSize).toBe(0.2);
  });

  it("round_start 幂等刷新保留已累积反思文本", () => {
    let t = applyAiTradeTracePhase([], ev("round_start", 1));
    t = applyAiTradeTracePhase(t, ev("reflection", 1, { delta: "复核中" }));
    t = applyAiTradeTracePhase(t, ev("round_start", 1, { lastPx: 200 }));
    expect(t[0].reflection).toBe("复核中"); // 与 thinking/analysis 同：幂等刷新不丢累积
    expect(t[0].lastPx).toBe(200);
  });

  it("未知 phase 走 default 容错（向后兼容，不崩）", () => {
    let t = applyAiTradeTracePhase([], ev("round_start", 1));
    t = applyAiTradeTracePhase(t, ev("analysis", 1, { delta: "X" }));
    // @ts-expect-error 故意注入未知 phase，模拟老前端遇新事件。
    const out = applyAiTradeTracePhase(t, ev("totally_unknown", 1, { delta: "Y" }));
    expect(out[0].analysis).toBe("X"); // default: 原样返回，不误改
  });

  it("base_rule 相位落 4 个字段（【基础策略】铁律门禁，§6.2 用例 28）", () => {
    let t = applyAiTradeTracePhase([], ev("round_start", 1));
    expect(t[0].baseRuleStatus).toBeNull();          // 未收到该相位前恒为初值
    t = applyAiTradeTracePhase(
      t,
      ev("base_rule", 1, {
        status: "veto",
        zone: null,
        pctB: 0.42,
        reason: "base_rule_ma_band(pctB=0.42)",
      }),
    );
    expect(t[0].baseRuleStatus).toBe("veto");
    expect(t[0].baseRuleZone).toBeNull();
    expect(t[0].baseRulePctB).toBe(0.42);
    expect(t[0].baseRuleReason).toBe("base_rule_ma_band(pctB=0.42)");
  });

  it("base_rule 放行轮落 pass + 锚区，不影响其它累积", () => {
    let t = applyAiTradeTracePhase([], ev("round_start", 1));
    t = applyAiTradeTracePhase(t, ev("analysis", 1, { delta: "研判" }));
    t = applyAiTradeTracePhase(t, ev("base_rule", 1, { status: "pass", zone: "mid", pctB: 0.5 }));
    expect(t[0].baseRuleStatus).toBe("pass");
    expect(t[0].baseRuleZone).toBe("mid");
    expect(t[0].analysis).toBe("研判");
  });

  // ---------- 唤醒感知上下文（wake-aware-context §6.1 T-17）----------
  it("round_start 带四个新键 ⇒ AiTradeRoundTrace 上可读（防 store.ts 逐键手抄漏抄）", () => {
    const tactical = {
      executionBar: "30m",
      wakeIntervalS: 180,
      asOfTs: 1_700_000_000,
      executionPrimary: "trend_up",
      alignment: "diverged" as const,
      changeSinceLastRound: { pct: 0.42, atrMult: 0.91, atrBar: "30m" },
      frames: [
        { bar: "5m", sampleCount: 120, closeTs: 1, ageSeconds: 42, stale: false, closedBars: 1 },
        { bar: "1m", sampleCount: 120, closeTs: 1, ageSeconds: 12, stale: false, closedBars: 3 },
      ],
      missingBars: [],
      staleBars: [],
    };
    const wake = { kind: "price_breakout" as const, trigger: "interval" as const, priceMoveAtr: 0.91 };
    let t = applyAiTradeTracePhase(
      [],
      ev("round_start", 1, {
        wake,
        tacticalContext: tactical,
        tacticalTier: "full",
        contextDisclosure: "repeat",
      }),
    );
    expect(t[0].wake).toEqual(wake);
    expect(t[0].tacticalContext).toEqual(tactical);
    expect(t[0].tacticalTier).toBe("full");
    expect(t[0].contextDisclosure).toBe("repeat");

    // 后续 phase 归约不得把它们丢掉（thinking/analysis 累积期整轮都要留着）
    t = applyAiTradeTracePhase(t, ev("analysis", 1, { delta: "研判" }));
    expect(t[0].tacticalContext).toEqual(tactical);
    expect(t[0].wake?.kind).toBe("price_breakout");
  });

  it("老后端不发这四键时恒为 null（前端整段回退旧文案，不炸）", () => {
    const t = applyAiTradeTracePhase([], ev("round_start", 1));
    expect(t[0].wake).toBeNull();
    expect(t[0].tacticalContext).toBeNull();
    expect(t[0].tacticalTier).toBeNull();
    expect(t[0].contextDisclosure).toBeNull();
  });

  it("趋势基调走两条不同的手抄轨：verdict 在 round_start、gate 在 tool_call", () => {
    // execution-trend-verdict §3.7.4-4/5：`round_start` **只经过** `emptyRoundTrace` 这一个
    // 装配点，`trendGate` 走 `reduceRoundPhase` 的 `case "tool_call"`。两条轨不可互相顶替，
    // 漏抄任一处都不会报错，只会安静地什么都不显示。
    const verdict = {
      bar: "15m", asOfTs: 1, score: -0.32, execScore: -0.61,
      direction: "down" as const, bias: "short" as const, strength: "strong" as const,
      verdictConfidence: 0.59, components: [], missing: [],
      availableWeight: 0.86, execAvailableWeight: 0.84,
      higherTimeframe: { status: "bullish", agree: false, bullishBars: ["1D"], bearishBars: [] },
      tacticalAlignment: null, conflict: "htf_conflict" as const, bonusWithheld: true,
      counterGate: { armed: true, minConfidence: 0.65, sizeMult: 0.5 },
      degraded: false, degradeReason: null,
    };
    const gate = {
      lane: "veto" as const, alignment: "counter" as const, strength: "strong" as const,
      score: -0.32, execScore: -0.61, minConfidence: 0.65, modelConfidence: 0.4,
      verdictConfidence: 0.59, sizeMult: 0.5, sizeBefore: 0.5, sizeAfter: null,
      reason: "counter_trend_confidence(conf=0.40/0.65)",
    };
    let t = applyAiTradeTracePhase([], ev("round_start", 1, { trendVerdict: verdict }));
    expect(t[0].trendVerdict).toBe(verdict);
    expect(t[0].trendGate).toBeNull();
    t = applyAiTradeTracePhase(t, ev("tool_call", 1, { trendGate: gate }));
    expect(t[0].trendGate).toBe(gate);
    expect(t[0].trendVerdict).toBe(verdict);   // round_start 那一份不被 tool_call 冲掉
    // 本层没判过的轮次后端不发这个键 ⇒ 保住同一轮里先到的那一份。
    t = applyAiTradeTracePhase(t, ev("tool_call", 1, {}));
    expect(t[0].trendGate).toBe(gate);
  });

  it("老后端不发趋势基调两键时恒为 null（整段不渲染，不炸）", () => {
    const t = applyAiTradeTracePhase([], ev("round_start", 1));
    expect(t[0].trendVerdict).toBeNull();
    expect(t[0].trendGate).toBeNull();
  });

  it("环形容量：超过 20 轮裁最旧", () => {
    let t: AiTradeRoundTrace[] = [];
    for (let r = 1; r <= 25; r++) t = applyAiTradeTracePhase(t, ev("round_start", r));
    expect(t).toHaveLength(20);
    expect(t[0].round).toBe(6); // 1..5 被裁
    expect(t[t.length - 1].round).toBe(25);
  });
});
