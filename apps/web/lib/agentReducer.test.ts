import { describe, expect, it } from "vitest";
import {
  appendUserMessage,
  buildHistory,
  reduceAgentEvent,
} from "./agentReducer";
import type { AgentMessage, AgentServerEvent, ChatMessage } from "./agentTypes";

function run(events: AgentServerEvent[], initial: ChatMessage[] = []): ChatMessage[] {
  return events.reduce((state, ev) => reduceAgentEvent(state, ev), initial);
}

describe("reduceAgentEvent streaming", () => {
  it("accumulates text deltas into one assistant bubble and ends streaming", () => {
    const state = run([
      { type: "message_start", data: { id: "a1", role: "assistant" } },
      { type: "text_delta", data: { id: "a1", text: "你好" } },
      { type: "text_delta", data: { id: "a1", text: "，世界" } },
      { type: "message_end", data: { id: "a1" } },
    ]);
    expect(state).toHaveLength(1);
    expect(state[0]).toMatchObject({ id: "a1", role: "assistant", text: "你好，世界", streaming: false });
  });

  it("pairs tool_call with its tool_result on the active assistant bubble", () => {
    const state = run([
      { type: "message_start", data: { id: "a1", role: "assistant" } },
      { type: "tool_call", data: { id: "t1", name: "run_backtest", input: { strategyId: 1 } } },
      { type: "tool_result", data: { id: "t1", name: "run_backtest", ok: true, summary: "runId=9" } },
    ]);
    expect(state[0].toolCalls).toHaveLength(1);
    expect(state[0].toolCalls[0]).toMatchObject({
      id: "t1",
      name: "run_backtest",
      ok: true,
      summary: "runId=9",
      done: true,
    });
  });

  it("marks an in-flight tool call as not done until its result arrives", () => {
    const state = run([
      { type: "message_start", data: { id: "a1", role: "assistant" } },
      { type: "tool_call", data: { id: "t1", name: "get_candles", input: {} } },
    ]);
    expect(state[0].toolCalls[0].done).toBe(false);
  });

  it("ignores non-message events (ready/ui_action/done/error)", () => {
    const start = run([{ type: "message_start", data: { id: "a1", role: "assistant" } }]);
    const after = run(
      [
        { type: "ui_action", data: { action: "show_chart", instId: "BTC-USDT-SWAP" } },
        { type: "done", data: { cancelled: false } },
      ],
      start,
    );
    expect(after).toEqual(start);
  });
});

describe("buildHistory", () => {
  it("rebuilds bubbles and back-fills tool results from the tool row (P1-B)", () => {
    const messages: AgentMessage[] = [
      { id: 1, role: "user", ts: 1, blocks: [{ type: "text", text: "回测一下" }] },
      {
        id: 2,
        role: "assistant",
        ts: 2,
        blocks: [
          { type: "text", text: "好的" },
          { type: "tool_use", id: "t1", name: "run_backtest", input: { strategyId: 1 } },
        ],
      },
      {
        id: 3,
        role: "tool",
        ts: 3,
        blocks: [{ type: "tool_result", toolUseId: "t1", ok: true, summary: "runId=5" }],
      },
    ];
    const history = buildHistory(messages);
    expect(history.map((m) => m.role)).toEqual(["user", "assistant"]);
    expect(history[1].text).toBe("好的");
    expect(history[1].toolCalls[0]).toMatchObject({ id: "t1", ok: true, summary: "runId=5", done: true });
  });

  it("history event resets the message list", () => {
    const seeded = appendUserMessage([], "u1", "旧消息");
    const next = reduceAgentEvent(seeded, {
      type: "history",
      data: { messages: [{ id: 9, role: "user", ts: 1, blocks: [{ type: "text", text: "新历史" }] }] },
    });
    expect(next).toHaveLength(1);
    expect(next[0].text).toBe("新历史");
  });
});

describe("appendUserMessage", () => {
  it("appends a non-streaming user bubble", () => {
    const state = appendUserMessage([], "u1", "你好");
    expect(state[0]).toMatchObject({ id: "u1", role: "user", text: "你好", streaming: false });
  });
});
