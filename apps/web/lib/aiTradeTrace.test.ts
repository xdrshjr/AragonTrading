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

  it("未知 phase 走 default 容错（向后兼容，不崩）", () => {
    let t = applyAiTradeTracePhase([], ev("round_start", 1));
    t = applyAiTradeTracePhase(t, ev("analysis", 1, { delta: "X" }));
    // @ts-expect-error 故意注入未知 phase，模拟老前端遇新事件。
    const out = applyAiTradeTracePhase(t, ev("totally_unknown", 1, { delta: "Y" }));
    expect(out[0].analysis).toBe("X"); // default: 原样返回，不误改
  });

  it("环形容量：超过 20 轮裁最旧", () => {
    let t: AiTradeRoundTrace[] = [];
    for (let r = 1; r <= 25; r++) t = applyAiTradeTracePhase(t, ev("round_start", r));
    expect(t).toHaveLength(20);
    expect(t[0].round).toBe(6); // 1..5 被裁
    expect(t[t.length - 1].round).toBe(25);
  });
});
