import { beforeEach, describe, expect, it } from "vitest";
import { DEFAULT_COMBO_POLICY } from "./combo";
import { FALLBACK_INSTRUMENTS } from "./instruments";
import {
  DEFAULT_DEPTH_PANEL_RATIO,
  DEFAULT_RIGHT_PANEL_WIDTH,
  DEFAULT_SIM_BALANCE,
  DEFAULT_WF_PARAMS,
  useAppStore,
} from "./store";

describe("app store — account mode & sim balance", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it("setSimBalance clamps to [100, 1e8]", () => {
    useAppStore.getState().setSimBalance(-5);
    expect(useAppStore.getState().simBalance).toBe(100);
    useAppStore.getState().setSimBalance(1e12);
    expect(useAppStore.getState().simBalance).toBe(1e8);
    useAppStore.getState().setSimBalance(50000);
    expect(useAppStore.getState().simBalance).toBe(50000);
  });

  it("setSimBalance ignores non-finite input", () => {
    useAppStore.getState().setSimBalance(50000);
    useAppStore.getState().setSimBalance(Number.NaN);
    expect(useAppStore.getState().simBalance).toBe(50000);
    useAppStore.getState().setSimBalance(Number.POSITIVE_INFINITY);
    expect(useAppStore.getState().simBalance).toBe(50000);
  });

  it("setAccountMode writes only the account mode field", () => {
    useAppStore.getState().setAccountMode("live");
    expect(useAppStore.getState().accountMode).toBe("live");
    useAppStore.getState().setAccountMode("sim");
    expect(useAppStore.getState().accountMode).toBe("sim");
  });

  it("migrates v4 persisted state to v5 with account defaults", async () => {
    localStorage.setItem(
      "aragon-ui",
      JSON.stringify({
        state: { instId: "ETH-USDT-SWAP", bar: "4H", theme: "dark" },
        version: 4,
      }),
    );
    await useAppStore.persist.rehydrate();
    const s = useAppStore.getState();
    // v5 仅新增 simBalance 默认；accountMode 的默认在 v16 迁移翻为 "live"（见专属用例），故此处不断言。
    expect(s.simBalance).toBe(DEFAULT_SIM_BALANCE);
    // 既有持久化字段仍恢复
    expect(s.instId).toBe("ETH-USDT-SWAP");
    expect(s.bar).toBe("4H");
  });

  it("initializes a fresh install with account mode defaulting to live", () => {
    // 全新安装只走初始化器（不触发 migrate）：实盘模式默认开启。
    // 读 initialState 而非 getState，避免依赖前序用例对单例 store 的改动顺序。
    expect(useAppStore.getInitialState().accountMode).toBe("live");
  });

  it("migrates pre-v16 persisted account mode to live", async () => {
    localStorage.setItem(
      "aragon-ui",
      JSON.stringify({
        state: { instId: "ETH-USDT-SWAP", bar: "4H", accountMode: "sim" },
        version: 15,
      }),
    );
    await useAppStore.persist.rehydrate();
    const s = useAppStore.getState();
    // v16 迁移把老用户持久化的 "sim" 升到 "live"（无就绪账户时由 AccountModeSwitch 守门回落）。
    expect(s.accountMode).toBe("live");
    // 既有持久化字段仍恢复
    expect(s.instId).toBe("ETH-USDT-SWAP");
    expect(s.bar).toBe("4H");
  });

  it("does not persist runtime paper batch handles", async () => {
    useAppStore.getState().setPaperBatchGroupId("group-1");
    useAppStore.getState().setPaperStreamsEnabled(true);
    useAppStore.getState().setPaperSessionsByStrategyId({
      1: {
        strategyId: 1,
        strategyName: "Alpha",
        sessionId: "session-1",
        runId: 101,
        color: "#111111",
        state: "running",
      },
    });

    const raw = localStorage.getItem("aragon-ui");
    expect(raw).toBeTruthy();
    expect(raw).not.toContain("paperBatchGroupId");
    expect(raw).not.toContain("paperSessionsByStrategyId");
    expect(raw).not.toContain("paperStreamsEnabled");
    expect(raw).not.toContain("session-1");
  });
});

