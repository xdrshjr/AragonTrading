import { describe, expect, it } from "vitest";
import {
  appendUserMessage,
  buildHistory,
  dropStreaming,
  reconcileHistory,
  reduceAgentEvent,
  settleStreaming,
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

describe("message_end usage (H5)", () => {
  it("attaches normalized usage to the bubble and ends streaming", () => {
    const state = run([
      { type: "message_start", data: { id: "a1", role: "assistant" } },
      { type: "text_delta", data: { id: "a1", text: "hi" } },
      { type: "message_end", data: { id: "a1", usage: { inputTokens: 3, outputTokens: 5 } } },
    ]);
    expect(state[0].usage).toEqual({ inputTokens: 3, outputTokens: 5 });
    expect(state[0].streaming).toBe(false);
  });

  it("tolerates message_end without usage (老前端 / 取不到即省略)", () => {
    const state = run([
      { type: "message_start", data: { id: "a1", role: "assistant" } },
      { type: "message_end", data: { id: "a1" } },
    ]);
    expect(state[0].usage).toBeUndefined();
    expect(state[0].streaming).toBe(false);
  });
});

describe("settleStreaming (H8a / RN-3)", () => {
  it("collapses an in-flight streaming bubble to interrupted", () => {
    const state: ChatMessage[] = [
      { id: "a1", role: "assistant", text: "半句", thinking: "", toolCalls: [], streaming: true },
    ];
    const next = settleStreaming(state);
    expect(next[0]).toMatchObject({ streaming: false, interrupted: true });
  });

  it("leaves already-settled bubbles untouched", () => {
    const state: ChatMessage[] = [
      { id: "a1", role: "assistant", text: "完成", thinking: "", toolCalls: [], streaming: false },
    ];
    expect(settleStreaming(state)).toEqual(state);
  });
});

describe("reconcileHistory (RN-1)", () => {
  const histRound: AgentMessage[] = [
    { id: 1, role: "user", ts: 1, blocks: [{ type: "text", text: "回测一下" }] },
    { id: 2, role: "assistant", ts: 2, blocks: [{ type: "text", text: "结果如下" }] },
  ];

  it("(a) initializes from history when local is empty (equiv overwrite)", () => {
    const out = reconcileHistory([], histRound);
    expect(out.map((m) => m.role)).toEqual(["user", "assistant"]);
    expect(out[1].text).toBe("结果如下");
  });

  it("(b) rebuilds from DB history without duplicating completed rounds (uuid != DB-int)", () => {
    // 本地已有用客户端 uuid 流式完成的同轮气泡（与 DB-int 不同 id 空间）。
    const prevLocal: ChatMessage[] = [
      { id: "u-xxx", role: "user", text: "回测一下", thinking: "", toolCalls: [], streaming: false },
      { id: "deadbeef", role: "assistant", text: "结果如下", thinking: "", toolCalls: [], streaming: false },
    ];
    const out = reconcileHistory(prevLocal, histRound);
    // 旧 merge-by-id 会因 uuid≠DB-int 而重复；reconcile 以 DB 整体重建 → 不重复。
    expect(out).toHaveLength(2);
    expect(out.map((m) => m.id)).toEqual(["1", "2"]);
  });

  it("(c) preserves the single in-flight streaming bubble", () => {
    const prevLocal: ChatMessage[] = [
      { id: "stream-1", role: "assistant", text: "进行中", thinking: "", toolCalls: [], streaming: true },
    ];
    const out = reconcileHistory(prevLocal, histRound);
    expect(out).toHaveLength(3);
    expect(out[2]).toMatchObject({ id: "stream-1", streaming: true });
  });

  it("(d) cancelled half-round settled by H8a appears once on reconnect, no orphan", () => {
    // H8a 已把半轮收束为 streaming:false；DB history 含该半轮（落盘）。
    const prevLocal: ChatMessage[] = [
      { id: "u-1", role: "user", text: "回测一下", thinking: "", toolCalls: [], streaming: false },
      {
        id: "half-uuid", role: "assistant", text: "刚开口便断",
        thinking: "", toolCalls: [], streaming: false, interrupted: true,
      },
    ];
    const histWithHalf: AgentMessage[] = [
      { id: 1, role: "user", ts: 1, blocks: [{ type: "text", text: "回测一下" }] },
      { id: 2, role: "assistant", ts: 2, blocks: [{ type: "text", text: "刚开口便断" }] },
    ];
    const out = reconcileHistory(prevLocal, histWithHalf);
    // 半轮只现一次（来自 DB 重建）；本地非 streaming 气泡不保留 → 无重复、无孤儿。
    expect(out).toHaveLength(2);
    expect(out.filter((m) => m.text === "刚开口便断")).toHaveLength(1);
  });
});

describe("dropStreaming (RN-1)", () => {
  it("removes in-flight streaming bubbles, keeps settled ones", () => {
    const state: ChatMessage[] = [
      { id: "u1", role: "user", text: "q", thinking: "", toolCalls: [], streaming: false },
      { id: "done", role: "assistant", text: "完成", thinking: "", toolCalls: [], streaming: false },
      { id: "stream", role: "assistant", text: "半截", thinking: "", toolCalls: [], streaming: true },
    ];
    expect(dropStreaming(state).map((m) => m.id)).toEqual(["u1", "done"]);
  });

  it("after resumed{ok:false}, dropStreaming + reconcileHistory yields no duplicate bubble", () => {
    // 本地：用户气泡 + 在途 uuid 流式气泡（同一轮，与 DB-int 不同 id 空间）。
    const local: ChatMessage[] = [
      { id: "u1", role: "user", text: "回测一下", thinking: "", toolCalls: [], streaming: false },
      { id: "uuid-stream", role: "assistant", text: "结果如下", thinking: "", toolCalls: [], streaming: true },
    ];
    // run 已在宽限内终结：先丢弃在途，再以 DB history 权威重建（替代旧的 settleStreaming 标中断）。
    const dropped = dropStreaming(local);
    const hist: AgentMessage[] = [
      { id: 1, role: "user", ts: 1, blocks: [{ type: "text", text: "回测一下" }] },
      { id: 2, role: "assistant", ts: 2, blocks: [{ type: "text", text: "结果如下" }] },
    ];
    const out = reconcileHistory(dropped, hist);
    // 同一轮只出现一次（DB-int 重建），无 uuid 残留重复（RN-1 根治"宽限内完成→重复气泡"）。
    expect(out).toHaveLength(2);
    expect(out.map((m) => m.id)).toEqual(["1", "2"]);
    expect(out.filter((m) => m.text === "结果如下")).toHaveLength(1);
  });
});

describe("reduceAgentEvent resilient events (RS-7/RN-2)", () => {
  it("heartbeat passes through default — message stream untouched, no lastSeq mutation", () => {
    const start = run([{ type: "message_start", data: { id: "a1", role: "assistant" } }]);
    const after = reduceAgentEvent(start, { type: "heartbeat", data: {} } as AgentServerEvent);
    expect(after).toEqual(start);
  });

  it("resumed passes through default — no message mutation", () => {
    const start = run([{ type: "message_start", data: { id: "a1", role: "assistant" } }]);
    const after = reduceAgentEvent(start, {
      type: "resumed",
      data: { ok: true },
    } as AgentServerEvent);
    expect(after).toEqual(start);
  });
});
