import { act, renderHook, waitFor } from "@testing-library/react";
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
  // `aiTradeWS` 的默认替身在 `captureRouter()` 里会被逐用例覆盖。
  vi.mocked(aiTradeWS).mockImplementation((() => ({ connect: vi.fn(), close: vi.fn() })) as never);
  useAppStore.setState({
    accountMode: "live", aiTradeGroup: null, aiTradeResumable: null,
    aiTradeSyncState: "ok", aiTradeReconcileUntil: null, aiTradeEnabled: false,
  });
});

afterEach(() =>
  useAppStore.setState({
    aiTradeGroup: null, aiTradeResumable: null, aiTradeSyncState: "ok",
    aiTradeReconcileUntil: null,
  }),
);

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

// ============ 生命周期对账器（ai-trade-lifecycle-truth §8.2 用例 14 / 26 / 27）============

const MEMBER = {
  instId: "BTC-USDT-SWAP", sessionId: "s1", runId: 1, state: "running", bar: "1H",
  strategyIds: [1], strategyNames: ["S"], budgetUsd: 100, leverage: 3,
  contextRefreshRounds: 10, roundsCompleted: 0, roundsSinceSummary: 0,
  positionSide: "flat", sessionPnlUsd: 0, startedTs: 0, allocationPct: 100,
};

const MEMBER_WIRE = {
  state: "running", armed: true, reason: "armed", reasonText: "", warmedBars: 300,
  feedAgeS: 3, staleAfterS: 10800, linkState: "up", nextWakeInS: 120, nextBarInS: 480,
  roundsCompleted: 4, pauseReason: null, pausedTs: 0, resumedTs: 0, stopReason: null,
};

const GROUP_WIRE = {
  state: "running", declaredState: "running", armed: true, membersArmed: 1, membersTotal: 1,
  asOfTs: 1_755_500_000, notArmed: [], nextWakeInS: 120, nextBarInS: 480,
  pauseReason: null, pausedTs: 0, resumedCount: 0,
};

const HOT = {
  groupId: "grp-hot", bar: "1H", strategyIds: [1], strategyNames: ["S"], totalBudgetUsd: 100,
  leverage: 3, contextRefreshRounds: 10, createdTs: 0, state: "running",
  lifecycle: GROUP_WIRE, members: [{ ...MEMBER, lifecycle: MEMBER_WIRE }],
};

/** 本地热态：带一条成交（窄 patch 绝不许把它清掉）。 */
function seedLocalGroup(over: Record<string, unknown> = {}) {
  useAppStore.setState({
    aiTradeGroup: {
      groupId: "grp-hot", bar: "1H", strategyIds: [1], strategyNames: ["S"],
      totalBudgetUsd: 100, leverage: 3, contextRefreshRounds: 10, state: "running",
      lifecycle: GROUP_WIRE, startedTs: 0,
      members: {
        "BTC-USDT-SWAP": {
          instId: "BTC-USDT-SWAP", sessionId: "s1", allocationPct: 100,
          session: { ...MEMBER },
          trades: [{ ts: 1, side: "buy", price: 100, size: 1 }],
          position: { side: "long", size: 1 },
          decisions: [], traces: [], summary: null,
        },
      },
      ...over,
    } as never,
  });
}