describe("app store — watchlist & instrument sidebar tab", () => {
  beforeEach(() => {
    localStorage.clear();
    useAppStore.setState({ watchlist: [...FALLBACK_INSTRUMENTS], instSidebarTab: "watch" });
  });

  it("toggleWatchlist adds (prepends) then removes, idempotently", () => {
    useAppStore.setState({ watchlist: [] });
    useAppStore.getState().toggleWatchlist("PEPE-USDT");
    expect(useAppStore.getState().watchlist).toEqual(["PEPE-USDT"]);
    useAppStore.getState().toggleWatchlist("DOGE-USDT");
    expect(useAppStore.getState().watchlist).toEqual(["DOGE-USDT", "PEPE-USDT"]);
    useAppStore.getState().toggleWatchlist("PEPE-USDT");
    expect(useAppStore.getState().watchlist).toEqual(["DOGE-USDT"]);
  });

  it("addToWatchlist dedupes and removeFromWatchlist is a no-op when absent", () => {
    useAppStore.setState({ watchlist: ["BTC-USDT-SWAP"] });
    useAppStore.getState().addToWatchlist("BTC-USDT-SWAP");
    expect(useAppStore.getState().watchlist).toEqual(["BTC-USDT-SWAP"]);
    useAppStore.getState().removeFromWatchlist("NOPE-USDT");
    expect(useAppStore.getState().watchlist).toEqual(["BTC-USDT-SWAP"]);
    useAppStore.getState().removeFromWatchlist("BTC-USDT-SWAP");
    expect(useAppStore.getState().watchlist).toEqual([]);
  });

  it("persists watchlist and instSidebarTab to localStorage", () => {
    useAppStore.getState().setInstSidebarTab("swap");
    useAppStore.getState().toggleWatchlist("OP-USDT");
    const raw = localStorage.getItem("aragon-ui");
    expect(raw).toBeTruthy();
    const persisted = JSON.parse(raw as string);
    expect(persisted.state.instSidebarTab).toBe("swap");
    expect(persisted.state.watchlist).toContain("OP-USDT");
  });

  it("migrates v5 persisted state to v6 with default watchlist seed and tab", async () => {
    localStorage.setItem(
      "aragon-ui",
      JSON.stringify({
        state: { instId: "SOL-USDT-SWAP", bar: "1H", theme: "dark", accountMode: "sim" },
        version: 5,
      }),
    );
    await useAppStore.persist.rehydrate();
    const s = useAppStore.getState();
    expect(s.watchlist).toEqual([...FALLBACK_INSTRUMENTS]);
    expect(s.instSidebarTab).toBe("watch");
    // 既有持久化字段仍恢复
    expect(s.instId).toBe("SOL-USDT-SWAP");
  });
});

describe("app store — settings center (v9)", () => {
  beforeEach(() => {
    localStorage.clear();
    // store 为模块单例：重置瞬态 showSettings，避免跨用例残留。
    useAppStore.setState({ showSettings: false });
  });

  it("setShowSettings toggles transient flag without persisting it", () => {
    useAppStore.getState().setShowSettings(true);
    expect(useAppStore.getState().showSettings).toBe(true);
    const raw = localStorage.getItem("aragon-ui");
    expect(raw).toBeTruthy();
    // showSettings 为瞬态：绝不写入 localStorage（刷新回工作台）。
    expect(raw).not.toContain("showSettings");
  });

  it("persists settingsSection", () => {
    useAppStore.getState().setSettingsSection("ai-models");
    const persisted = JSON.parse(localStorage.getItem("aragon-ui") as string);
    expect(persisted.state.settingsSection).toBe("ai-models");
    expect(persisted.state.showSettings).toBeUndefined();
  });

  it("migrates v8 persisted state to v9 with default settingsSection", async () => {
    localStorage.setItem(
      "aragon-ui",
      JSON.stringify({
        state: { instId: "ETH-USDT-SWAP", rightTab: "agent" },
        version: 8,
      }),
    );
    await useAppStore.persist.rehydrate();
    const s = useAppStore.getState();
    expect(s.settingsSection).toBe("ai-models");
    expect(s.showSettings).toBe(false); // 瞬态默认值，不来自持久化
    expect(s.instId).toBe("ETH-USDT-SWAP");
  });
});

describe("app store — strategy combination (v11)", () => {
  beforeEach(() => {
    localStorage.clear();
    useAppStore.setState({ comboDraft: { memberIds: [], policy: { ...DEFAULT_COMBO_POLICY } } });
  });

  it("persists comboDraft", () => {
    useAppStore.getState().setComboDraft({
      memberIds: [1, 2, 3],
      policy: { ...DEFAULT_COMBO_POLICY, rebalanceBars: 48 },
    });
    const persisted = JSON.parse(localStorage.getItem("aragon-ui") as string);
    expect(persisted.state.comboDraft.memberIds).toEqual([1, 2, 3]);
    expect(persisted.state.comboDraft.policy.rebalanceBars).toBe(48);
  });

  it("migrates v10 persisted state to v11 with default comboDraft", async () => {
    localStorage.setItem(
      "aragon-ui",
      JSON.stringify({
        state: { instId: "ETH-USDT-SWAP", mode: "backtest" },
        version: 10,
      }),
    );
    await useAppStore.persist.rehydrate();
    const s = useAppStore.getState();
    expect(s.comboDraft.memberIds).toEqual([]);
    expect(s.comboDraft.policy.selectionMetric).toBe("sharpe");
    expect(s.instId).toBe("ETH-USDT-SWAP");
  });
});

