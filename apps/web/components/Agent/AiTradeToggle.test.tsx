import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { accountApi } from "@/lib/accountApi";
import { agentApi } from "@/lib/agentApi";
import { api } from "@/lib/api";
import { aiTradeApi } from "@/lib/aiTradeApi";
import type { AiTradeGroupRow, AiTradeGroupState } from "@/lib/aiTradeTypes";
import { useAppStore } from "@/lib/store";
import { tradingApi } from "@/lib/tradingApi";
import AiTradeToggle from "./AiTradeToggle";

vi.mock("@/lib/aiTradeApi", () => ({
  aiTradeApi: {
    start: vi.fn(), stopGroup: vi.fn(), stopMember: vi.fn(), status: vi.fn(),
    // 暂停 / 继续 / 结束（ai-trade-pause-resume §3.9.2）：三个 mutation + 一次只读复核。
    pauseGroup: vi.fn(), resumeGroup: vi.fn(), endGroup: vi.fn(), resumePreview: vi.fn(),
    repairBackstop: vi.fn(),
  },
  aiTradeWS: vi.fn(() => ({ connect: vi.fn(), close: vi.fn() })),
}));
vi.mock("@/lib/accountApi", () => ({ accountApi: { accounts: { list: vi.fn() } } }));
vi.mock("@/lib/agentApi", () => ({ agentApi: { config: vi.fn() } }));
vi.mock("@/lib/tradingApi", () => ({ tradingApi: { config: vi.fn() } }));
vi.mock("@/lib/api", async (orig) => {
  const actual = (await orig()) as Record<string, unknown>;
  return {
    ...actual,
    api: { ...(actual.api as Record<string, unknown>), strategies: vi.fn().mockResolvedValue([]) },
  };
});

const READY_ACCOUNTS = { accounts: [{ id: 1, hasSecret: true, hasPassphrase: true }], defaultId: 1 };

function makeGroup(): AiTradeGroupState {
  return {
    groupId: "grp-1",
    bar: "1H",
    strategyIds: [1],
    strategyNames: ["S1"],
    totalBudgetUsd: 100,
    leverage: 3,
    contextRefreshRounds: 10,
    state: "running",
    startedTs: 0,
    members: {
      "BTC-USDT-SWAP": {
        instId: "BTC-USDT-SWAP",
        sessionId: "sess-1",
        allocationPct: 100,
        session: {
          sessionId: "sess-1", runId: 1, state: "running", instId: "BTC-USDT-SWAP", bar: "1H",
          strategyIds: [1], strategyNames: ["S1"], budgetUsd: 100, leverage: 3,
          contextRefreshRounds: 10, roundsCompleted: 0, roundsSinceSummary: 0,
          positionSide: "flat", sessionPnlUsd: 0, startedTs: 0,
        },
        trades: [],
        position: null,
        decisions: [],
        traces: [],
        summary: null,
      },
    },
  };
}

function renderToggle() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <AiTradeToggle />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
  useAppStore.setState({
    accountMode: "live",
    instId: "BTC-USDT-SWAP",
    bar: "1H",
    strategyId: 1,
    aiTradeGroup: null,
    aiTradeResumable: null,
    autoTradeMembers: [],
    autoTradeAllocationMode: "even",
    liveAutoGroup: null,
    aiTradeAcknowledged: false,
    aiTradeConfig: null,
  });
  vi.mocked(accountApi.accounts.list).mockResolvedValue(READY_ACCOUNTS as never);
  vi.mocked(agentApi.config).mockResolvedValue({ configured: true, provider: "x", model: "y" } as never);
  vi.mocked(aiTradeApi.status).mockResolvedValue({
    group: null, aiTradeEnabled: true, resumable: null,
  } as never);
  vi.mocked(tradingApi.config).mockResolvedValue({ availUsdt: 1000 } as never);
});

afterEach(() => cleanup());