describe("useAiTradeGroupStream · 生命周期对账器", () => {
  it("用例 26（[评审 R0-7]）：本地**没有分组**时轮询照常发起，「重发现」这一行真的执行", async () => {
    vi.mocked(aiTradeApi.status).mockResolvedValue({
      group: HOT, aiTradeEnabled: true, resumable: null,
    } as never);

    renderHook(() => useAiTradeGroupStream());

    await waitFor(() => expect(useAppStore.getState().aiTradeGroup?.groupId).toBe("grp-hot"));
    // 生命周期证据必须一路穿到 store（`groupStateFromStatus` 是逐键手抄的映射，风险 R-8）。
    expect(useAppStore.getState().aiTradeGroup?.lifecycle?.armed).toBe(true);
    expect(useAppStore.getState().aiTradeEnabled).toBe(true);
  });

  it("用例 27（[评审 R1-3]）：同组窄 patch 改写成员态，而 trades / position **未被清空**", async () => {
    seedLocalGroup();
    const dead = {
      ...HOT,
      state: "stopped",
      lifecycle: { ...GROUP_WIRE, state: "stopped", armed: false, membersArmed: 0,
                   notArmed: [{ instId: "BTC-USDT-SWAP", state: "stopped",
                                reason: "session_stopped", reasonText: "会话已终止" }] },
      members: [{ ...MEMBER, state: "stopped", stopReason: "session_stop_loss",
                  lifecycle: { ...MEMBER_WIRE, state: "stopped", armed: false,
                               reason: "session_stopped" } }],
    };
    vi.mocked(aiTradeApi.status).mockResolvedValue({
      group: dead, aiTradeEnabled: true, resumable: null,
    } as never);

    renderHook(() => useAiTradeGroupStream());

    await waitFor(() =>
      expect(useAppStore.getState().aiTradeGroup?.members["BTC-USDT-SWAP"].session?.state)
        .toBe("stopped"),
    );
    const member = useAppStore.getState().aiTradeGroup!.members["BTC-USDT-SWAP"];
    expect(member.trades).toHaveLength(1);          // 成交流绝不能被 3~15 秒的轮询清掉
    expect(member.position).not.toBeNull();
    expect(member.session?.stopReason).toBe("session_stop_loss");
    expect(useAppStore.getState().aiTradeGroup?.state).toBe("stopped");
  });

  it("`lifecycleUnknown` ⇒ **什么都不清**，只置 unknown（L-7：读不到 ≠ 没有）", async () => {
    seedLocalGroup();
    vi.mocked(aiTradeApi.status).mockResolvedValue({
      group: null, aiTradeEnabled: true, resumable: null, lifecycleUnknown: true,
    } as never);

    renderHook(() => useAiTradeGroupStream());

    await waitFor(() => expect(useAppStore.getState().aiTradeSyncState).toBe("unknown"));
    expect(useAppStore.getState().aiTradeGroup?.groupId).toBe("grp-hot");
  });

  it("快对账窗口到点后**自己降回常态**（否则 3 秒节奏会永久留下来）", async () => {
    vi.mocked(aiTradeApi.status).mockResolvedValue({
      group: HOT, aiTradeEnabled: true, resumable: null,
    } as never);
    // 一个**已经过期**的窗口：effect 必须按常态节奏跑，且把这个陈旧时间戳清掉。
    useAppStore.setState({ aiTradeReconcileUntil: Date.now() - 1 });

    renderHook(() => useAiTradeGroupStream());

    await waitFor(() => expect(useAppStore.getState().aiTradeGroup?.groupId).toBe("grp-hot"));
    // 未过期的窗口会在到点时被 setTimeout 清掉；这里断言的是「过期即不再被当成 fast」。
    expect(useAppStore.getState().aiTradeReconcileUntil).toBeLessThan(Date.now());
  });

  it("请求本身失败 ⇒ unknown，同样什么都不清（连不上后端 ≠ 没有分组）", async () => {
    seedLocalGroup();
    vi.mocked(aiTradeApi.status).mockRejectedValue(new Error("network down"));

    renderHook(() => useAiTradeGroupStream());

    await waitFor(() => expect(useAppStore.getState().aiTradeSyncState).toBe("unknown"));
    expect(useAppStore.getState().aiTradeGroup?.groupId).toBe("grp-hot");
  });

  it("终态留痕：后端已清组而本地成员全 stopped ⇒ 保留面板并置 stopped", async () => {
    seedLocalGroup();
    useAppStore.setState((s) => ({
      aiTradeGroup: {
        ...s.aiTradeGroup!,
        members: {
          "BTC-USDT-SWAP": {
            ...s.aiTradeGroup!.members["BTC-USDT-SWAP"],
            session: { ...s.aiTradeGroup!.members["BTC-USDT-SWAP"].session!, state: "stopped" },
          },
        },
      } as never,
    }));
    vi.mocked(aiTradeApi.status).mockResolvedValue({
      group: null, aiTradeEnabled: true, resumable: null,
    } as never);

    renderHook(() => useAiTradeGroupStream());

    await waitFor(() => expect(useAppStore.getState().aiTradeGroup?.state).toBe("stopped"));
    expect(useAppStore.getState().aiTradeGroup).not.toBeNull();   // 面板必须留着
  });
});

