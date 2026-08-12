import { describe, expect, it } from "vitest";

import {
  CHIP_LABEL,
  KIND_LABEL,
  STATE_LABEL,
  accountLine,
  activitySummary,
  badgeTone,
  channelRows,
  chipView,
  clockLine,
  lastError,
  stateTone,
} from "./okxLink";
import type { OkxConnectivity, OkxLinkChannel, OkxLinkState } from "./okxLinkTypes";

const NOW = 1_786_000_000;

function channel(over: Partial<OkxLinkChannel> = {}): OkxLinkChannel {
  return {
    key: "rest:public",
    kind: "restPublic",
    label: "行情接口(REST)",
    state: "up",
    lastOkTs: NOW - 3,
    lastErrorTs: null,
    lastErrorCode: null,
    lastErrorMessage: null,
    latencyMs: 28,
    consecutiveFailures: 0,
    reconnects1h: 0,
    resubscribes1h: 0,
    gapRepairs1h: 0,
    lastMessageTs: null,
    circuit: "closed",
    circuitOpenUntilTs: null,
    ...over,
  };
}

function snapshot(over: Partial<OkxConnectivity> = {}): OkxConnectivity {
  return {
    ts: NOW,
    overall: "up",
    accountState: "unknown",
    summary: "OKX 链路正常",
    clockOffsetMs: 0,
    clockSyncedTs: NOW - 60,
    channels: [channel()],
    ...over,
  };
}

describe("状态中文（唯一口径）", () => {
  it("覆盖全部五个状态，组件不另抄一份字符串", () => {
    const states: OkxLinkState[] = ["up", "idle", "degraded", "down", "unknown"];
    for (const state of states) {
      expect(STATE_LABEL[state]).toBeTruthy();
      expect(CHIP_LABEL[state]).toBeTruthy();
    }
    expect(Object.keys(KIND_LABEL)).toHaveLength(4);
  });

  it("idle 在面板行里说「空闲」——把闲的说成坏的也是说谎", () => {
    expect(STATE_LABEL.idle).toBe("空闲");
  });

  it("idle 在芯片上等同 up：顶栏不该多出第五种需要被解释的颜色", () => {
    expect(CHIP_LABEL.idle).toBe(CHIP_LABEL.up);
    expect(stateTone("idle")).toBe(stateTone("up"));
  });

  it("Badge 没有 warn，映射只在 badgeTone 一处", () => {
    expect(badgeTone("warn")).toBe("accent");
    expect(badgeTone("long")).toBe("long");
    expect(badgeTone("short")).toBe("short");
  });
});

describe("顶栏芯片", () => {
  it("行情面异常时不得显示「已连接」", () => {
    const view = chipView(snapshot({ overall: "degraded" }), true);
    expect(view.label).toBe("连接不稳");
    expect(view.tone).toBe("warn");
  });

  it("账户面 down 不许把芯片变红", () => {
    const view = chipView(snapshot({ overall: "up", accountState: "down" }), true);
    expect(view.label).toBe("OKX 已连接");
    expect(view.tone).toBe("long");
  });

  it("idle 的芯片仍是绿的「已连接」", () => {
    expect(chipView(snapshot({ overall: "idle" }), true).tone).toBe("long");
  });

  it("端点缺失时回落到 okxUp 的二元展示，绝不白屏", () => {
    expect(chipView(null, true)).toMatchObject({ state: "up", degradedToLegacy: true });
    expect(chipView(null, false)).toMatchObject({ state: "down", degradedToLegacy: true });
    expect(chipView(undefined, null)).toMatchObject({ state: "unknown", degradedToLegacy: true });
  });
});

describe("面板行", () => {
  it("逐通道渲染，中文标签由后端下发（值域属于后端）", () => {
    const rows = channelRows(
      snapshot({
        channels: [
          channel(),
          channel({
            key: "ws:business:BTC-USDT-SWAP:1H:chart",
            kind: "wsBusiness",
            label: "实时流(K 线 · BTC-USDT-SWAP 1H)",
            state: "degraded",
            latencyMs: null,
            lastMessageTs: NOW - 90,
          }),
        ],
      }),
      NOW,
    );
    expect(rows).toHaveLength(2);
    expect(rows[0].detail).toContain("28ms");
    expect(rows[1].label).toContain("BTC-USDT-SWAP");
    expect(rows[1].stateText).toBe("连接不稳");
    expect(rows[1].detail).toContain("最后数据");
  });

  it("从未连通的通道如实说「尚未连通」，不冒充有数据", () => {
    const rows = channelRows(
      snapshot({
        channels: [channel({ state: "down", lastOkTs: null, lastMessageTs: null })],
      }),
      NOW,
    );
    expect(rows[0].detail).toBe("尚未连通");
  });

  it("端点缺失时给空数组而不是抛错", () => {
    expect(channelRows(null)).toEqual([]);
  });
});

describe("摘要 / 错误 / 账户 / 时钟", () => {
  it("滚动计数而非逐事件流水（重连风暴不刷屏）", () => {
    const text = activitySummary(
      snapshot({
        channels: [
          channel({ reconnects1h: 2, gapRepairs1h: 1 }),
          channel({ key: "ws:public:X:1H:chart", kind: "wsPublic", reconnects1h: 1 }),
        ],
      }),
    );
    expect(text).toContain("重连 3 次");
    expect(text).toContain("补齐 K 线 1 次");
  });

  it("无事发生时说「无重连」，不留空白", () => {
    expect(activitySummary(snapshot())).toBe("1 小时内无重连");
  });

  it("最近错误取时间最新的那条", () => {
    const err = lastError(
      snapshot({
        channels: [
          channel({ lastErrorTs: NOW - 100, lastErrorCode: "OLD", lastErrorMessage: "旧的" }),
          channel({
            key: "ws:business:X:1H:chart",
            lastErrorTs: NOW - 5,
            lastErrorCode: "WS_PONG_TIMEOUT",
            lastErrorMessage: "心跳应答超时，已强制重连",
          }),
        ],
      }),
    );
    expect(err?.code).toBe("WS_PONG_TIMEOUT");
    expect(err?.message).toContain("心跳应答超时");
  });

  it("没有错误就返回 null（不造一个不存在的问题）", () => {
    expect(lastError(snapshot())).toBeNull();
    expect(lastError(null)).toBeNull();
  });

  it("没配实盘（accountState=unknown）时不显示账户行", () => {
    expect(accountLine(snapshot())).toBeNull();
    expect(accountLine(snapshot({ accountState: "down" }))).toBe("账户接口 已断开");
  });

  it("时钟偏差小于 2 秒不打扰用户（签名容忍是 ±30s）", () => {
    expect(clockLine(snapshot({ clockOffsetMs: 800 }))).toBeNull();
    expect(clockLine(snapshot({ clockOffsetMs: null }))).toBeNull();
    expect(clockLine(snapshot({ clockOffsetMs: 5200 }))).toContain("慢 5.2 秒");
    expect(clockLine(snapshot({ clockOffsetMs: -5200 }))).toContain("快 5.2 秒");
  });
});