describe("app store — workstation panel sizes (v12)", () => {
  beforeEach(() => {
    localStorage.clear();
    // store 为模块单例：把两面板尺寸复位到默认，避免跨用例残留。
    useAppStore.setState({
      rightPanelWidth: DEFAULT_RIGHT_PANEL_WIDTH,
      depthPanelRatio: DEFAULT_DEPTH_PANEL_RATIO,
    });
  });

  it("setRightPanelWidth clamps to [320, 640]", () => {
    useAppStore.getState().setRightPanelWidth(-5);
    expect(useAppStore.getState().rightPanelWidth).toBe(320);
    useAppStore.getState().setRightPanelWidth(9999);
    expect(useAppStore.getState().rightPanelWidth).toBe(640);
    useAppStore.getState().setRightPanelWidth(500);
    expect(useAppStore.getState().rightPanelWidth).toBe(500);
  });

  it("setRightPanelWidth ignores non-finite input", () => {
    useAppStore.getState().setRightPanelWidth(500);
    useAppStore.getState().setRightPanelWidth(Number.NaN);
    expect(useAppStore.getState().rightPanelWidth).toBe(500);
    useAppStore.getState().setRightPanelWidth(Number.POSITIVE_INFINITY);
    expect(useAppStore.getState().rightPanelWidth).toBe(500);
  });

  it("setDepthPanelRatio clamps to [0.10, 0.30]", () => {
    useAppStore.getState().setDepthPanelRatio(0);
    expect(useAppStore.getState().depthPanelRatio).toBeCloseTo(0.1, 10);
    useAppStore.getState().setDepthPanelRatio(1);
    expect(useAppStore.getState().depthPanelRatio).toBeCloseTo(0.3, 10);
    useAppStore.getState().setDepthPanelRatio(0.2);
    expect(useAppStore.getState().depthPanelRatio).toBeCloseTo(0.2, 10);
  });

  it("setDepthPanelRatio ignores non-finite input", () => {
    useAppStore.getState().setDepthPanelRatio(0.2);
    useAppStore.getState().setDepthPanelRatio(Number.NaN);
    expect(useAppStore.getState().depthPanelRatio).toBeCloseTo(0.2, 10);
    useAppStore.getState().setDepthPanelRatio(Number.POSITIVE_INFINITY);
    expect(useAppStore.getState().depthPanelRatio).toBeCloseTo(0.2, 10);
  });

  it("persists rightPanelWidth and depthPanelRatio", () => {
    useAppStore.getState().setRightPanelWidth(420);
    useAppStore.getState().setDepthPanelRatio(0.22);
    const persisted = JSON.parse(localStorage.getItem("aragon-ui") as string);
    expect(persisted.state.rightPanelWidth).toBe(420);
    expect(persisted.state.depthPanelRatio).toBeCloseTo(0.22, 10);
  });

  it("migrates v11 persisted state to v12 with default panel sizes", async () => {
    localStorage.setItem(
      "aragon-ui",
      JSON.stringify({
        state: { instId: "ETH-USDT-SWAP", chartSplitRatio: 0.7 },
        version: 11,
      }),
    );
    await useAppStore.persist.rehydrate();
    const s = useAppStore.getState();
    expect(s.rightPanelWidth).toBe(DEFAULT_RIGHT_PANEL_WIDTH);
    expect(s.depthPanelRatio).toBe(DEFAULT_DEPTH_PANEL_RATIO);
    // 既有持久化字段仍恢复
    expect(s.instId).toBe("ETH-USDT-SWAP");
    expect(s.chartSplitRatio).toBe(0.7);
  });
});

