import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { accountApi } from "@/lib/accountApi";
import { agentApi } from "@/lib/agentApi";
import { api } from "@/lib/api";
import { aiTradeApi } from "@/lib/aiTradeApi";
import { resolveAiTradeLifecycle } from "@/lib/aiTrade";
import { ApiRequestError, toApiError } from "@/lib/api";
import { RESUME_MS } from "@/lib/httpTimeout";
import type { AiTradeGroupRow, AiTradeGroupState } from "@/lib/aiTradeTypes";
import { useAppStore } from "@/lib/store";
import { tradingApi } from "@/lib/tradingApi";
import {
  AI_TRADE_LAUNCH_STEP_TITLE,
  type AiTradeLaunchStepId,
} from "@/lib/aiTradeLaunchSteps";
import AiTradeToggle from "./AiTradeToggle";

/**
 * ai-trade-guided-launch 附录 A 的两个迁移 helper（与 `AiTradeLaunchModal.test.tsx` 里那一份同款）。
 *
 * 迁移规则按**步骤**计而不是按用例计：一条用例里每个步骤的首个查询目标之前各插一次 `gotoStep`；
 * 点击 CTA 的语句替换为 `submitLaunch()`。**断言本身一律不改。**
 */
async function gotoStep(id: AiTradeLaunchStepId) {
  fireEvent.click(
    await screen.findByRole("tab", { name: new RegExp(AI_TRADE_LAUNCH_STEP_TITLE[id]) }),
  );
  return screen.findByRole("heading", {
    level: 3,
    name: new RegExp(AI_TRADE_LAUNCH_STEP_TITLE[id]),
  });
}

