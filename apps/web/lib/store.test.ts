import { beforeEach, describe, expect, it } from "vitest";
import { DEFAULT_COMBO_POLICY } from "./combo";
import { FALLBACK_INSTRUMENTS } from "./instruments";
import { DEFAULT_SIM_BALANCE, useAppStore } from "./store";

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
    expect(s.accountMode).toBe("sim");
    expect(s.simBalance).toBe(DEFAULT_SIM_BALANCE);
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

  it("setMode('combo') switches chartView to run-range", () => {
    useAppStore.getState().setMode("combo");
    expect(useAppStore.getState().mode).toBe("combo");
    expect(useAppStore.getState().chartView).toBe("run-range");
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