describe("AiTradeToggle", () => {
  it("renders an off switch when no group is active", () => {
    renderToggle();
    expect(screen.getByRole("switch")).toHaveAttribute("aria-checked", "false");
  });

  it("opens the launch modal in live mode when all gates pass", async () => {
    renderToggle();
    // 守门通过后包裹容器 title 变为「开启…」，据此等三条查询（账户/模型/AI 开关）都已水合再点击。
    await screen.findByTitle("开启 AI 自动交易 / 回测");
    fireEvent.click(screen.getByRole("switch"));
    expect(await screen.findByText("启动 AI 自动交易")).toBeInTheDocument();
  });

  it("opens in backtest mode when live AI trading is gated but agent is configured (R6)", async () => {
    // AI 开关后端未开 → 实盘段禁用，但 Agent 已配置 → 弹窗仍可开，默认落回测段。
    vi.mocked(aiTradeApi.status).mockResolvedValue({ group: null, aiTradeEnabled: false } as never);
    renderToggle();
    await screen.findByTitle("开启 AI 自动交易 / 回测");
    fireEvent.click(screen.getByRole("switch"));
    expect(await screen.findByText("AI 自动交易 · 回测")).toBeInTheDocument();
    expect(screen.queryByText("启动 AI 自动交易")).not.toBeInTheDocument();
  });

  it("does not open the modal when the agent model is not configured", async () => {
    vi.mocked(agentApi.config).mockResolvedValue({ configured: false } as never);
    renderToggle();
    await screen.findByTitle("请先在设置中心配置 AI 模型");
    fireEvent.click(screen.getByRole("switch"));
    expect(screen.queryByText("启动 AI 自动交易")).not.toBeInTheDocument();
    expect(screen.queryByText("AI 自动交易 · 回测")).not.toBeInTheDocument();
  });

  // wake-interval（ai-auto-trading-wake-interval §6.2）：startM 请求体携 wakeIntervalMinutes（默认 2），
  // 成功后取自 opts 写入 aiTradeConfig（res 不回显该键，取 res 会让偏好每次重开回落默认）。
  it("sends wakeIntervalMinutes in the start body and persists it to aiTradeConfig", async () => {
    vi.mocked(api.strategies).mockResolvedValue([{ id: 1, name: "均线策略" }] as never);
    const groupRow: AiTradeGroupRow = {
      groupId: "grp-1", bar: "1H", strategyIds: [1], strategyNames: ["均线策略"],
      totalBudgetUsd: 100, leverage: 3, contextRefreshRounds: 10, createdTs: 0,
      members: [], wakeIntervalMinutes: 2,
    };
    vi.mocked(aiTradeApi.start).mockResolvedValue(groupRow);
    useAppStore.setState({ aiTradeAcknowledged: true });
    renderToggle();
    await screen.findByTitle("开启 AI 自动交易 / 回测");
    fireEvent.click(screen.getByRole("switch"));
    await screen.findByText("均线策略");
    const confirm = await screen.findByRole("button", { name: /确认并开始 AI 自动交易/ });
    await waitFor(() => expect(confirm).not.toBeDisabled());
    fireEvent.click(confirm);
    await waitFor(() => expect(aiTradeApi.start).toHaveBeenCalledTimes(1));
    expect(vi.mocked(aiTradeApi.start).mock.calls[0][0].wakeIntervalMinutes).toBe(2);
    await waitFor(() =>
      expect(useAppStore.getState().aiTradeConfig?.wakeIntervalMinutes).toBe(2),
    );
  });

  // 【基础策略】铁律（ai-trade-base-strategy-ma-band §6.2 用例 30）：start 请求体携 baseRules，
  // 成功后取自 opts 写入 aiTradeConfig（回执只镜像装配态，不回显完整参数，[R-4] 同理）。
  it("sends baseRules in the start body and persists the rule to aiTradeConfig", async () => {
    vi.mocked(api.strategies).mockResolvedValue([{ id: 1, name: "均线策略" }] as never);
    const groupRow: AiTradeGroupRow = {
      groupId: "grp-1", bar: "1H", strategyIds: [1], strategyNames: ["均线策略"],
      totalBudgetUsd: 100, leverage: 3, contextRefreshRounds: 10, createdTs: 0,
      members: [], wakeIntervalMinutes: 2,
      baseRules: { maBandEntry: { enabled: true } },
    };
    vi.mocked(aiTradeApi.start).mockResolvedValue(groupRow);
    useAppStore.setState({ aiTradeAcknowledged: true });
    renderToggle();
    await screen.findByTitle("开启 AI 自动交易 / 回测");
    fireEvent.click(screen.getByRole("switch"));
    await screen.findByText("均线策略");
    const confirm = await screen.findByRole("button", { name: /确认并开始 AI 自动交易/ });
    await waitFor(() => expect(confirm).not.toBeDisabled());
    fireEvent.click(confirm);
    await waitFor(() => expect(aiTradeApi.start).toHaveBeenCalledTimes(1));
    expect(vi.mocked(aiTradeApi.start).mock.calls[0][0].baseRules).toEqual({
      maBandEntry: { enabled: true, period: 20, stdMult: 2, tolerance: 0.15, directional: false },
    });
    await waitFor(() =>
      expect(useAppStore.getState().aiTradeConfig?.baseRules?.enabled).toBe(true),
    );
  });

  // 利润保护（ai-trade-profit-protection §3.3）：start 请求体携 profitGuard，成功后**取自 opts**
  // 写入 aiTradeConfig 并在下次打开弹窗回填——诊断根因 ③ 的闭环（回执不回显完整参数，取 res 会让
  // 偏好每次重开都回落默认）。单仓 TP/SL 同理一并记忆。
  it("sends profitGuard in the start body and persists it (with per-position OCO) to aiTradeConfig", async () => {
    vi.mocked(api.strategies).mockResolvedValue([{ id: 1, name: "均线策略" }] as never);
    const groupRow: AiTradeGroupRow = {
      groupId: "grp-1", bar: "1H", strategyIds: [1], strategyNames: ["均线策略"],
      totalBudgetUsd: 100, leverage: 3, contextRefreshRounds: 10, createdTs: 0,
      members: [], wakeIntervalMinutes: 2,
      baseRules: { maBandEntry: { enabled: true } },
      profitGuard: { enabled: true },
    };
    vi.mocked(aiTradeApi.start).mockResolvedValue(groupRow);
    useAppStore.setState({ aiTradeAcknowledged: true });
    renderToggle();
    await screen.findByTitle("开启 AI 自动交易 / 回测");
    fireEvent.click(screen.getByRole("switch"));
    await screen.findByText("均线策略");
    fireEvent.change(screen.getByLabelText("允许回吐（%）"), { target: { value: "35" } });
    fireEvent.change(screen.getByLabelText("单仓止损%（如 0.05＝5%）"), { target: { value: "0.03" } });
    const confirm = await screen.findByRole("button", { name: /确认并开始 AI 自动交易/ });
    await waitFor(() => expect(confirm).not.toBeDisabled());
    fireEvent.click(confirm);
    await waitFor(() => expect(aiTradeApi.start).toHaveBeenCalledTimes(1));
    const body = vi.mocked(aiTradeApi.start).mock.calls[0][0];
    expect(body.profitGuard?.enabled).toBe(true);
    expect(body.profitGuard?.givebackPct).toBeCloseTo(0.35, 10);
    expect(body.stopLossPct).toBeCloseTo(0.03, 10);
    await waitFor(() =>
      expect(useAppStore.getState().aiTradeConfig?.profitGuard?.givebackPct).toBeCloseTo(0.35, 10),
    );
    expect(useAppStore.getState().aiTradeConfig?.stopLossPct).toBeCloseTo(0.03, 10);
  });

  it("stops the whole group when toggled off while running", async () => {
    useAppStore.setState({ aiTradeGroup: makeGroup() });
    vi.mocked(aiTradeApi.stopGroup).mockResolvedValue({
      groupId: "grp-1", stopReason: "user_stop", members: [],
    } as never);
    renderToggle();
    const sw = screen.getByRole("switch");
    expect(sw).toHaveAttribute("aria-checked", "true");
    fireEvent.click(sw);
    await waitFor(() => expect(aiTradeApi.stopGroup).toHaveBeenCalledWith("grp-1"));
  });
});