async function submitLaunch(name: RegExp = /确认并开始 AI 自动交易/) {
  await gotoStep("review");
  const cta = await screen.findByRole("button", { name });
  await waitFor(() => expect(cta).not.toBeDisabled());
  fireEvent.click(cta);
  return cta;
}

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
    aiTradeSyncState: "ok",
    aiTradeReconcileUntil: null,
    aiTradeEnabled: false,
    aiTradeLifecycleNotice: null,
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

  // W4 / W5（intel-session-binding 轴 A）：提交体恒带 `intel`，且**默认不勾**；`setAiTradeConfig`
  // 记住的是 `opts.intel` 而不是回执（回执里的 `mode` 是服务端解析后的**生效值**，含派生档
  // `off`——拿它回填会把「我选了索引档但这次被运维总闸忽略了」固化成「我选了关闭」）。
  it("sends intel in the start body (default off) and persists opts.intel, not the receipt", async () => {
    vi.mocked(api.strategies).mockResolvedValue([{ id: 1, name: "均线策略" }] as never);
    const groupRow: AiTradeGroupRow = {
      groupId: "grp-1", bar: "1H", strategyIds: [1], strategyNames: ["均线策略"],
      totalBudgetUsd: 100, leverage: 3, contextRefreshRounds: 10, createdTs: 0,
      members: [], wakeIntervalMinutes: 2,
      // 服务端把它解析成了「关」（比如运维总闸把请求忽略了）——这一份**不许**被回填。
      intel: { enabled: false, mode: "off", source: "ops_off" },
    };
    vi.mocked(aiTradeApi.start).mockResolvedValue(groupRow);
    useAppStore.setState({ aiTradeAcknowledged: true });
    renderToggle();
    await screen.findByTitle("开启 AI 自动交易 / 回测");
    fireEvent.click(screen.getByRole("switch"));
    await screen.findByText("均线策略");
    await submitLaunch();
    await waitFor(() => expect(aiTradeApi.start).toHaveBeenCalledTimes(1));
    // W4：整键恒下发，且**默认不勾**（与弹窗里其余六张卡的方向相反，需求的明文要求）。
    expect(vi.mocked(aiTradeApi.start).mock.calls[0][0].intel)
      .toEqual({ enabled: false, mode: "index" });
    // W5：老 localStorage 无此键 ⇒ 回填 `{enabled:false, mode:"index"}`；记住的是 opts 那一份。
    await waitFor(() =>
      expect(useAppStore.getState().aiTradeConfig?.intel)
        .toEqual({ enabled: false, mode: "index" }),
    );
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
    await submitLaunch();
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
    await submitLaunch();
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
    await gotoStep("guards");
    fireEvent.change(screen.getByLabelText("允许回吐（%）"), { target: { value: "35" } });
    await gotoStep("capital");
    fireEvent.change(screen.getByLabelText("单仓止损%（如 0.05＝5%）"), { target: { value: "0.03" } });
    await submitLaunch();
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

// ============ 通栏排版契约（agent-panel-toolbar-two-rows §9.1 B）============
// 守的是**契约**而不是像素：jsdom 无布局引擎，「不折行 / 不抖动」只能靠冻结类名与结构来固定，
// 真实排版由人工验收（R1–R9，三档面板宽 × 明暗双主题）负责。
describe("AiTradeToggle 的通栏排版契约", () => {
  it("三态共用同一条定高单行：左簇可截断、右簇不收缩", async () => {
    const cases: { name: string; setup: () => void }[] = [
      { name: "idle", setup: () => useAppStore.setState({ aiTradeGroup: null, aiTradeResumable: null }) },
      { name: "running", setup: () => useAppStore.setState({ aiTradeGroup: makeGroup() }) },
      {
        name: "paused",
        setup: () => useAppStore.setState({ aiTradeGroup: { ...makeGroup(), state: "paused" } }),
      },
    ];
    for (const { name, setup } of cases) {
      setup();
      const view = renderToggle();
      // min-h-[24px]：idle 行内最高的是 20px 的 Switch，running / paused 多出 24px 的 sm Button，
      // 不钉死行高，开关一开一关整条控制条就抖一格并连带推动输入框。
      expect(screen.getByTestId("ai-trade-toggle-row"), name).toHaveClass(
        "min-h-[24px]",
        "justify-between",
      );
      const main = screen.getByTestId("ai-trade-toggle-main");
      expect(main, name).toHaveClass("min-w-0");
      const label = main.firstElementChild!;
      expect(label, name).toHaveTextContent("AI 自动交易");
      expect(label, name).toHaveClass("min-w-0", "truncate");
      // idle 态右簇是个空 div，但**必须存在**：三态同一落点，shrink-0 这条契约才在三态下都成立。
      expect(screen.getByTestId("ai-trade-toggle-actions"), name).toHaveClass("shrink-0");
      view.unmount();
    }
  });

  // 这一行唯一的到达路径是个竞态：四个弹窗的 onClose 一律 setError(null)，而 openResume() 是**先**
  // 开框再发请求 —— 复核失败时错误只会落在续跑弹窗里（那段同样是 role="alert"）。只有「在途时先
  // 关框、随后才失败」才能让错误落到一个没有弹窗承接的状态上。
  it("暂停态错误独占第二行且自然换行（按竞态路径构造）", async () => {
    let rejectPreview!: (e: unknown) => void;
    vi.mocked(aiTradeApi.resumePreview).mockImplementation(
      () => new Promise((_resolve, reject) => { rejectPreview = reject; }),
    );
    useAppStore.setState({ aiTradeGroup: { ...makeGroup(), state: "paused" } });
    renderToggle();

    fireEvent.click(await screen.findByRole("button", { name: "继续 AI 自动交易" }));
    await screen.findByTestId("ai-trade-resume-dialog");
    expect(screen.queryByTestId("ai-trade-toggle-error")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "取消" }));
    await waitFor(() =>
      expect(screen.queryByTestId("ai-trade-resume-dialog")).not.toBeInTheDocument(),
    );
    await act(async () => {
      rejectPreview(new Error("boom"));
    });

    const line = await screen.findByTestId("ai-trade-toggle-error");
    expect(line).toHaveAttribute("role", "alert");
    expect(line).toHaveClass("whitespace-normal");
    expect(line).not.toHaveClass("truncate");
    expect(line).not.toHaveClass("max-w-[220px]");
  });

  // K1：title 只能存在一份。留在根节点又抄一份到行容器，6 处 findByTitle 会因多命中直接抛错。
  it("承载门禁文案的 title 全组件只有一份", async () => {
    renderToggle();
    await screen.findByTitle("开启 AI 自动交易 / 回测");
    expect(screen.getAllByTitle("开启 AI 自动交易 / 回测")).toHaveLength(1);
  });
});

