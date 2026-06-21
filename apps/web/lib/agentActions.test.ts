import { describe, expect, it, vi } from "vitest";
import { applyOps, showChartOps } from "./agentActions";
import type { ShowChartAction, UiActionStore } from "./agentTypes";

function mockStore(): UiActionStore & { calls: [string, unknown[]][] } {
  const calls: [string, unknown[]][] = [];
  return {
    calls,
    setInst: (...a) => calls.push(["setInst", a]),
    setBar: (...a) => calls.push(["setBar", a]),
    setBacktestRange: (...a) => calls.push(["setBacktestRange", a]),
    setMode: (...a) => calls.push(["setMode", a]),
    setRunId: (...a) => calls.push(["setRunId", a]),
    setChartView: (...a) => calls.push(["setChartView", a]),
  };
}

describe("showChartOps", () => {
  it("maps a runId directive to backtest run-range in the correct order", () => {
    const action: ShowChartAction = {
      action: "show_chart",
      instId: "BTC-USDT-SWAP",
      bar: "1D",
      startTs: 100,
      endTs: 200,
      runId: 42,
    };
    expect(showChartOps(action)).toEqual([
      { fn: "setInst", args: ["BTC-USDT-SWAP"] },
      { fn: "setBar", args: ["1D"] },
      { fn: "setBacktestRange", args: [100, 200] },
      { fn: "setMode", args: ["backtest"] },
      { fn: "setRunId", args: [42] },
      { fn: "setChartView", args: ["run-range"] },
    ]);
  });

  it("falls back to live-follow when no runId is given", () => {
    const action: ShowChartAction = { action: "show_chart", instId: "ETH-USDT", bar: "1H" };
    const ops = showChartOps(action);
    expect(ops.at(-1)).toEqual({ fn: "setChartView", args: ["live-follow"] });
    expect(ops.some((o) => o.fn === "setMode")).toBe(false);
  });

  it("omits range when start/end are not both present", () => {
    const ops = showChartOps({ action: "show_chart", instId: "BTC-USDT-SWAP", startTs: 1 });
    expect(ops.some((o) => o.fn === "setBacktestRange")).toBe(false);
  });

  it("emits nothing structural but still sets live-follow for an empty directive", () => {
    expect(showChartOps({ action: "show_chart" })).toEqual([
      { fn: "setChartView", args: ["live-follow"] },
    ]);
  });
});

describe("applyOps", () => {
  it("invokes store setters in op order (setInst before setRunId/run-range)", () => {
    const store = mockStore();
    applyOps(
      showChartOps({ action: "show_chart", instId: "BTC-USDT-SWAP", bar: "1D", runId: 7 }),
      store,
    );
    expect(store.calls).toEqual([
      ["setInst", ["BTC-USDT-SWAP"]],
      ["setBar", ["1D"]],
      ["setMode", ["backtest"]],
      ["setRunId", [7]],
      ["setChartView", ["run-range"]],
    ]);
  });

  it("only calls the setters present in the ops list", () => {
    const store = mockStore();
    const setInst = vi.spyOn(store, "setInst");
    applyOps(showChartOps({ action: "show_chart", bar: "5m" }), store);
    expect(setInst).not.toHaveBeenCalled();
    expect(store.calls.map((c) => c[0])).toEqual(["setBar", "setChartView"]);
  });
});
