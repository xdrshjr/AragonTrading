import { renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { aiTradeApi, aiTradeWS } from "./aiTradeApi";
import { useAppStore } from "./store";
import { useAiTradeGroupStream } from "./useAiTradeGroupStream";

vi.mock("./aiTradeApi", () => ({
  aiTradeApi: { status: vi.fn() },
  aiTradeWS: vi.fn(() => ({ connect: vi.fn(), close: vi.fn() })),
}));

/**
 * G-23 的另一半（ai-trade-pause-resume §3.9.4）：**冷占位绝不建 WS**。
 *
 * 冷占位没有 `sessionId`（进程里根本没有会话），不过滤就会去连 `/ws/ai-trade/undefined`。
 * 同一条 effect 还有一个更隐蔽的坑：`resumable` 必须在 `!res.group` 那条早退**之前**消费，
 * 否则冷占位这条路径上 `setAiTradeResumable` 永远不会被调用 —— 那正是 D-7 那类「静默失效」。
 */
const COLD = {
  groupId: "grp-1",
  bar: "1H",
  accountId: 1,
  totalBudgetUsd: 300,
  leverage: 3,
  strategyNames: ["S"],
  createdTs: 0,
  pausedTs: 1_700_000_000,
  pauseReason: "process_restart" as const,
  pauseReasonText: "上次未正常退出，已自动暂停",
  resumedCount: 0,
  offlineSeconds: 60,
  offlineText: "1 分",
  hasPosition: false,
  members: [{ instId: "BTC-USDT-SWAP", runId: 1 }],
};

beforeEach(() => {
  vi.clearAllMocks();
  useAppStore.setState({ accountMode: "live", aiTradeGroup: null, aiTradeResumable: null });
});

afterEach(() => useAppStore.setState({ aiTradeGroup: null, aiTradeResumable: null }));

describe("useAiTradeGroupStream · 冷占位", () => {
  it("消费 resumable 并且**一条 WS 都不建**（冷占位没有 sessionId）", async () => {
    vi.mocked(aiTradeApi.status).mockResolvedValue({
      group: null,
      aiTradeEnabled: true,
      resumable: COLD,
    } as never);

    renderHook(() => useAiTradeGroupStream());

    await waitFor(() =>
      expect(useAppStore.getState().aiTradeResumable?.groupId).toBe("grp-1"),
    );
    expect(useAppStore.getState().aiTradeGroup).toBeNull();   // 冷占位绝不 setAiTradeGroup
    expect(aiTradeWS).not.toHaveBeenCalled();
  });

  it("后端不再下发 resumable ⇒ 本地那张卡被清掉（不留幽灵卡）", async () => {
    useAppStore.setState({ aiTradeResumable: COLD as never });
    vi.mocked(aiTradeApi.status).mockResolvedValue({
      group: null,
      aiTradeEnabled: true,
      resumable: null,
    } as never);

    renderHook(() => useAiTradeGroupStream());

    await waitFor(() => expect(useAppStore.getState().aiTradeResumable).toBeNull());
  });

  it("模拟盘模式下整条 effect 不跑（续跑入口只在实盘账户模式出现，[P2-10]）", async () => {
    useAppStore.setState({ accountMode: "sim" });
    renderHook(() => useAiTradeGroupStream());
    await waitFor(() => expect(aiTradeApi.status).not.toHaveBeenCalled());
    expect(useAppStore.getState().aiTradeResumable).toBeNull();
  });
});