// ============ 生命周期真值化（ai-trade-lifecycle-truth §8.2 用例 15 / 17 / 18 / 25 / 29）============

const MEMBER_WIRE = {
  state: "running" as const, armed: true, reason: "armed" as const, reasonText: "",
  warmedBars: 300, feedAgeS: 3, staleAfterS: 10800, linkState: "up",
  nextWakeInS: 120, nextBarInS: 480, roundsCompleted: 4,
  pauseReason: null, pausedTs: 0, resumedTs: 0, stopReason: null,
};

function groupWire(over: Record<string, unknown> = {}) {
  return {
    state: "running", declaredState: "running", armed: true, membersArmed: 1, membersTotal: 1,
    asOfTs: Math.floor(Date.now() / 1000), notArmed: [], nextWakeInS: 192, nextBarInS: 480,
    pauseReason: null, pausedTs: 0, resumedCount: 0, ...over,
  } as never;
}

/** 带生命周期证据的本地热态。 */
function armedGroup(over: Record<string, unknown> = {}): AiTradeGroupState {
  const base = makeGroup();
  return {
    ...base,
    lifecycle: groupWire(),
    members: {
      "BTC-USDT-SWAP": {
        ...base.members["BTC-USDT-SWAP"],
        session: { ...base.members["BTC-USDT-SWAP"].session!, lifecycle: MEMBER_WIRE } as never,
      },
    },
    ...over,
  } as AiTradeGroupState;
}

