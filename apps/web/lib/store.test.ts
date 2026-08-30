import { beforeEach, describe, expect, it } from "vitest";
import { DEFAULT_COMBO_POLICY } from "./combo";
import { FALLBACK_INSTRUMENTS } from "./instruments";
import {
  DEFAULT_CHART_INDICATOR_RATIO,
  DEFAULT_DEPTH_PANEL_RATIO,
  DEFAULT_RIGHT_PANEL_WIDTH,
  DEFAULT_SIM_BALANCE,
  DEFAULT_WF_PARAMS,
  useAppStore,
} from "./store";
import type { AiTradeGroupMemberState, AiTradeGroupState } from "./aiTradeTypes";
import type { Trade } from "./types";
import { TRADES_MAX } from "./wireCaps";

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

describe("app store — chart indicator pane split (v22)", () => {
  beforeEach(() => {
    localStorage.clear();
    // store 为模块单例：把主图/副图分栏比例复位到默认，避免跨用例残留。
    useAppStore.setState({ chartIndicatorRatio: DEFAULT_CHART_INDICATOR_RATIO });
  });

  it("setChartIndicatorRatio clamps to [0.40, 0.85]", () => {
    useAppStore.getState().setChartIndicatorRatio(0);
    expect(useAppStore.getState().chartIndicatorRatio).toBeCloseTo(0.4, 10);
    useAppStore.getState().setChartIndicatorRatio(1);
    expect(useAppStore.getState().chartIndicatorRatio).toBeCloseTo(0.85, 10);
    useAppStore.getState().setChartIndicatorRatio(0.6);
    expect(useAppStore.getState().chartIndicatorRatio).toBeCloseTo(0.6, 10);
  });

  it("setChartIndicatorRatio ignores non-finite input", () => {
    useAppStore.getState().setChartIndicatorRatio(0.6);
    useAppStore.getState().setChartIndicatorRatio(Number.NaN);
    expect(useAppStore.getState().chartIndicatorRatio).toBeCloseTo(0.6, 10);
    useAppStore.getState().setChartIndicatorRatio(Number.POSITIVE_INFINITY);
    expect(useAppStore.getState().chartIndicatorRatio).toBeCloseTo(0.6, 10);
  });

  it("persists chartIndicatorRatio", () => {
    useAppStore.getState().setChartIndicatorRatio(0.65);
    const persisted = JSON.parse(localStorage.getItem("aragon-ui") as string);
    expect(persisted.state.chartIndicatorRatio).toBeCloseTo(0.65, 10);
  });

  it("migrates v21 persisted state to v22 with default ratio", async () => {
    localStorage.setItem(
      "aragon-ui",
      JSON.stringify({
        state: { instId: "ETH-USDT-SWAP", chartSplitRatio: 0.7 },
        version: 21,
      }),
    );
    await useAppStore.persist.rehydrate();
    const s = useAppStore.getState();
    expect(s.chartIndicatorRatio).toBe(DEFAULT_CHART_INDICATOR_RATIO);
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

  it("setStrategyListTab supports the factor tab and persists it", () => {
    useAppStore.getState().setStrategyListTab("factor");
    expect(useAppStore.getState().strategyListTab).toBe("factor");
    const persisted = JSON.parse(localStorage.getItem("aragon-ui") as string);
    expect(persisted.state.strategyListTab).toBe("factor");
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

describe("app store — left nav rail primary view (v19)", () => {
  beforeEach(() => {
    localStorage.clear();
    // store 为模块单例：复位一级视图，避免跨用例残留。
    useAppStore.setState({ activeView: "trade" });
  });

  it("defaults activeView to trade on a fresh install", () => {
    // 全新安装只走初始化器（不触发 migrate）：默认在「交易」工作台。
    expect(useAppStore.getInitialState().activeView).toBe("trade");
  });

  it("setActiveView switches the primary view", () => {
    useAppStore.getState().setActiveView("factor");
    expect(useAppStore.getState().activeView).toBe("factor");
    useAppStore.getState().setActiveView("trade");
    expect(useAppStore.getState().activeView).toBe("trade");
  });

  it("persists activeView to localStorage", () => {
    useAppStore.getState().setActiveView("factor");
    const persisted = JSON.parse(localStorage.getItem("aragon-ui") as string);
    expect(persisted.state.activeView).toBe("factor");
  });

  it("migrates pre-v19 persisted state to v19 with activeView defaulting to trade", async () => {
    localStorage.setItem(
      "aragon-ui",
      JSON.stringify({
        state: { instId: "ETH-USDT-SWAP", factorLabSection: "catalog" },
        version: 18,
      }),
    );
    await useAppStore.persist.rehydrate();
    const s = useAppStore.getState();
    expect(s.activeView).toBe("trade");
    // 既有持久化字段仍恢复
    expect(s.instId).toBe("ETH-USDT-SWAP");
    expect(s.factorLabSection).toBe("catalog");
  });
});

describe("app store — auto-trade group members (v20)", () => {
  beforeEach(() => {
    localStorage.clear();
    useAppStore.setState({
      watchlist: ["BTC-USDT-SWAP", "ETH-USDT-SWAP", "SOL-USDT-SWAP"],
      autoTradeMembers: [],
      autoTradeAllocationMode: "even",
      liveAutoGroup: null,
    });
  });

  it("addAutoTradeMember adds with the remaining headroom and only for watchlist items", () => {
    useAppStore.getState().addAutoTradeMember("BTC-USDT-SWAP");
    expect(useAppStore.getState().autoTradeMembers).toEqual([
      { instId: "BTC-USDT-SWAP", allocationPct: 100 },
    ]);
    useAppStore.getState().addAutoTradeMember("ETH-USDT-SWAP");
    expect(useAppStore.getState().autoTradeMembers.map((m) => m.allocationPct)).toEqual([100, 0]);
    // 非自选标的不可加入
    useAppStore.getState().addAutoTradeMember("DOGE-USDT-SWAP");
    expect(useAppStore.getState().autoTradeMembers).toHaveLength(2);
    // 重复加入幂等
    useAppStore.getState().addAutoTradeMember("BTC-USDT-SWAP");
    expect(useAppStore.getState().autoTradeMembers).toHaveLength(2);
  });

  it("setAutoTradeAllocation clamps to [1, 100 - others]", () => {
    useAppStore.setState({
      autoTradeMembers: [
        { instId: "BTC-USDT-SWAP", allocationPct: 50 },
        { instId: "ETH-USDT-SWAP", allocationPct: 30 },
      ],
    });
    useAppStore.getState().setAutoTradeAllocation("BTC-USDT-SWAP", 95);
    expect(useAppStore.getState().autoTradeMembers[0].allocationPct).toBe(70); // 100 - 30
    useAppStore.getState().setAutoTradeAllocation("BTC-USDT-SWAP", 0);
    expect(useAppStore.getState().autoTradeMembers[0].allocationPct).toBe(1);
  });

  it("evenSplitAutoTradeMembers splits to sum exactly 100", () => {
    useAppStore.setState({
      autoTradeMembers: [
        { instId: "BTC-USDT-SWAP", allocationPct: 50 },
        { instId: "ETH-USDT-SWAP", allocationPct: 30 },
        { instId: "SOL-USDT-SWAP", allocationPct: 20 },
      ],
    });
    useAppStore.getState().evenSplitAutoTradeMembers();
    const pct = useAppStore.getState().autoTradeMembers.map((m) => m.allocationPct);
    expect(pct).toEqual([34, 33, 33]);
  });

  it("removeFromWatchlist and toggleWatchlist-off drop the auto-trade member (invariant)", () => {
    useAppStore.setState({
      autoTradeMembers: [
        { instId: "BTC-USDT-SWAP", allocationPct: 60 },
        { instId: "ETH-USDT-SWAP", allocationPct: 40 },
      ],
    });
    useAppStore.getState().removeFromWatchlist("BTC-USDT-SWAP");
    expect(useAppStore.getState().autoTradeMembers.map((m) => m.instId)).toEqual(["ETH-USDT-SWAP"]);
    useAppStore.getState().toggleWatchlist("ETH-USDT-SWAP"); // 关闭收藏
    expect(useAppStore.getState().autoTradeMembers).toEqual([]);
  });

  it("liveAutoGroup member event routers patch only the targeted member", () => {
    useAppStore.setState({
      liveAutoGroup: {
        groupId: "g1",
        strategyId: 1,
        strategyName: "s",
        bar: "1H",
        totalBudgetUsd: 100,
        leverage: 3,
        state: "running",
        startedTs: 0,
        members: {
          "BTC-USDT-SWAP": {
            instId: "BTC-USDT-SWAP",
            sessionId: "a",
            runId: 1,
            allocationPct: 60,
            budgetUsd: 60,
            status: null,
            position: null,
            trades: [],
          },
        },
      },
    });
    useAppStore.getState().setLiveAutoMemberStatus("BTC-USDT-SWAP", {
      state: "running",
      sessionPnlUsd: 12,
    });
    expect(useAppStore.getState().liveAutoGroup?.members["BTC-USDT-SWAP"].status?.sessionPnlUsd).toBe(12);
    // 不存在的成员安全回落（不抛、不变更）
    useAppStore.getState().setLiveAutoMemberStatus("NONE", { state: "running", sessionPnlUsd: 1 });
    expect(useAppStore.getState().liveAutoGroup?.members["NONE"]).toBeUndefined();
  });

  it("does not persist liveAutoGroup but persists autoTradeMembers", () => {
    useAppStore.getState().addAutoTradeMember("BTC-USDT-SWAP");
    useAppStore.setState({
      liveAutoGroup: {
        groupId: "g1",
        strategyId: 1,
        strategyName: "s",
        bar: "1H",
        totalBudgetUsd: 100,
        leverage: 3,
        state: "running",
        startedTs: 0,
        members: {},
      },
    });
    const persisted = JSON.parse(localStorage.getItem("aragon-ui") as string);
    expect(persisted.state.autoTradeMembers).toEqual([{ instId: "BTC-USDT-SWAP", allocationPct: 100 }]);
    expect(persisted.state.liveAutoGroup).toBeUndefined();
  });

  it("migrates pre-v20 persisted state to v20 with autoTradeMembers defaulting to []", async () => {
    localStorage.setItem(
      "aragon-ui",
      JSON.stringify({ state: { instId: "ETH-USDT-SWAP" }, version: 19 }),
    );
    await useAppStore.persist.rehydrate();
    const s = useAppStore.getState();
    expect(s.autoTradeMembers).toEqual([]);
    expect(s.instId).toBe("ETH-USDT-SWAP");
  });
});

describe("app store — auto-trade allocation mode (v21)", () => {
  beforeEach(() => {
    localStorage.clear();
    useAppStore.setState({
      watchlist: ["BTC-USDT-SWAP", "ETH-USDT-SWAP", "SOL-USDT-SWAP"],
      autoTradeMembers: [],
      autoTradeAllocationMode: "even",
      liveAutoGroup: null,
    });
  });

  it("setAutoTradeAllocationMode persists the preference", () => {
    expect(useAppStore.getState().autoTradeAllocationMode).toBe("even");
    useAppStore.getState().setAutoTradeAllocationMode("max_ratio");
    expect(useAppStore.getState().autoTradeAllocationMode).toBe("max_ratio");
    const persisted = JSON.parse(localStorage.getItem("aragon-ui") as string);
    expect(persisted.state.autoTradeAllocationMode).toBe("max_ratio");
  });

  it("addAutoTradeMember uses the fixed conservative default in max_ratio mode", () => {
    useAppStore.getState().setAutoTradeAllocationMode("max_ratio");
    useAppStore.getState().addAutoTradeMember("BTC-USDT-SWAP");
    useAppStore.getState().addAutoTradeMember("ETH-USDT-SWAP");
    // 各标的独立默认 20%（不再按剩余额度 100/80…），合计可超 100。
    expect(useAppStore.getState().autoTradeMembers.map((m) => m.allocationPct)).toEqual([20, 20]);
  });

  it("setAutoTradeAllocation in max_ratio clamps to [1,100] independent of others", () => {
    useAppStore.getState().setAutoTradeAllocationMode("max_ratio");
    useAppStore.setState({
      autoTradeMembers: [
        { instId: "BTC-USDT-SWAP", allocationPct: 80 },
        { instId: "ETH-USDT-SWAP", allocationPct: 70 },
      ],
    });
    useAppStore.getState().setAutoTradeAllocation("BTC-USDT-SWAP", 90);
    expect(useAppStore.getState().autoTradeMembers[0].allocationPct).toBe(90); // not limited by ETH 70
  });

  it("migrates pre-v21 persisted state to v21 with allocationMode defaulting to even", async () => {
    localStorage.setItem(
      "aragon-ui",
      JSON.stringify({ state: { instId: "ETH-USDT-SWAP" }, version: 20 }),
    );
    await useAppStore.persist.rehydrate();
    expect(useAppStore.getState().autoTradeAllocationMode).toBe("even");
  });
});

describe("app store — hydrateWorkspace (watchlist-auto-trade-persistence)", () => {
  beforeEach(() => {
    localStorage.clear();
    useAppStore.setState({
      watchlist: ["LOCAL-USDT-SWAP"],
      autoTradeMembers: [],
      autoTradeAllocationMode: "even",
    });
  });

  it("overwrites the slice atomically with the backend values", () => {
    useAppStore.getState().hydrateWorkspace({
      watchlist: ["BTC-USDT-SWAP", "ETH-USDT-SWAP"],
      autoTradeMembers: [{ instId: "BTC-USDT-SWAP", allocationPct: 60 }],
      autoTradeAllocationMode: "max_ratio",
      symbolGroups: [],
    });
    const s = useAppStore.getState();
    expect(s.watchlist).toEqual(["BTC-USDT-SWAP", "ETH-USDT-SWAP"]);
    expect(s.autoTradeMembers).toEqual([{ instId: "BTC-USDT-SWAP", allocationPct: 60 }]);
    expect(s.autoTradeAllocationMode).toBe("max_ratio");
  });

  it("re-asserts the members ⊆ watchlist invariant by dropping orphan members", () => {
    useAppStore.getState().hydrateWorkspace({
      watchlist: ["BTC-USDT-SWAP"],
      autoTradeMembers: [
        { instId: "BTC-USDT-SWAP", allocationPct: 50 },
        { instId: "GHOST-USDT-SWAP", allocationPct: 50 }, // not in watchlist → dropped
      ],
      autoTradeAllocationMode: "even",
      symbolGroups: [],
    });
    expect(useAppStore.getState().autoTradeMembers).toEqual([
      { instId: "BTC-USDT-SWAP", allocationPct: 50 },
    ]);
  });

  it("respects a backend-saved empty watchlist (does not fall back to defaults)", () => {
    useAppStore.getState().hydrateWorkspace({
      watchlist: [],
      autoTradeMembers: [],
      autoTradeAllocationMode: "even",
      symbolGroups: [],
    });
    expect(useAppStore.getState().watchlist).toEqual([]);
    expect(useAppStore.getState().autoTradeMembers).toEqual([]);
  });
});

// 成交缓冲的 wire 级封顶（resilience-and-hci-hardening D4）。
//
// 这四条追加路径此前**全部无界**，且每条都带一次 `some()` 去重 —— 会话全程是 O(n²)。
// 封顶把扫描范围钉死在 TRADES_MAX 上；语义不变（保留的永远是最新的那些）。
describe("app store — 实时成交缓冲封顶（D4）", () => {
  function trade(ts: number): Trade {
    return { ts, side: "long", price: 100 + ts, qty: 1, fee: 0, pnl: 0, positionAfter: 1, reason: "entry" };
  }

  function member(instId: string, trades: Trade[]): AiTradeGroupMemberState {
    return {
      instId,
      sessionId: `sess-${instId}`,
      allocationPct: 100,
      session: null,
      trades,
      position: null,
      decisions: [],
      traces: [],
      summary: null,
    };
  }

  function aiGroup(trades: Trade[]): AiTradeGroupState {
    return {
      groupId: "g1",
      bar: "1H",
      strategyIds: [1],
      strategyNames: ["s"],
      totalBudgetUsd: 1000,
      leverage: 3,
      contextRefreshRounds: 4,
      state: "running",
      startedTs: 0,
      members: { "BTC-USDT-SWAP": member("BTC-USDT-SWAP", trades) },
    };
  }

  beforeEach(() => {
    localStorage.clear();
    useAppStore.setState({ liveAutoTrades: [], aiTradeGroup: null, liveAutoGroup: null });
  });

  it("appendLiveAutoTrade 追加第 TRADES_MAX+1 笔后长度恒为上限且保留最新", () => {
    const store = useAppStore.getState();
    store.setLiveAutoTrades(Array.from({ length: TRADES_MAX }, (_, i) => trade(i)));
    store.appendLiveAutoTrade(trade(TRADES_MAX));
    const kept = useAppStore.getState().liveAutoTrades;
    expect(kept).toHaveLength(TRADES_MAX);
    expect(kept[kept.length - 1].ts).toBe(TRADES_MAX);
    expect(kept[0].ts).toBe(1); // 最旧的那笔被裁掉
  });

  it("setLiveAutoTrades 整列灌入（snapshot 重放）同样被封顶", () => {
    useAppStore
      .getState()
      .setLiveAutoTrades(Array.from({ length: TRADES_MAX + 50 }, (_, i) => trade(i)));
    const kept = useAppStore.getState().liveAutoTrades;
    expect(kept).toHaveLength(TRADES_MAX);
    expect(kept[kept.length - 1].ts).toBe(TRADES_MAX + 49);
  });

  it("appendAiTradeMemberTrade 与整列 setter 都封顶", () => {
    useAppStore.setState({ aiTradeGroup: aiGroup([]) });
    const store = useAppStore.getState();
    store.setAiTradeMemberTrades(
      "BTC-USDT-SWAP",
      Array.from({ length: TRADES_MAX + 10 }, (_, i) => trade(i)),
    );
    expect(useAppStore.getState().aiTradeGroup?.members["BTC-USDT-SWAP"].trades).toHaveLength(
      TRADES_MAX,
    );
    useAppStore.getState().appendAiTradeMemberTrade("BTC-USDT-SWAP", trade(99999));
    const kept = useAppStore.getState().aiTradeGroup?.members["BTC-USDT-SWAP"].trades ?? [];
    expect(kept).toHaveLength(TRADES_MAX);
    expect(kept[kept.length - 1].ts).toBe(99999);
  });

  it("去重语义不变：同 ts/side/price 的重复成交仍被丢弃", () => {
    const store = useAppStore.getState();
    store.appendLiveAutoTrade(trade(1));
    store.appendLiveAutoTrade(trade(1));
    expect(useAppStore.getState().liveAutoTrades).toHaveLength(1);
  });
});

// ============ 生命周期真值化（ai-trade-lifecycle-truth F-9 · 风险 R-7 / [评审 R1-3]）============
describe("app store — 生命周期对账的三个瞬态字段与窄 action", () => {
  function lifecycleMember(trades: Trade[]) {
    return {
      instId: "BTC-USDT-SWAP",
      sessionId: "s1",
      allocationPct: 100,
      session: {
        sessionId: "s1", runId: 1, state: "running", instId: "BTC-USDT-SWAP", bar: "1H",
        strategyIds: [1], strategyNames: ["s"], budgetUsd: 100, leverage: 3,
        contextRefreshRounds: 4, roundsCompleted: 0, roundsSinceSummary: 0,
        positionSide: "flat", sessionPnlUsd: 0, startedTs: 0,
      },
      trades,
      position: { side: "long", size: 1 },
      decisions: [{ ts: 1 }],
      traces: [{ round: 1 }],
      summary: "记忆",
    } as never;
  }

  function seedGroup(trades: Trade[]) {
    useAppStore.setState({
      aiTradeGroup: {
        groupId: "g1", bar: "1H", strategyIds: [1], strategyNames: ["s"],
        totalBudgetUsd: 1000, leverage: 3, contextRefreshRounds: 4, state: "running",
        startedTs: 0, members: { "BTC-USDT-SWAP": lifecycleMember(trades) },
      } as never,
    });
  }

  beforeEach(() => {
    localStorage.clear();
    useAppStore.setState({
      aiTradeGroup: null, aiTradeSyncState: "ok", aiTradeReconcileUntil: null,
      aiTradeEnabled: false,
    });
  });

  it("风险 R-7：三个新字段**不得**出现在 persist 快照里（瞬态就该只活在内存里）", () => {
    useAppStore.setState({
      aiTradeSyncState: "unknown", aiTradeReconcileUntil: 123, aiTradeEnabled: true,
    });
    useAppStore.getState().setTheme("dark");        // 触发一次持久化写入
    const persisted = JSON.parse(localStorage.getItem("aragon-ui") as string);
    expect(persisted.state).not.toHaveProperty("aiTradeSyncState");
    expect(persisted.state).not.toHaveProperty("aiTradeReconcileUntil");
    expect(persisted.state).not.toHaveProperty("aiTradeEnabled");
    expect(persisted.state).not.toHaveProperty("aiTradeGroup");
  });

  it("[评审 R1-3] 窄 action 写分组级 + 逐成员生命周期键，**绝不碰** trades/position/decisions", () => {
    const trades = [{ ts: 1, side: "buy", price: 1, size: 1 }] as unknown as Trade[];
    seedGroup(trades);

    useAppStore.getState().patchAiTradeGroupLifecycle({
      group: { state: "stopped", declaredState: "running", pauseReason: null,
               pausedTs: 0, resumedCount: 2, lifecycle: null },
      members: [{ instId: "BTC-USDT-SWAP",
                  session: { state: "stopped", stopReason: "session_stop_loss" } }],
    } as never);

    const group = useAppStore.getState().aiTradeGroup!;
    expect(group.state).toBe("stopped");
    expect(group.declaredState).toBe("running");
    expect(group.resumedCount).toBe(2);
    const member = group.members["BTC-USDT-SWAP"];
    expect(member.session?.state).toBe("stopped");
    expect(member.session?.stopReason).toBe("session_stop_loss");
    // 这四样是 WS 流的产物：覆盖即闪烁 / 丢历史，而本 action 跑在一个 3~15 秒的轮询里。
    expect(member.trades).toHaveLength(1);
    expect(member.position).not.toBeNull();
    expect(member.decisions).toHaveLength(1);
    expect(member.summary).toBe("记忆");
  });

  it("回执里缺席的成员**保留本地行不动，不删**（它的去向由终态留痕卡负责）", () => {
    seedGroup([]);
    useAppStore.getState().patchAiTradeGroupLifecycle({
      group: { state: "running" }, members: [{ instId: "ETH-USDT-SWAP", session: {} }],
    } as never);
    expect(Object.keys(useAppStore.getState().aiTradeGroup!.members)).toEqual(["BTC-USDT-SWAP"]);
  });

  it("无分组时静默空转（轮询与分组生命周期天然有竞态）", () => {
    useAppStore.getState().patchAiTradeGroupLifecycle({
      group: { state: "stopped" }, members: [],
    } as never);
    expect(useAppStore.getState().aiTradeGroup).toBeNull();
  });

  it("requestAiTradeReconcile 打开 30 秒的快对账窗口", () => {
    const before = Date.now();
    useAppStore.getState().requestAiTradeReconcile();
    const until = useAppStore.getState().aiTradeReconcileUntil!;
    expect(until).toBeGreaterThanOrEqual(before + 29_000);
    expect(until).toBeLessThanOrEqual(before + 31_000);
  });
});

describe("app store — 市场情报切片（ai-trade-market-intel §6.2 #38）", () => {
  const INST = "BTC-USDT-SWAP";
  const summary = {
    instId: INST,
    counts: { ultra: 2, mid: 1, long: 0, archived: 0 },
    unread: 3,
    lastSeenTs: 0,
    lastCollectTs: 1,
    lastCurateTs: null,
    nextDueTs: null,
    inFlight: false,
    degrade: null,
    providerConfigured: true,
    dailyUsed: 1,
    dailyCap: 96,
  };

  beforeEach(() => useAppStore.setState({ aiTradeIntel: {}, aiTradeGroup: null }));

  it("按 instId 落库，互不覆盖", () => {
    useAppStore.getState().setAiTradeIntelSummary(INST, summary as never);
    useAppStore.getState().setAiTradeIntelSummary("ETH-USDT-SWAP",
      { ...summary, instId: "ETH-USDT-SWAP", unread: 0 } as never);
    const slice = useAppStore.getState().aiTradeIntel;
    expect(slice[INST].unread).toBe(3);
    expect(slice["ETH-USDT-SWAP"].unread).toBe(0);
  });

  it("`null` 是 no-op（一条读不到的推送不该把已有徽标抹掉）", () => {
    useAppStore.getState().setAiTradeIntelSummary(INST, summary as never);
    useAppStore.getState().setAiTradeIntelSummary(INST, null);
    expect(useAppStore.getState().aiTradeIntel[INST].unread).toBe(3);
  });

  it("clearAiTradeRuntime 一并清情报切片", () => {
    // 不清的话，下一次开另一个标的时旧的未读红点会挂在一个已经不在跑的标的上——
    // 一个指向已归档会话的红点比没有红点更坏。
    useAppStore.getState().setAiTradeIntelSummary(INST, summary as never);
    useAppStore.getState().clearAiTradeRuntime();
    expect(useAppStore.getState().aiTradeIntel).toEqual({});
  });

  it("情报切片**不进 partialize**（它是服务端真值的镜像，重连即补齐）", () => {
    useAppStore.getState().setAiTradeIntelSummary(INST, summary as never);
    const persisted = JSON.parse(localStorage.getItem("aragon-ui") ?? "{}");
    expect(persisted?.state?.aiTradeIntel).toBeUndefined();
  });
});
