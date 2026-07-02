import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useAppStore } from "./store";
import { WORKSPACE_WRITE_DEBOUNCE_MS, useWorkspaceSync } from "./useWorkspaceSync";
import { workspaceApi } from "./workspaceApi";
import type { WorkspaceWatchlistOut } from "./workspaceTypes";

// 桩掉真实网络：本测试验证水合 / 写穿编排，不验证 REST 线缆（那是 workspaceApi.test.ts 的职责）。
vi.mock("./workspaceApi", () => ({ workspaceApi: { get: vi.fn(), update: vi.fn() } }));

const mockGet = vi.mocked(workspaceApi.get);
const mockUpdate = vi.mocked(workspaceApi.update);

function out(over: Partial<WorkspaceWatchlistOut> = {}): WorkspaceWatchlistOut {
  return {
    present: true,
    watchlist: ["AAA-USDT-SWAP"],
    autoTradeMembers: [],
    autoTradeAllocationMode: "even",
    symbolGroups: [],
    ...over,
  };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((res) => {
    resolve = res;
  });
  return { promise, resolve };
}

// 冲刷挂起的 microtask（rehydrate / GET 链）：setTimeout(0) 在所有 microtask 排空后才触发。
const tick = async () =>
  act(async () => {
    await new Promise((r) => setTimeout(r, 0));
  });

// 等过一个去抖窗口，确认（不该发的）写穿不会迟发。
const waitDebounce = async () =>
  act(async () => {
    await new Promise((r) => setTimeout(r, WORKSPACE_WRITE_DEBOUNCE_MS + 50));
  });

beforeEach(() => {
  localStorage.clear();
  vi.clearAllMocks();
  useAppStore.setState({
    watchlist: ["LOCAL-USDT-SWAP"],
    autoTradeMembers: [],
    autoTradeAllocationMode: "even",
    liveAutoStatus: null,
    liveAutoEvents: [],
  });
});

afterEach(cleanup);