describe("AiTradeToggle · 四态 + armed（F-6）", () => {
  it("用例 29：2 个标的死 1 个 ⇒ 徽标带分数，绝不让用户以为两个都停了", async () => {
    useAppStore.setState({
      aiTradeGroup: armedGroup({
        lifecycle: groupWire({
          armed: false, membersArmed: 1, membersTotal: 2,
          notArmed: [{ instId: "ETH-USDT-SWAP", state: "running", reason: "feed_stale",
                       reasonText: "行情已 12 分钟没有新 K 线" }],
        }),
      }),
    });
    renderToggle();
    expect(await screen.findByTestId("ai-trade-lifecycle-badge")).toHaveTextContent("1/2 未武装");
  });

  it("全部武装 ⇒「运行中」+「下一次决策」倒计时真的在走秒", async () => {
    useAppStore.setState({ aiTradeGroup: armedGroup() });
    renderToggle();
    expect(await screen.findByTestId("ai-trade-lifecycle-badge")).toHaveTextContent("运行中");
    expect(await screen.findByTestId("ai-trade-next-wake")).toHaveTextContent("下一次决策 03:1");
  });

  it("[评审 R1-6] paused ⇒ 倒计时**不渲染**（对着一个不会到来的时刻读秒是新的谎话）", async () => {
    useAppStore.setState({
      aiTradeGroup: armedGroup({ state: "paused", lifecycle: groupWire({ state: "paused" }) }),
    });
    renderToggle();
    await screen.findByRole("button", { name: "继续 AI 自动交易" });
    expect(screen.queryByTestId("ai-trade-next-wake")).not.toBeInTheDocument();
  });

  it("stopped（第四态）⇒ 徽标「已停止」+ 一颗「知道了」，点它才清运行期态", async () => {
    useAppStore.setState({
      aiTradeGroup: armedGroup({ state: "stopped", lifecycle: groupWire({ state: "stopped" }) }),
    });
    renderToggle();
    expect(await screen.findByTestId("ai-trade-lifecycle-badge")).toHaveTextContent("已停止");
    fireEvent.click(screen.getByRole("button", { name: "清理已停止的 AI 自动交易面板" }));
    await waitFor(() => expect(useAppStore.getState().aiTradeGroup).toBeNull());
  });

  it("[评审 R1-5] stopped **不算**活跃会话：本地留痕不该把「再开一个新分组」锁死", async () => {
    useAppStore.setState({
      aiTradeGroup: armedGroup({ state: "stopped", lifecycle: groupWire({ state: "stopped" }) }),
    });
    renderToggle();
    // 后端此刻已经清了组（B-1），容量闸不会 409 ⇒ 实盘段必须是**可用**的：点开开关就能看到
    // 「启动 AI 自动交易」那一屏，而不是被门禁挡回回测段。
    // 等门禁三条查询都水合（水合前 title 是「请先在设置中心配置 AI 模型」）。
    await screen.findByTitle("这次 AI 自动交易已结束。点「知道了」清理面板，或直接开启一个新的分组");
    fireEvent.click(screen.getByRole("switch"));
    expect(await screen.findByText("启动 AI 自动交易")).toBeInTheDocument();
  });

  it("用例 18：`unknown` 态下三个动作按钮 `disabled` 均为真（读不到就不让点）", async () => {
    useAppStore.setState({
      aiTradeGroup: armedGroup({ state: "paused", lifecycle: groupWire({ state: "paused" }) }),
      aiTradeSyncState: "unknown",
    });
    renderToggle();
    expect(await screen.findByTestId("ai-trade-lifecycle-badge")).toHaveTextContent("与后端同步中…");
    expect(screen.getByRole("button", { name: "继续 AI 自动交易" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "结束 AI 自动交易" })).toBeDisabled();
    expect(screen.getByRole("switch")).toHaveAttribute("aria-checked", "false");
  });

  it("用例 15（L-4 的守卫）：面板与开关在同一份 store 快照下必须给出**同一个**态", () => {
    const group = armedGroup({ state: "running", lifecycle: groupWire({ state: "stopped" }) });
    // 两个组件都只走 `resolveAiTradeLifecycle`；此前面板自己拼判据，于是徽标说「已停止」而
    // 开关还是开的 —— 用户对着两个互相矛盾的控件做真实资金决策（D-5）。
    expect(resolveAiTradeLifecycle(group, null)).toBe("stopped");
    expect(resolveAiTradeLifecycle(group, null)).toBe(
      resolveAiTradeLifecycle({ ...group }, null),
    );
  });
});