// ============ 三态控件（ai-trade-pause-resume §3.9.2）============
const COLD_RESUMABLE = {
  groupId: "grp-1",
  bar: "1H",
  accountId: 1,
  totalBudgetUsd: 300,
  leverage: 3,
  strategyNames: ["趋势带波段"],
  createdTs: 0,
  pausedTs: 1_700_000_000,
  pauseReason: "process_restart" as const,
  pauseReasonText: "上次未正常退出，已自动暂停",
  resumedCount: 0,
  offlineSeconds: 3600,
  offlineText: "1 小时",
  hasPosition: true,
  members: [
    { instId: "BTC-USDT-SWAP", runId: 1, position: { side: "long" as const, size: 0.6 } },
  ],
};

const READY_PREVIEW = {
  groupId: "grp-1",
  pausedTs: 1_700_000_000,
  asOfTs: 1_700_003_600,
  offlineSeconds: 3600,
  offlineText: "1 小时",
  ageExceeded: false,
  resumedCount: 0,
  pauseReason: "user" as const,
  pauseReasonText: "你暂停了它",
  contract: { bar: "1H", instIds: ["BTC-USDT-SWAP"], strategyNames: ["趋势带波段"] },
  drift: [],
  members: [
    { instId: "BTC-USDT-SWAP", verdict: "matched" as const, message: "与暂停时一致。" },
  ],
  verdict: "ready" as const,
  blockedReason: null,
};

