// 把 ui_action directive 映射为 store setter 调用序列（纯函数，带单测）(Agent Trading Copilot §3.2)。
//
// 顺序要点：setInst / setBar 会把 runId/chartView 复位到 live-follow，setMode("backtest") 也会把
// chartView 设为 live-follow，故必须先调它们、最后再 setRunId + setChartView("run-range")，
// 否则后置的复位会冲掉目标视图（对照 store.ts 真实 setter 语义，P2-4）。
import type { Bar, ChartView, Mode } from "./types";
import type { ShowChartAction, UiActionStore } from "./agentTypes";

export type StoreOp =
  | { fn: "setInst"; args: [string] }
  | { fn: "setBar"; args: [Bar] }
  | { fn: "setBacktestRange"; args: [number, number] }
  | { fn: "setMode"; args: [Mode] }
  | { fn: "setRunId"; args: [number | null] }
  | { fn: "setChartView"; args: [ChartView] };

/** 把 show_chart directive 解析为有序的 store 操作（不直接触碰 store，便于单测）。 */
export function showChartOps(action: ShowChartAction): StoreOp[] {
  const ops: StoreOp[] = [];
  if (action.instId) ops.push({ fn: "setInst", args: [action.instId] });
  if (action.bar) ops.push({ fn: "setBar", args: [action.bar] });
  if (typeof action.startTs === "number" && typeof action.endTs === "number") {
    ops.push({ fn: "setBacktestRange", args: [action.startTs, action.endTs] });
  }
  if (typeof action.runId === "number") {
    // 叠加某次 run 的买卖点：进 backtest 模式 → 设 runId → 切到该 run 区间视图。
    ops.push({ fn: "setMode", args: ["backtest"] });
    ops.push({ fn: "setRunId", args: [action.runId] });
    ops.push({ fn: "setChartView", args: ["run-range"] });
  } else {
    // 仅切标的/周期/区间：保持实时跟随。
    ops.push({ fn: "setChartView", args: ["live-follow"] });
  }
  return ops;
}

/** 把 ops 应用到真实 store（按序调用对应 setter）。 */
export function applyOps(ops: StoreOp[], store: UiActionStore): void {
  for (const op of ops) {
    switch (op.fn) {
      case "setInst":
        store.setInst(...op.args);
        break;
      case "setBar":
        store.setBar(...op.args);
        break;
      case "setBacktestRange":
        store.setBacktestRange(...op.args);
        break;
      case "setMode":
        store.setMode(...op.args);
        break;
      case "setRunId":
        store.setRunId(...op.args);
        break;
      case "setChartView":
        store.setChartView(...op.args);
        break;
    }
  }
}

/** 便捷封装：解析并应用一个 show_chart directive。 */
export function applyShowChart(action: ShowChartAction, store: UiActionStore): void {
  applyOps(showChartOps(action), store);
}