describe("AiTradeToggle · 写端点超时（用例 17 / 25 · L-7 · [评审 R0-6]）", () => {
  it("用例 25：续跑超时 ⇒ 界面**不出现「失败」二字**，出现「结果未知」并触发快对账", async () => {
    // **必须走真实的 `toApiError` 路径**：直接对 DOMException 断言的用例会绿着放过这个 bug
    // （`isTimeoutError` 在 mutation 的 onError 那一层恒为假）。
    const raw = Object.assign(new Error("aborted"), { name: "TimeoutError" });
    const translated = toApiError(raw, RESUME_MS, "/ai-trade/grp-1/resume");
    vi.mocked(aiTradeApi.resumePreview).mockResolvedValue({
      groupId: "grp-1", offlineText: "1 分", pauseReasonText: "", resumedCount: 0,
      contract: { bar: "1H", instIds: ["BTC-USDT-SWAP"], strategyNames: [] },
      drift: [], members: [], verdict: "ready",
    } as never);
    vi.mocked(aiTradeApi.resumeGroup).mockRejectedValue(translated);
    useAppStore.setState({
      aiTradeGroup: armedGroup({ state: "paused", lifecycle: groupWire({ state: "paused" }) }),
    });
    renderToggle();

    fireEvent.click(await screen.findByRole("button", { name: "继续 AI 自动交易" }));
    await screen.findByTestId("ai-trade-resume-dialog");
    fireEvent.click(await screen.findByRole("button", { name: "继续" }));

    await waitFor(() =>
      expect(useAppStore.getState().aiTradeLifecycleNotice).toContain("结果未知"),
    );
    expect(useAppStore.getState().aiTradeReconcileUntil).toBeTruthy();
    expect(document.body.textContent).not.toContain("失败");
  });

  it("非超时的 409 仍照常渲染逐标的明细（F-10 的白名单）", async () => {
    vi.mocked(aiTradeApi.resumePreview).mockResolvedValue({
      groupId: "grp-1", offlineText: "1 分", pauseReasonText: "", resumedCount: 0,
      contract: { bar: "1H", instIds: ["BTC-USDT-SWAP"], strategyNames: [] },
      drift: [], members: [], verdict: "ready",
    } as never);
    vi.mocked(aiTradeApi.resumeGroup).mockRejectedValue(
      new ApiRequestError("AI_TRADE_RESUME_PARTIALLY_DEAD", "整组失败", 409, [
        { instId: "ETH-USDT-SWAP", code: "stopped", message: "会话已结束（触发会话止损）" },
      ]),
    );
    useAppStore.setState({
      aiTradeGroup: armedGroup({ state: "paused", lifecycle: groupWire({ state: "paused" }) }),
    });
    renderToggle();

    fireEvent.click(await screen.findByRole("button", { name: "继续 AI 自动交易" }));
    await screen.findByTestId("ai-trade-resume-dialog");
    fireEvent.click(await screen.findByRole("button", { name: "继续" }));

    expect(await screen.findByText(/ETH-USDT-SWAP：会话已结束（触发会话止损）/)).toBeInTheDocument();
  });

  it("暂停超时同款处理（L-7 说的是三个写端点，不是只有 /resume）", async () => {
    const raw = Object.assign(new Error("aborted"), { name: "TimeoutError" });
    vi.mocked(aiTradeApi.pauseGroup).mockRejectedValue(
      toApiError(raw, 20_000, "/ai-trade/grp-1/pause"),
    );
    useAppStore.setState({ aiTradeGroup: armedGroup() });
    renderToggle();

    fireEvent.click(await screen.findByRole("button", { name: "暂停 AI 自动交易" }));
    fireEvent.click(await screen.findByRole("button", { name: "保留持仓并暂停" }));

    await waitFor(() =>
      expect(useAppStore.getState().aiTradeLifecycleNotice).toContain("结果未知"),
    );
  });
  it("明确的 409 ⇒ **退出**武装清单回到复核屏（不让一次已被拒绝的续跑对着清单空等 45 秒）", async () => {
    vi.mocked(aiTradeApi.resumePreview).mockResolvedValue({
      groupId: "grp-1", offlineText: "1 分", pauseReasonText: "", resumedCount: 0,
      contract: { bar: "1H", instIds: ["BTC-USDT-SWAP"], strategyNames: [] },
      drift: [], members: [], verdict: "ready",
    } as never);
    vi.mocked(aiTradeApi.resumeGroup).mockRejectedValue(
      new ApiRequestError("AI_TRADE_RESUME_NOT_ARMED", "继续失败，已退回暂停态。", 409, [
        { instId: "BTC-USDT-SWAP", code: "feed_down", message: "实时链路不可达" },
      ]),
    );
    useAppStore.setState({
      aiTradeGroup: armedGroup({ state: "paused", lifecycle: groupWire({ state: "paused" }) }),
    });
    renderToggle();

    fireEvent.click(await screen.findByRole("button", { name: "继续 AI 自动交易" }));
    await screen.findByTestId("ai-trade-resume-dialog");
    fireEvent.click(await screen.findByRole("button", { name: "继续" }));

    await screen.findByText(/BTC-USDT-SWAP：实时链路不可达/);
    // 回到复核屏：武装清单不再挂着，主按钮立刻可再试。
    expect(screen.queryByTestId("ai-trade-arming-checklist")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "继续" })).toBeInTheDocument();
  });
});
