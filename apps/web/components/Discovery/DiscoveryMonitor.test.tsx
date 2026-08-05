import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { emptyRunState } from "@/lib/discovery";
import type { DiscoveryIterationRow, DiscoveryRunState, DiscoveryRunSummary } from "@/lib/discoveryTypes";
import { useAppStore } from "@/lib/store";
import DiscoveryMonitor from "./DiscoveryMonitor";

const contextValue = {
  runs: [] as DiscoveryRunSummary[],
  defaults: null,
  loading: false,
  error: "",
  activeRunId: 1 as number | null,
  selectRun: vi.fn(),
  refresh: vi.fn(),
  goSection: vi.fn(),
};

vi.mock("./DiscoveryLab", () => ({
  useDiscovery: () => contextValue,
}));

function summary(over: Partial<DiscoveryRunSummary> = {}): DiscoveryRunSummary {
  return {
    runId: 1,
    label: "BTC 1H 挖掘",
    instId: "BTC-USDT-SWAP",
    bar: "1H",
    status: "running",
    pauseReason: "",
    iteration: 5,
    keptCount: 3,
    testedCount: 200,
    counters: {},
    startedTs: Math.floor(Date.now() / 1000) - 3600,
    lastIterTs: null,
    finishedTs: null,
    errorCode: "",
    errorMessage: "",
    ...over,
  };
}

function iteration(over: Partial<DiscoveryIterationRow> = {}): DiscoveryIterationRow {
  return {
    index: 5,
    startedTs: 1_700_000_000,
    finishedTs: 1_700_000_030,
    budget: { mutate: 0.25, crossover: 0.25, perturb: 0.15, random: 0.15, llm: 0.2 },
    generated: 40,
    ruleRejected: 12,
    llmRejected: 3,
    validated: 25,
    kept: 2,
    rejectedByReason: { F3: 10, F10: 8 },
    bySource: { mutate: 10, crossover: 10, perturb: 6, random: 6, llm: 8 },
    truncatedByTimeout: 0,
    overrun: false,
    skipped: "",
    durationMs: 30_000,
    llm: { calls: 1, latencyMs: 900, degraded: false, degradedReason: "", promptChars: 100 },
    note: "生成 40／审出 25／入库 2",
    ...over,
  };
}

function setRun(over: Partial<DiscoveryRunState> = {}) {
  useAppStore.setState({
    discoveryRun: { ...emptyRunState(1), connected: true, ...over },
    discoveryRunId: 1,
  });
}

beforeEach(() => {
  contextValue.runs = [summary()];
  contextValue.activeRunId = 1;
  useAppStore.setState({ discoveryRun: null, discoveryRunId: null });
});
afterEach(cleanup);