describe("useAiTradeGroupStream · WS 两个新 case（用例 14）", () => {
  /** 抓住 hook 注入给 `aiTradeWS` 的那个事件回调。 */
  function captureRouter() {
    const close = vi.fn();
    let onEvent: ((e: { type: string; data: unknown }) => void) | null = null;
    vi.mocked(aiTradeWS).mockImplementation(((_sid: string, handler: never) => {
      onEvent = handler as never;
      return { connect: vi.fn(), close } as never;
    }) as never);
    return { getOnEvent: () => onEvent, close };
  }

  it("`lifecycle` 事件即时把成员置 paused，并请一次快对账（D-10：这条信号此前被丢在地上）", async () => {
    const { getOnEvent } = captureRouter();
    seedLocalGroup();
    vi.mocked(aiTradeApi.status).mockResolvedValue({
      group: HOT, aiTradeEnabled: true, resumable: null,
    } as never);

    renderHook(() => useAiTradeGroupStream());
    await waitFor(() => expect(getOnEvent()).toBeTruthy());

    act(() =>
      getOnEvent()!({
        type: "lifecycle",
        data: { action: "paused", reason: "user", ts: 1_700_000_000,
                lifecycle: { ...MEMBER_WIRE, state: "paused", armed: false,
                             reason: "session_paused" } },
      }),
    );

    const member = useAppStore.getState().aiTradeGroup!.members["BTC-USDT-SWAP"];
    expect(member.session?.state).toBe("paused");
    expect(member.session?.pauseReason).toBe("user");
    expect(member.session?.lifecycle?.reason).toBe("session_paused");
    // **本 case 不直接写分组级 state**：分组结论是后端 `derive_group_state` 的产物（L-1）。
    expect(useAppStore.getState().aiTradeGroup?.state).toBe("running");
    expect(useAppStore.getState().aiTradeReconcileUntil).toBeTruthy();
  });

  it("`error` 的 `NOT_FOUND` ⇒ **停止重连** + 立即对账（D-9：此前是一场静默的重连风暴）", async () => {
    const { getOnEvent, close } = captureRouter();
    seedLocalGroup();
    vi.mocked(aiTradeApi.status).mockResolvedValue({
      group: HOT, aiTradeEnabled: true, resumable: null,
    } as never);

    renderHook(() => useAiTradeGroupStream());
    await waitFor(() => expect(getOnEvent()).toBeTruthy());

    act(() =>
      getOnEvent()!({ type: "error", data: { code: "NOT_FOUND", message: "会话不存在" } }),
    );

    expect(close).toHaveBeenCalled();
    expect(useAppStore.getState().aiTradeReconcileUntil).toBeTruthy();
  });

  it("其余错误码落成员状态行，且**绝不**替它猜一个 state", async () => {
    const { getOnEvent, close } = captureRouter();
    seedLocalGroup();
    vi.mocked(aiTradeApi.status).mockResolvedValue({
      group: HOT, aiTradeEnabled: true, resumable: null,
    } as never);

    renderHook(() => useAiTradeGroupStream());
    await waitFor(() => expect(getOnEvent()).toBeTruthy());

    act(() =>
      getOnEvent()!({
        type: "error",
        data: { code: "AI_TRADE_FLATTEN_FAILED", message: "平仓失败，请立即到实盘面板手动平仓" },
      }),
    );

    const member = useAppStore.getState().aiTradeGroup!.members["BTC-USDT-SWAP"];
    expect(member.session?.message).toContain("平仓失败");
    expect(member.session?.state).toBe("running");    // 一条错误帧不是一次状态迁移
    expect(close).not.toHaveBeenCalled();
  });
});