describe("AiTradeToggle 的三态", () => {
  it("running：出现「⏸ 暂停」，点开确认框后按默认选项下发 holdPosition:true", async () => {
    vi.mocked(aiTradeApi.pauseGroup).mockResolvedValue({} as never);
    useAppStore.setState({ aiTradeGroup: makeGroup() });
    renderToggle();

    fireEvent.click(await screen.findByRole("button", { name: "暂停 AI 自动交易" }));
    fireEvent.click(await screen.findByRole("button", { name: "保留持仓并暂停" }));
    await waitFor(() =>
      expect(aiTradeApi.pauseGroup).toHaveBeenCalledWith("grp-1", { holdPosition: true }),
    );
  });

  // RV-4：暂停回执的 `notice` 是后端单点生成的判定性文案（§3.10）——它是唯一一处会说出
  // 「平仓其实没成功」「此刻没有任何持久化兜底」的地方。只写不读 = 用户永远看不到那句话。
  it("暂停回执的 notice 必须有读点（落进持久提示，不是被丢掉）", async () => {
    vi.mocked(aiTradeApi.pauseGroup).mockResolvedValue({
      notice: "已暂停，但平仓没有完成：BTC-USDT-SWAP 仍持有多头 0.6 张。",
    } as never);
    useAppStore.setState({ aiTradeGroup: makeGroup() });
    renderToggle();

    fireEvent.click(await screen.findByRole("button", { name: "暂停 AI 自动交易" }));
    fireEvent.click(await screen.findByRole("button", { name: "保留持仓并暂停" }));
    await waitFor(() =>
      expect(useAppStore.getState().aiTradeLifecycleNotice).toContain("平仓没有完成"),
    );
  });

  it("paused（热）：徽标 + 继续 + 结束；开关保持 off 且点它不会触发停止", async () => {
    useAppStore.setState({ aiTradeGroup: { ...makeGroup(), state: "paused" } });
    renderToggle();

    expect(await screen.findByText("已暂停")).toBeInTheDocument();
    expect(screen.getByRole("switch")).toHaveAttribute("aria-checked", "false");
    fireEvent.click(screen.getByRole("switch"));
    expect(aiTradeApi.stopGroup).not.toHaveBeenCalled();   // 暂停可逆、结束不可逆，两个入口不重叠
    expect(screen.getByRole("button", { name: "继续 AI 自动交易" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "结束 AI 自动交易" })).toBeInTheDocument();
  });

  it("paused（冷占位）：内存里没有分组也要出三态，点继续先拉复核再续跑", async () => {
    vi.mocked(aiTradeApi.resumePreview).mockResolvedValue(READY_PREVIEW as never);
    vi.mocked(aiTradeApi.resumeGroup).mockResolvedValue({
      groupId: "grp-1", bar: "1H", strategyIds: [1], strategyNames: ["S1"],
      totalBudgetUsd: 300, leverage: 3, contextRefreshRounds: 10, createdTs: 0,
      members: [], state: "running",
    } as never);
    useAppStore.setState({ aiTradeGroup: null, aiTradeResumable: COLD_RESUMABLE as never });
    renderToggle();

    fireEvent.click(await screen.findByRole("button", { name: "继续 AI 自动交易" }));
    await waitFor(() => expect(aiTradeApi.resumePreview).toHaveBeenCalledWith("grp-1"));

    fireEvent.click(await screen.findByRole("button", { name: "继续" }));
    await waitFor(() =>
      expect(aiTradeApi.resumeGroup).toHaveBeenCalledWith("grp-1", { acknowledge: false }),
    );
    // 续跑成功后热态回来、冷占位被清掉（两者互斥，绝不同时非空）。
    await waitFor(() => expect(useAppStore.getState().aiTradeResumable).toBeNull());
    expect(useAppStore.getState().aiTradeGroup?.groupId).toBe("grp-1");
  });

  it("结束（热暂停，带仓）：强制二选一 + 勾选，走 endGroup 并清掉运行期态", async () => {
    vi.mocked(aiTradeApi.endGroup).mockResolvedValue({} as never);
    const group = makeGroup();
    group.state = "paused";
    group.members["BTC-USDT-SWAP"].position = { side: "long", size: 0.6 } as never;
    useAppStore.setState({ aiTradeGroup: group, aiTradeResumable: null });
    renderToggle();

    fireEvent.click(await screen.findByRole("button", { name: "结束 AI 自动交易" }));
    const submit = await screen.findByRole("button", { name: "结束并归档" });
    expect(submit).toBeDisabled();                       // 没有默认值
    fireEvent.click(screen.getAllByRole("radio")[1]);    // 保留持仓
    expect(submit).toBeDisabled();                       // 还要勾选
    fireEvent.click(screen.getByLabelText("我知道这些持仓将不再由任何自动化管理"));
    fireEvent.click(submit);

    await waitFor(() =>
      expect(aiTradeApi.endGroup).toHaveBeenCalledWith("grp-1", {
        flatten: false,
        acknowledgeOrphanPosition: true,
      }),
    );
    await waitFor(() => expect(useAppStore.getState().aiTradeGroup).toBeNull());
  });

  // RV-1：冷续跑卡上没有会话对象，后端也就没有任何东西能代用户下平仓单（服务端同样 fail-closed）。
  // 这里**不给**那个选项，而不是给了再报错。
  it("结束（冷占位，带仓）：不出现「平仓」这一支，勾选后仍可归档", async () => {
    vi.mocked(aiTradeApi.endGroup).mockResolvedValue({
      orphanPositions: [
        { instId: "BTC-USDT-SWAP", position: { side: "long", size: 0.6 },
          backstop: { covered: false } },
      ],
    } as never);
    useAppStore.setState({ aiTradeGroup: null, aiTradeResumable: COLD_RESUMABLE as never });
    renderToggle();

    fireEvent.click(await screen.findByRole("button", { name: "结束 AI 自动交易" }));
    expect(screen.queryAllByRole("radio")).toHaveLength(0);
    const submit = await screen.findByRole("button", { name: "结束并归档" });
    expect(submit).toBeDisabled();
    fireEvent.click(screen.getByLabelText("我知道这些持仓将不再由任何自动化管理"));
    fireEvent.click(submit);

    await waitFor(() =>
      expect(aiTradeApi.endGroup).toHaveBeenCalledWith("grp-1", {
        flatten: false,
        acknowledgeOrphanPosition: true,
      }),
    );
    await waitFor(() => expect(useAppStore.getState().aiTradeResumable).toBeNull());
    // RV-4：`orphanPositions` 必须有读点——归档后 AI 面板整块消失，这条提示是「这些仓从此
    // 没人管了」唯一还能被说出来的地方。
    expect(useAppStore.getState().aiTradeLifecycleNotice).toContain("BTC-USDT-SWAP");
  });

  it("idle：三个新按钮一个都不出现（既有渲染逐字不变）", async () => {
    renderToggle();
    await screen.findByTitle("开启 AI 自动交易 / 回测");
    expect(screen.queryByRole("button", { name: "暂停 AI 自动交易" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "继续 AI 自动交易" })).not.toBeInTheDocument();
    expect(screen.queryByText("已暂停")).not.toBeInTheDocument();
  });
});