describe("useWorkspaceSync", () => {
  it("backend-wins: present:true overwrites the store slice and does not seed", async () => {
    mockGet.mockResolvedValue(out({ present: true, watchlist: ["BTC-USDT-SWAP", "ETH-USDT-SWAP"] }));

    renderHook(() => useWorkspaceSync());

    await waitFor(() =>
      expect(useAppStore.getState().watchlist).toEqual(["BTC-USDT-SWAP", "ETH-USDT-SWAP"]),
    );
    await waitDebounce();
    expect(mockUpdate).not.toHaveBeenCalled();
  });

  it("seed-if-empty: present:false PUTs the current slice once and leaves the store unchanged", async () => {
    mockGet.mockResolvedValue(out({ present: false, watchlist: [], autoTradeMembers: [] }));
    mockUpdate.mockResolvedValue(out());
    useAppStore.setState({ watchlist: ["LOCAL-USDT-SWAP"], autoTradeMembers: [] });

    renderHook(() => useWorkspaceSync());

    await waitFor(() => expect(mockUpdate).toHaveBeenCalledTimes(1));
    expect(mockUpdate.mock.calls[0][0].watchlist).toEqual(["LOCAL-USDT-SWAP"]);
    // seed 不覆盖本地切片
    expect(useAppStore.getState().watchlist).toEqual(["LOCAL-USDT-SWAP"]);
  });

  it("debounces rapid edits into a single write with the final value", async () => {
    mockGet.mockResolvedValue(out({ present: true, watchlist: ["BTC-USDT-SWAP"] }));
    mockUpdate.mockResolvedValue(out());

    renderHook(() => useWorkspaceSync());
    await waitFor(() => expect(useAppStore.getState().watchlist).toEqual(["BTC-USDT-SWAP"]));

    act(() => {
      useAppStore.getState().addToWatchlist("ETH-USDT-SWAP");
      useAppStore.getState().addToWatchlist("SOL-USDT-SWAP");
      useAppStore.getState().addToWatchlist("DOGE-USDT-SWAP");
    });

    await waitFor(() => expect(mockUpdate).toHaveBeenCalledTimes(1));
    expect(mockUpdate.mock.calls[0][0].watchlist).toEqual([
      "DOGE-USDT-SWAP",
      "SOL-USDT-SWAP",
      "ETH-USDT-SWAP",
      "BTC-USDT-SWAP",
    ]);
  });

  it("does not echo a PUT for the hydrating set()", async () => {
    mockGet.mockResolvedValue(out({ present: true, watchlist: ["BTC-USDT-SWAP"] }));

    renderHook(() => useWorkspaceSync());
    await waitFor(() => expect(useAppStore.getState().watchlist).toEqual(["BTC-USDT-SWAP"]));

    await waitDebounce();
    expect(mockUpdate).not.toHaveBeenCalled();
  });

  it("ignores non-slice store changes (no PUT for runtime state)", async () => {
    mockGet.mockResolvedValue(out({ present: true, watchlist: ["BTC-USDT-SWAP"] }));

    renderHook(() => useWorkspaceSync());
    await waitFor(() => expect(useAppStore.getState().watchlist).toEqual(["BTC-USDT-SWAP"]));

    act(() => {
      useAppStore.getState().setLiveAutoStatus({ state: "running", sessionPnlUsd: 0 });
      useAppStore.getState().pushLiveAutoEvent({ ts: 1, level: "info", message: "tick" });
    });

    await waitDebounce();
    expect(mockUpdate).not.toHaveBeenCalled();
  });

  it("treats the write-through PUT as fire-and-forget (response not applied back)", async () => {
    mockGet.mockResolvedValue(out({ present: true, watchlist: ["BTC-USDT-SWAP"] }));
    // 响应回带一个与本地不同的 sanitized 文档；绝不能被写回 store。
    mockUpdate.mockResolvedValue(out({ watchlist: ["SERVER-ONLY-SWAP"] }));

    renderHook(() => useWorkspaceSync());
    await waitFor(() => expect(useAppStore.getState().watchlist).toEqual(["BTC-USDT-SWAP"]));

    act(() => {
      useAppStore.getState().addToWatchlist("ETH-USDT-SWAP");
    });

    await waitFor(() => expect(mockUpdate).toHaveBeenCalledTimes(1));
    await tick();
    expect(useAppStore.getState().watchlist).toEqual(["ETH-USDT-SWAP", "BTC-USDT-SWAP"]);
  });

  it("no-clobber: a GET resolving after a user edit does not overwrite the edit", async () => {
    const d = deferred<WorkspaceWatchlistOut>();
    mockGet.mockReturnValue(d.promise);
    mockUpdate.mockResolvedValue(out());
    useAppStore.setState({ watchlist: ["LOCAL-USDT-SWAP"] });

    renderHook(() => useWorkspaceSync());
    await tick(); // rehydrate 结算 + 基线捕获；GET 仍挂起

    act(() => {
      useAppStore.getState().addToWatchlist("USER-EDIT-SWAP");
    });
    expect(useAppStore.getState().watchlist).toEqual(["USER-EDIT-SWAP", "LOCAL-USDT-SWAP"]);

    await act(async () => {
      d.resolve(out({ present: true, watchlist: ["BACKEND-ONLY-SWAP"] }));
      await d.promise;
    });

    // 本地编辑留存（未被后端覆盖），且随后被写穿持久化。
    expect(useAppStore.getState().watchlist).toEqual(["USER-EDIT-SWAP", "LOCAL-USDT-SWAP"]);
    await waitFor(() => expect(mockUpdate).toHaveBeenCalled());
    expect(mockUpdate.mock.calls[0][0].watchlist).toEqual(["USER-EDIT-SWAP", "LOCAL-USDT-SWAP"]);
  });

  it("tolerates a failing GET: keeps local cache, no seed, no crash", async () => {
    mockGet.mockRejectedValue(new Error("backend down"));
    useAppStore.setState({ watchlist: ["LOCAL-USDT-SWAP"] });

    renderHook(() => useWorkspaceSync());
    await tick();

    expect(useAppStore.getState().watchlist).toEqual(["LOCAL-USDT-SWAP"]);
    expect(mockUpdate).not.toHaveBeenCalled();
  });
});