describe("DiscoveryMonitor", () => {
  it("漏斗各段数字与迭代记录一致（生成 ≥ 审出 ≥ 入库）", () => {
    setRun({ iterations: [iteration()] });
    render(<DiscoveryMonitor />);
    expect(screen.getByText("生成")).toBeTruthy();
    // 40 生成 − 12 规则拒 − 3 LLM 拒 = 25 审出，与 validated 一致。
    expect(screen.getAllByText("25").length).toBeGreaterThan(0);
    expect(screen.getByText("F3")).toBeTruthy();
  });

  it("样本不足时入库率显示「样本不足」而不是外推的百分比", () => {
    setRun({ counters: { tested: 3, kept: 1, rejected: {} }, iterations: [] });
    render(<DiscoveryMonitor />);
    expect(screen.getAllByText(/样本不足/).length).toBeGreaterThan(0);
  });

  it("断线时显示明确的「重连中」，绝不假装还在跑", () => {
    setRun({ connected: false, iterations: [] });
    render(<DiscoveryMonitor />);
    expect(screen.getByText(/实时连接已断开，重连中/)).toBeTruthy();
  });

  it("重建候选池期间显示明确提示（而不是「已在挖掘」）", () => {
    setRun({ rebuilding: true, iterations: [] });
    render(<DiscoveryMonitor />);
    expect(screen.getByText(/正在重建候选池/)).toBeTruthy();
  });

  it("LLM 未配置时明说已退化为纯演化，入库标准不变", () => {
    setRun({
      iterations: [],
      llmUsage: { callsThisHour: 0, limit: 0, sinceProcessStart: true, enabled: false },
    });
    render(<DiscoveryMonitor />);
    expect(screen.getByText(/纯演化搜索/)).toBeTruthy();
  });

  it("LLM 启用时标注「本小时用量自进程启动起算」（不许让用户误以为跨重启累计）", () => {
    setRun({
      iterations: [],
      llmUsage: { callsThisHour: 5, limit: 24, sinceProcessStart: true, enabled: true },
    });
    render(<DiscoveryMonitor />);
    expect(screen.getByText(/本小时 5 \/ 24 次/)).toBeTruthy();
    expect(screen.getByText(/进程启动/)).toBeTruthy();
  });

  it("暂停原因中文化显示，给出可执行的下一步语境", () => {
    contextValue.runs = [summary({ status: "paused", pauseReason: "data_unavailable" })];
    setRun({ status: "paused", pauseReason: "data_unavailable", iterations: [] });
    render(<DiscoveryMonitor />);
    expect(screen.getByText(/行情数据暂不可达/)).toBeTruthy();
  });

  it("没有选中任务时给出引导而不是空白", () => {
    contextValue.activeRunId = null;
    render(<DiscoveryMonitor />);
    expect(screen.getByText(/还没有选中挖掘任务/)).toBeTruthy();
  });

  it("没有 FSA 冻结时解释这个机制在做什么（空态有引导）", () => {
    setRun({ iterations: [], fsaBans: [] });
    render(<DiscoveryMonitor />);
    expect(screen.getByText(/超过 15%/)).toBeTruthy();
  });

  // ---- 三条策略轴的增量 ----
  it("多轴时逐轴显示候选池规模与已测数（BL-14：计数一律逐轴）", () => {
    setRun({
      iterations: [],
      axes: ["factor", "strategy_param"],
      poolSizes: { factor: 12, strategy_param: 3 },
      axisSnapshots: { factor: { tested: 800 }, strategy_param: { tested: 40 } },
    });
    render(<DiscoveryMonitor />);
    expect(screen.getByText("各轴候选池")).toBeTruthy();
    expect(screen.getByText("12")).toBeTruthy();
    expect(screen.getByText("已测 40")).toBeTruthy();
  });

  it("单轴时不渲染多余的逐轴卡片（默认路径观感零变化，R-15）", () => {
    setRun({ iterations: [], axes: ["factor"], poolSizes: { factor: 12 } });
    render(<DiscoveryMonitor />);
    expect(screen.queryByText("各轴候选池")).toBeNull();
  });

  it("把逐轴计数与让渡记录传给漏斗组件（BL-9 的落点在 DiscoveryFunnel）", () => {
    setRun({
      axes: ["factor", "strategy_combo"],
      iterations: [
        iteration({
          perAxis: {
            factor: { generated: 30, ruleRejected: 0, llmRejected: 0, validated: 30,
                      kept: 2, truncated: 0, truncatedByBacktestCap: 0,
                      rejectedByReason: {} },
            strategy_combo: { generated: 0, ruleRejected: 0, llmRejected: 0, validated: 0,
                              kept: 0, truncated: 0, truncatedByBacktestCap: 0,
                              rejectedByReason: {} },
          },
          yieldedByPool: {
            strategy_combo: { count: 10, reason: "upstream_pool_too_small",
                              toAxis: "factor" },
          },
        }),
      ],
    });
    render(<DiscoveryMonitor />);
    expect(screen.getByText("本轮让渡")).toBeTruthy();
    expect(screen.getByText(/策略轮动组合 10/)).toBeTruthy();
  });
});