describe("app store — advanced backtest / walk-forward (v13)", () => {
  beforeEach(() => {
    localStorage.clear();
    useAppStore.setState({
      backtestKind: "simple",
      wfParams: { ...DEFAULT_WF_PARAMS },
      walkForwardResult: null,
    });
  });

  it("setBacktestKind switches kind without touching chartView", () => {
    useAppStore.setState({ chartView: "live-follow" });
    useAppStore.getState().setBacktestKind("walkforward");
    expect(useAppStore.getState().backtestKind).toBe("walkforward");
    // 切到 walkforward 不联动 chartView（无单条净值，左图维持 K 线视图）。
    expect(useAppStore.getState().chartView).toBe("live-follow");
  });

  it("setWfParams merges partial updates", () => {
    useAppStore.getState().setWfParams({ windowBars: 2000 });
    expect(useAppStore.getState().wfParams).toEqual({
      windowBars: 2000,
      stepBars: 200,
      resetCapital: true,
    });
    useAppStore.getState().setWfParams({ resetCapital: false });
    expect(useAppStore.getState().wfParams.resetCapital).toBe(false);
    expect(useAppStore.getState().wfParams.windowBars).toBe(2000);
  });

  it("persists backtestKind and wfParams but not walkForwardResult", () => {
    useAppStore.getState().setBacktestKind("walkforward");
    useAppStore.getState().setWfParams({ stepBars: 500 });
    useAppStore.getState().setWalkForwardResult({
      runId: 1,
      summary: {
        windowCount: 1,
        profitableWindows: 1,
        profitableRate: 1,
        medianReturn: 0.1,
        avgReturn: 0.1,
        worstReturn: 0.1,
        bestReturn: 0.1,
        avgMaxDrawdown: -0.02,
        stabilityScore: 0.8,
        uncoveredBars: 0,
        notes: [],
      },
      windows: [],
    });
    const persisted = JSON.parse(localStorage.getItem("aragon-ui") as string);
    expect(persisted.state.backtestKind).toBe("walkforward");
    expect(persisted.state.wfParams.stepBars).toBe(500);
    // walkForwardResult 为运行期产物，绝不持久化（避免刷新后陈旧）。
    expect(persisted.state.walkForwardResult).toBeUndefined();
  });

  it("migrates v12 persisted state to v13 with default advanced-backtest fields", async () => {
    localStorage.setItem(
      "aragon-ui",
      JSON.stringify({
        state: { instId: "ETH-USDT-SWAP", mode: "backtest" },
        version: 12,
      }),
    );
    await useAppStore.persist.rehydrate();
    const s = useAppStore.getState();
    expect(s.backtestKind).toBe("simple");
    expect(s.wfParams).toEqual(DEFAULT_WF_PARAMS);
    // 既有持久化字段仍恢复
    expect(s.instId).toBe("ETH-USDT-SWAP");
  });
});

describe("app store — combo merged into backtest list (v14)", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it("migrates v13 persisted mode:'combo' to backtest (combo tab removed)", async () => {
    localStorage.setItem(
      "aragon-ui",
      JSON.stringify({
        state: { instId: "ETH-USDT-SWAP", mode: "combo" },
        version: 13,
      }),
    );
    await useAppStore.persist.rehydrate();
    const s = useAppStore.getState();
    // 历史的「组合」模式已删，安全收敛到回测，避免水合到不存在的视图。
    expect(s.mode).toBe("backtest");
    expect(s.instId).toBe("ETH-USDT-SWAP");
  });

  it("leaves a non-combo persisted mode untouched across the v14 migration", async () => {
    localStorage.setItem(
      "aragon-ui",
      JSON.stringify({
        state: { instId: "SOL-USDT-SWAP", mode: "paper" },
        version: 13,
      }),
    );
    await useAppStore.persist.rehydrate();
    expect(useAppStore.getState().mode).toBe("paper");
  });
});

describe("app store - strategy list tab (v15)", () => {
  beforeEach(() => {
    localStorage.clear();
    useAppStore.setState({ mode: "backtest", strategyListTab: "regular" });
  });

  it("setStrategyListTab updates state", () => {
    useAppStore.getState().setStrategyListTab("combo");
    expect(useAppStore.getState().strategyListTab).toBe("combo");
  });

  it("persists strategyListTab to localStorage", () => {
    useAppStore.getState().setStrategyListTab("combo");
    const persisted = JSON.parse(localStorage.getItem("aragon-ui") as string);
    expect(persisted.state.strategyListTab).toBe("combo");
  });

  it("migrates v14 persisted state to regular tab by default", async () => {
    localStorage.setItem(
      "aragon-ui",
      JSON.stringify({
        state: { instId: "ETH-USDT-SWAP", mode: "backtest" },
        version: 14,
      }),
    );
    await useAppStore.persist.rehydrate();
    const state = useAppStore.getState();
    expect(state.strategyListTab).toBe("regular");
    expect(state.instId).toBe("ETH-USDT-SWAP");
  });

  it("migrates legacy combo mode to backtest with combo tab selected", async () => {
    localStorage.setItem(
      "aragon-ui",
      JSON.stringify({
        state: { instId: "SOL-USDT-SWAP", mode: "combo" },
        version: 13,
      }),
    );
    await useAppStore.persist.rehydrate();
    const state = useAppStore.getState();
    expect(state.mode).toBe("backtest");
    expect(state.strategyListTab).toBe("combo");
    expect(state.instId).toBe("SOL-USDT-SWAP");
  });
});
