import "@testing-library/jest-dom/vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type {
  AiTradeGroupState,
  AiTradeSessionRow,
  ProfitGuardSnapshot,
} from "@/lib/aiTradeTypes";
import { useAppStore } from "@/lib/store";
import type { Instrument, PaperPositionEvent } from "@/lib/types";
import AiTradePanel from "./AiTradePanel";

// AiTradePanel 内部订阅 store.aiTradeGroup（非 props 注入），故用例须 seed 分组并逐例复位。
function makeSession(instId: string, over: Partial<AiTradeSessionRow> = {}): AiTradeSessionRow {
  return {
    sessionId: `sess-${instId}`,
    runId: 1,
    state: "running",
    instId,
    bar: "1H",
    strategyIds: [1],
    strategyNames: ["S"],
    budgetUsd: 500,
    leverage: 3,
    contextRefreshRounds: 10,
    roundsCompleted: 2,
    roundsSinceSummary: 1,
    positionSide: "long",
    sessionPnlUsd: 12.3,
    startedTs: 0,
    ...over,
  };
}

function makeGroup(sessions: AiTradeSessionRow[]): AiTradeGroupState {
  const members: AiTradeGroupState["members"] = {};
  for (const s of sessions) {
    members[s.instId] = {
      instId: s.instId,
      sessionId: s.sessionId,
      allocationPct: Math.round(100 / sessions.length),
      session: s,
      trades: [],
      position: null,
      decisions: [],
      traces: [],
      summary: null,
    };
  }
  return {
    groupId: "g1",
    bar: "1H",
    strategyIds: [1],
    strategyNames: ["S"],
    totalBudgetUsd: 1000,
    leverage: 3,
    contextRefreshRounds: 10,
    state: "running",
    startedTs: 0,
    members,
  };
}

const ROWS: Instrument[] = [
  { instId: "BTC-USDT-SWAP", instType: "SWAP", base: "BTC", quote: "USDT", last: 68420, changePct: 0.012 },
  { instId: "ETH-USDT-SWAP", instType: "SWAP", base: "ETH", quote: "USDT", last: 3820, changePct: -0.004 },
];

const noop = () => {};

afterEach(() => {
  cleanup();
  useAppStore.setState({ aiTradeGroup: null, aiTradeSyncState: "ok" });
});

describe("AiTradePanel", () => {
  it("极低价格保留非零数值和完整价格单位提示", () => {
    const instId = "SATS-USDT-SWAP";
    useAppStore.setState({ aiTradeGroup: makeGroup([makeSession(instId)]) });
    render(<AiTradePanel rows={[{ instId, instType: "SWAP", base: "SATS",
      quote: "USDT", last: .0000000123456789 }]}
      active={instId} livePrice={null} changePct={null} onSelect={noop} />);
    expect(screen.getByText("1.235e-8")).toBeInTheDocument();
    expect(screen.getByTitle("0.0000000123456789 USDT")).toBeInTheDocument();
  });
  it("五成员各显示服务端原因，状态更新保留选择和成交", () => {
    const symbols = ["BTC", "ETH", "SOL", "DOGE", "SATS"];
    const group = makeGroup(symbols.map((symbol) => makeSession(
      `${symbol}-USDT-SWAP`, { integrity: {
        schemaVersion: 2, asOfTs: 100,
        diagnosis: { code: "model_hold", message: `${symbol} 当前证据不足` },
      } },
    )));
    useAppStore.setState({ aiTradeGroup: group });
    const selected = vi.fn();
    render(<AiTradePanel rows={ROWS} active="SATS-USDT-SWAP"
      livePrice={null} changePct={null} onSelect={selected} />);
    expect(screen.getAllByTestId("ai-member-integrity")).toHaveLength(5);
    const reason = screen.getByText("SATS 当前证据不足");
    const button = reason.closest("button")!;
    expect(button).toHaveAttribute("aria-current", "true");
    expect(button.querySelector("button")).toBeNull();
    button.focus();
    expect(button).toHaveFocus();
    fireEvent.click(button);
    expect(selected).toHaveBeenCalledWith("SATS-USDT-SWAP");
    const before = useAppStore.getState().aiTradeGroup!.members["SATS-USDT-SWAP"];
    act(() => useAppStore.getState().patchAiTradeMemberStatus("SATS-USDT-SWAP", {
      state: "running", integrity: { schemaVersion: 2,
        diagnosis: { code: "pending_unknown", message: "回执待核实" } },
    }));
    const after = useAppStore.getState().aiTradeGroup!.members["SATS-USDT-SWAP"];
    expect(after.trades).toBe(before.trades);
    expect(after.session?.integrity?.diagnosis?.message).toBe("回执待核实");
  });
  it("renders nothing when there is no AI trade group", () => {
    useAppStore.setState({ aiTradeGroup: null });
    const { container } = render(
      <AiTradePanel rows={ROWS} active="" livePrice={null} changePct={null} onSelect={noop} />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("renders the group header and a single auto-trade symbol row, without execution details", () => {
    useAppStore.setState({ aiTradeGroup: makeGroup([makeSession("BTC-USDT-SWAP")]) });
    render(
      <AiTradePanel rows={ROWS} active="BTC-USDT-SWAP" livePrice={null} changePct={null} onSelect={noop} />,
    );
    expect(screen.getByText("AI 自动交易")).toBeInTheDocument();
    expect(screen.getByText("运行中")).toBeInTheDocument();
    expect(screen.getByText("自动交易标的")).toBeInTheDocument();
    expect(screen.getByText("BTC-USDT-SWAP")).toBeInTheDocument();
    expect(screen.getByText("1H")).toBeInTheDocument();
    // 执行情况（决策日志 / 策略信号板 / 上下文摘要）已迁到右侧「AI 助手」，左侧不再呈现。
    expect(screen.queryByText(/决策日志/)).not.toBeInTheDocument();
    expect(screen.queryByText("策略信号板")).not.toBeInTheDocument();
    expect(screen.queryByText("上下文摘要")).not.toBeInTheDocument();
  });

  it("renders one row per member for a multi-symbol group", () => {
    useAppStore.setState({
      aiTradeGroup: makeGroup([makeSession("BTC-USDT-SWAP"), makeSession("ETH-USDT-SWAP")]),
    });
    render(
      <AiTradePanel rows={ROWS} active="BTC-USDT-SWAP" livePrice={null} changePct={null} onSelect={noop} />,
    );
    expect(screen.getByText("BTC-USDT-SWAP")).toBeInTheDocument();
    expect(screen.getByText("ETH-USDT-SWAP")).toBeInTheDocument();
    expect(screen.getByText("2 个标的")).toBeInTheDocument();
  });

  it("calls onSelect with the member instId when its symbol row is clicked", () => {
    useAppStore.setState({
      aiTradeGroup: makeGroup([makeSession("BTC-USDT-SWAP"), makeSession("ETH-USDT-SWAP")]),
    });
    const onSelect = vi.fn();
    render(
      <AiTradePanel rows={ROWS} active="BTC-USDT-SWAP" livePrice={null} changePct={null} onSelect={onSelect} />,
    );
    fireEvent.click(screen.getByText("ETH-USDT-SWAP"));
    expect(onSelect).toHaveBeenCalledWith("ETH-USDT-SWAP");
  });
});

// ---------------- 利润保护行（ai-trade-profit-protection §六.4 / [P1-8]）----------------
const GUARD: ProfitGuardSnapshot = {
  ruleId: "profit_trailing",
  status: "armed",
  side: "long",
  entryPx: 0.07245,
  livePx: 0.07305,
  peakPx: 0.0732,
  peakUnrealizedPnlUsd: 0.302,
  givebackRatio: 0.2,
  effectiveExitPx: 0.0729,
};

function makePosition(guard: ProfitGuardSnapshot | null): PaperPositionEvent {
  return {
    side: "long",
    size: 1,
    avgEntry: 0.07245,
    markPrice: 0.07305,
    unrealizedPnl: 0.24,
    profitGuard: guard,
  };
}

function seedGuardGroup(guard: ProfitGuardSnapshot | null, positionSide: "long" | "flat") {
  const group = makeGroup([makeSession("BTC-USDT-SWAP", { positionSide })]);
  group.members["BTC-USDT-SWAP"].position = makePosition(guard);
  useAppStore.setState({ aiTradeGroup: group });
}

describe("AiTradePanel · 利润保护行", () => {
  it("持仓且有快照 → 展示峰值 / 已回吐 / 有效退出线", () => {
    seedGuardGroup(GUARD, "long");
    render(
      <AiTradePanel rows={ROWS} active="BTC-USDT-SWAP" livePrice={null} changePct={null} onSelect={noop} />,
    );
    const row = screen.getByTestId("ai-profit-guard");
    expect(row).toHaveTextContent("峰值");
    expect(row).toHaveTextContent("已回吐 20.0%");
    expect(row).toHaveTextContent("移动止盈已武装");
  });

  it("无快照（规则未装配 / 老会话）→ 整行不渲染", () => {
    seedGuardGroup(null, "long");
    render(
      <AiTradePanel rows={ROWS} active="BTC-USDT-SWAP" livePrice={null} changePct={null} onSelect={noop} />,
    );
    expect(screen.queryByTestId("ai-profit-guard")).not.toBeInTheDocument();
  });

  it("[P1-8] 快照仍在但已空仓（positionSide==='flat'）→ 保护行必须消失", () => {
    // `member.position` 在平仓后不会被清空（后端无仓时不发 position 事件、store 也无复位路径），
    // 只判快照存在会继续渲染一个**已不存在持仓**的峰值与退出线——这正是本用例守的回归。
    seedGuardGroup(GUARD, "flat");
    render(
      <AiTradePanel rows={ROWS} active="BTC-USDT-SWAP" livePrice={null} changePct={null} onSelect={noop} />,
    );
    expect(screen.queryByTestId("ai-profit-guard")).not.toBeInTheDocument();
  });

  it("unavailable → 只出一句「暂不可用」，绝不显示退出线", () => {
    seedGuardGroup({ ...GUARD, status: "unavailable", unavailableReason: "stale_feed" }, "long");
    render(
      <AiTradePanel rows={ROWS} active="BTC-USDT-SWAP" livePrice={null} changePct={null} onSelect={noop} />,
    );
    const row = screen.getByTestId("ai-profit-guard");
    expect(row).toHaveTextContent("利润保护暂不可用（行情数据陈旧）");
    expect(row).not.toHaveTextContent("退出线");
  });

  it("承诺退出线（commitment C4）→ 展示承诺线 + 「当前由它定住」（只读 effectiveExitSource）", () => {
    seedGuardGroup(
      {
        ...GUARD,
        committedExitPx: 0.07298,
        committedExitBasis: "adopted",
        effectiveExitSource: "profit_guard_committed_exit",
      },
      "long",
    );
    render(
      <AiTradePanel rows={ROWS} active="BTC-USDT-SWAP" livePrice={null} changePct={null} onSelect={noop} />,
    );
    const committed = screen.getByTestId("ai-profit-guard-committed");
    expect(committed).toHaveTextContent("承诺退出线 0.07298");
    expect(committed).toHaveTextContent("当前由它定住");
  });

  it("无承诺线 → 承诺行不渲染", () => {
    seedGuardGroup(GUARD, "long");
    render(
      <AiTradePanel rows={ROWS} active="BTC-USDT-SWAP" livePrice={null} changePct={null} onSelect={noop} />,
    );
    expect(screen.queryByTestId("ai-profit-guard-committed")).not.toBeInTheDocument();
  });
});

// 冷却 chip 的门控是 `member.profitGuardCooldown`（与 position 平级），**不是** position 之下的
// 快照：冷却期内按定义已无持仓，挂在 position 之下这枚 chip 永远不会出现（adaptive §4.2 / P1-4）。
describe("AiTradePanel · 利润保护冷却 chip", () => {
  const COOLDOWN = {
    activeUntilTs: 1_700_000_180,
    remainingSeconds: 120,
    scope: "same_side" as const,
    blockedSide: "long" as const,
    reasonCode: "profit_guard_trailing",
    label: "移动止盈",
  };

  it("平仓后（position 为 null）仍渲染冷却 chip，含剩余时间与豁免范围", () => {
    const group = makeGroup([makeSession("BTC-USDT-SWAP", { positionSide: "flat" })]);
    group.members["BTC-USDT-SWAP"].profitGuardCooldown = COOLDOWN;
    useAppStore.setState({ aiTradeGroup: group });
    render(
      <AiTradePanel rows={ROWS} active="BTC-USDT-SWAP" livePrice={null} changePct={null} onSelect={noop} />,
    );
    const chip = screen.getByTestId("ai-profit-cooldown");
    expect(chip).toHaveTextContent("120s");
    expect(chip).toHaveTextContent("暂停做多开仓（反向不受影响）");
    expect(chip).toHaveTextContent("平仓不受限");
  });

  it("冷却已到期 / 未激活 → chip 不渲染（后端到期会主动补推一次 status 把它摘掉）", () => {
    const group = makeGroup([makeSession("BTC-USDT-SWAP", { positionSide: "flat" })]);
    group.members["BTC-USDT-SWAP"].profitGuardCooldown = { ...COOLDOWN, remainingSeconds: 0 };
    useAppStore.setState({ aiTradeGroup: group });
    render(
      <AiTradePanel rows={ROWS} active="BTC-USDT-SWAP" livePrice={null} changePct={null} onSelect={noop} />,
    );
    expect(screen.queryByTestId("ai-profit-cooldown")).not.toBeInTheDocument();
  });
});


// ---------- 交易周期 chip（ai-trade-holding-horizon-modes §2.11 展示落点之一）----------
function horizonSnapshot(over: Record<string, unknown> = {}) {
  return {
    profileId: "intraday_swing", title: "日内波段", targetLabel: "4 – 12 小时",
    status: "holding", holdSeconds: 5400, barsInPosition: 2, targetHoldMinSeconds: 14400,
    targetHoldMaxSeconds: 43200, minHoldSeconds: 7200, minHoldRemainingSeconds: 1800,
    exitConfirmRounds: 2, exitConfirmStreak: 0, confirmWindowSeconds: 3600,
    confirmWindowRemainingSeconds: null, confirmWindowElapsed: false,
    turnoverHysteresisK: 1.5, moveBps: 8.2, costBps: 13, requiredMoveBps: 19.5,
    returnPct: 0.02, lossReleaseReturnPct: 0.075, released: false, releaseReason: null,
    releasedGates: [], blockedStreak: 0, expectedHoldBars: 12, openedWallEstimated: false,
    gateReverse: true, asOfWall: 1_700_000_000, ...over,
  } as NonNullable<AiTradeSessionRow["horizon"]>;
}

describe("AiTradePanel · 交易周期 chip", () => {
  afterEach(() => useAppStore.setState({ aiTradeGroup: null }));

  it("持仓中显示倒计时（已持仓 / 最短 / 还差）", () => {
    useAppStore.setState({
      aiTradeGroup: makeGroup([makeSession("BTC-USDT-SWAP", { horizon: horizonSnapshot() })]),
    });
    render(<AiTradePanel rows={ROWS} active="BTC-USDT-SWAP" onSelect={noop} />);
    const chip = screen.getByTestId("ai-trade-horizon-chip");
    expect(chip.textContent).toContain("日内波段");
    expect(chip.textContent).toContain("还差 30 分钟");
  });

  it("released ⇒ 点名原因并用 --short 色（此刻锁是开的，用常规灰会让用户以为限制还在）", () => {
    useAppStore.setState({
      aiTradeGroup: makeGroup([makeSession("BTC-USDT-SWAP", {
        horizon: horizonSnapshot({ status: "released", released: true,
                                   releaseReason: "loss_release" }),
      })]),
    });
    render(<AiTradePanel rows={ROWS} active="BTC-USDT-SWAP" onSelect={noop} />);
    const chip = screen.getByTestId("ai-trade-horizon-chip");
    expect(chip.textContent).toContain("限制已解除");
    expect(chip.className).toContain("--short");
  });

  it("空仓态**不被吞掉**：如实告知「本层不约束开仓」（RV-2 教训：新增边缘态须验渲染门控）", () => {
    useAppStore.setState({
      aiTradeGroup: makeGroup([makeSession("BTC-USDT-SWAP", {
        positionSide: "flat", horizon: horizonSnapshot({ status: "flat", holdSeconds: 0 }),
      })]),
    });
    render(<AiTradePanel rows={ROWS} active="BTC-USDT-SWAP" onSelect={noop} />);
    expect(screen.getByTestId("ai-trade-horizon-chip").textContent).toContain("待入场");
  });

  it("未装配 ⇒ 整行不渲染（老会话与老快照零影响）", () => {
    useAppStore.setState({ aiTradeGroup: makeGroup([makeSession("BTC-USDT-SWAP")]) });
    render(<AiTradePanel rows={ROWS} active="BTC-USDT-SWAP" onSelect={noop} />);
    expect(screen.queryByTestId("ai-trade-horizon-chip")).toBeNull();
  });
});

// ============ 生命周期真值化（ai-trade-lifecycle-truth F-1 / F-5 / F-6 · 用例 15 / 29）============

const PANEL_MEMBER_WIRE = {
  state: "running" as const, armed: true, reason: "armed" as const, reasonText: "",
  warmedBars: 300, feedAgeS: 3, staleAfterS: 10800, linkState: "up",
  nextWakeInS: 120, nextBarInS: 480, roundsCompleted: 4,
  pauseReason: null, pausedTs: 0, resumedTs: 0, stopReason: null,
};

function panelGroupWire(over: Record<string, unknown> = {}) {
  return {
    state: "running", declaredState: "running", armed: true, membersArmed: 1, membersTotal: 1,
    asOfTs: Math.floor(Date.now() / 1000), notArmed: [], nextWakeInS: 192, nextBarInS: 480,
    pauseReason: null, pausedTs: 0, resumedCount: 0, ...over,
  } as never;
}

function renderPanel() {
  return render(
    <AiTradePanel rows={ROWS} active="BTC-USDT-SWAP" livePrice={null} changePct={null}
                  onSelect={noop} />,
  );
}

describe("AiTradePanel · 生命周期真值", () => {
  it("用例 15（L-4）：徽标只走唯一推导点 —— 派生的 `lifecycle.state` 优先于被赋值的 `group.state`", () => {
    useAppStore.setState({
      aiTradeGroup: {
        ...makeGroup([makeSession("BTC-USDT-SWAP", { state: "stopped",
                                                    stopReason: "session_stop_loss" })]),
        state: "running",                                   // 意图态还写着 running
        lifecycle: panelGroupWire({ state: "stopped", armed: false, membersArmed: 0 }),
      } as never,
    });
    renderPanel();
    expect(screen.getByTestId("ai-trade-panel-badge")).toHaveTextContent("已停止");
  });

  it("F-5：终态留痕卡说清「为什么它自己停了」+ 会话净额 + 持仓归属提醒", () => {
    useAppStore.setState({
      aiTradeGroup: {
        ...makeGroup([makeSession("BTC-USDT-SWAP", { state: "stopped",
                                                    stopReason: "session_stop_loss",
                                                    sessionPnlUsd: -42.5 })]),
        state: "stopped",
        lifecycle: panelGroupWire({ state: "stopped", armed: false, membersArmed: 0 }),
      } as never,
    });
    renderPanel();
    const card = screen.getByTestId("ai-trade-stopped-card");
    expect(card).toHaveTextContent("触发会话止损");
    expect(card).toHaveTextContent("-42.5");
    expect(card).toHaveTextContent("不再由任何自动化管理");
  });

  it("用例 29：多标的下横幅**逐标的**说明，绝不把还在正常交易的那个也说成停了", () => {
    useAppStore.setState({
      aiTradeGroup: {
        ...makeGroup([makeSession("BTC-USDT-SWAP"), makeSession("ETH-USDT-SWAP")]),
        lifecycle: panelGroupWire({
          armed: false, membersArmed: 1, membersTotal: 2,
          notArmed: [{ instId: "ETH-USDT-SWAP", state: "running", reason: "feed_stale",
                       reasonText: "行情已 12 分钟没有新 K 线，Agent 暂不响应" }],
        }),
      } as never,
    });
    renderPanel();
    expect(screen.getByTestId("ai-trade-panel-badge")).toHaveTextContent("1/2 未武装");
    const banner = screen.getByTestId("ai-trade-not-armed-banner");
    expect(banner).toHaveTextContent("ETH-USDT-SWAP：");
    expect(banner).toHaveTextContent("行情已 12 分钟没有新 K 线");
    expect(banner).toHaveTextContent("其余照常交易");
    expect(banner).toHaveAttribute("role", "alert");
  });

  it("全部武装 ⇒ 不挂横幅，且「下一次决策」倒计时在走", () => {
    useAppStore.setState({
      aiTradeGroup: {
        ...makeGroup([makeSession("BTC-USDT-SWAP")]),
        lifecycle: panelGroupWire(),
      } as never,
    });
    renderPanel();
    expect(screen.queryByTestId("ai-trade-not-armed-banner")).not.toBeInTheDocument();
    expect(screen.getByTestId("ai-trade-panel-next-wake")).toHaveTextContent("下一次决策");
  });

  it("老后端不下发 lifecycle ⇒ 徽标回落到 group.state，且**绝不**因此挂一条假红条", () => {
    useAppStore.setState({
      aiTradeGroup: { ...makeGroup([makeSession("BTC-USDT-SWAP")]), lifecycle: null } as never,
    });
    renderPanel();
    expect(screen.getByTestId("ai-trade-panel-badge")).toHaveTextContent("运行中");
    expect(screen.queryByTestId("ai-trade-not-armed-banner")).not.toBeInTheDocument();
    expect(screen.queryByTestId("ai-trade-panel-next-wake")).not.toBeInTheDocument();
  });

  it("`unknown`（后端读不到）⇒ 徽标改说「与后端同步中…」，绝不冒充一个确定的态", () => {
    useAppStore.setState({
      aiTradeGroup: {
        ...makeGroup([makeSession("BTC-USDT-SWAP")]),
        lifecycle: panelGroupWire(),
      } as never,
      aiTradeSyncState: "unknown",
    });
    renderPanel();
    expect(screen.getByTestId("ai-trade-panel-badge")).toHaveTextContent("与后端同步中…");
    useAppStore.setState({ aiTradeSyncState: "ok" });
  });

  it("paused ⇒ 暂停横幅照旧 + 倒计时不渲染（[评审 R1-6]）", () => {
    useAppStore.setState({
      aiTradeGroup: {
        ...makeGroup([makeSession("BTC-USDT-SWAP", { state: "paused" })]),
        state: "paused",
        lifecycle: panelGroupWire({ state: "paused", armed: false, membersArmed: 0 }),
      } as never,
    });
    renderPanel();
    expect(screen.getByTestId("ai-trade-panel-badge")).toHaveTextContent("已暂停");
    expect(screen.getByTestId("ai-trade-paused-banner")).toBeInTheDocument();
    expect(screen.queryByTestId("ai-trade-panel-next-wake")).not.toBeInTheDocument();
    expect(screen.queryByTestId("ai-trade-not-armed-banner")).not.toBeInTheDocument();
  });

  it("成员级 lifecycle 能穿过 store 到达成员槽（风险 R-13 的白名单守卫）", () => {
    useAppStore.setState({
      aiTradeGroup: {
        ...makeGroup([makeSession("BTC-USDT-SWAP", { lifecycle: PANEL_MEMBER_WIRE })]),
        lifecycle: panelGroupWire(),
      } as never,
    });
    renderPanel();
    expect(
      useAppStore.getState().aiTradeGroup?.members["BTC-USDT-SWAP"].session?.lifecycle?.armed,
    ).toBe(true);
  });
  it("F-4 的读点：会话状态行（行情断连 / 补根结果 / WS 错误帧）必须真的渲染出来", () => {
    useAppStore.setState({
      aiTradeGroup: {
        ...makeGroup([makeSession("BTC-USDT-SWAP", {
          message: "行情连接异常，AI 自动交易暂停响应 K 线（交易所侧止盈止损仍生效）" })]),
        lifecycle: panelGroupWire(),
      } as never,
    });
    renderPanel();
    expect(screen.getByTestId("ai-trade-member-message"))
      .toHaveTextContent("行情连接异常");
  });
});

// ---------------- silent-degradation-repair 轴 A-4 / 轴 E 的两个面板落点 ----------------
describe("AiTradePanel · 真值源与费率来源", () => {
  it("轴 A-4：降级态的费率来源必须**带着原因**出现在面板上（§14.4 第 1 步的硬判据）", () => {
    useAppStore.setState({
      aiTradeGroup: {
        ...makeGroup([makeSession("BTC-USDT-SWAP")]),
        lifecycle: panelGroupWire(),
        feeRates: {
          makerBps: 2, takerBps: 5, level: "", ruleType: "", source: "config",
          rebate: false, ageS: 0, stale: false, makerBpsRaw: 2, takerBpsRaw: 5,
          attempted: true,
          lastError: { rung: "type", httpStatus: 400, code: "50016",
                       msg: "instId and instType don't match" },
        },
      } as never,
    });
    renderPanel();
    const strip = screen.getByTestId("ai-fee-source-strip");
    expect(strip).toHaveAttribute("data-degraded", "1");
    expect(strip).toHaveTextContent("本地保守常数：maker 2 / taker 5 bps");
    // 「这是猜的」不够，还要说**为什么**猜——三周来用户看到的一直是一个没有原因的降级。
    expect(strip).toHaveTextContent("交易所拒绝了费率查询（50016 instId and instType don't match）");
  });

  it("缺键（老后端 / 总闸关）⇒ 整段不渲染，绝不编一句「按官方档位」", () => {
    useAppStore.setState({
      aiTradeGroup: { ...makeGroup([makeSession("BTC-USDT-SWAP")]),
                      lifecycle: panelGroupWire() } as never,
    });
    renderPanel();
    expect(screen.queryByTestId("ai-fee-source-strip")).not.toBeInTheDocument();
    expect(screen.queryByTestId("ai-truth-sources-chip")).not.toBeInTheDocument();
  });

  it("intel-session-binding 轴 A-4：情报采信回执必须在分组卡上有一个读点", () => {
    // 后端算了、`groupStateFromStatus` 抄了、却没有任何组件读 —— 那就是死数据。
    // `legacy_fallback` 这一格只有「刷新页面后重发现一个暂停中的老会话」才走得到。
    useAppStore.setState({
      aiTradeGroup: {
        ...makeGroup([makeSession("BTC-USDT-SWAP")]),
        lifecycle: panelGroupWire(),
        intel: { enabled: true, mode: "full", source: "legacy_fallback" },
      } as never,
    });
    renderPanel();
    const strip = screen.getByTestId("ai-intel-receipt-strip");
    expect(strip).toHaveAttribute("data-overridden", "1");
    expect(strip).toHaveTextContent("全文档");
    expect(strip).toHaveTextContent("该开关上线之前");
  });

  it("用户自己选的 ⇒ 不带颜色；老后端缺键 ⇒ 整段不渲染", () => {
    useAppStore.setState({
      aiTradeGroup: {
        ...makeGroup([makeSession("BTC-USDT-SWAP")]),
        lifecycle: panelGroupWire(),
        intel: { enabled: false, mode: "off", source: "request" },
      } as never,
    });
    renderPanel();
    const strip = screen.getByTestId("ai-intel-receipt-strip");
    expect(strip).toHaveAttribute("data-overridden", "0");
    expect(strip).toHaveTextContent("未采信");

    cleanup();
    useAppStore.setState({
      aiTradeGroup: { ...makeGroup([makeSession("BTC-USDT-SWAP")]),
                      lifecycle: panelGroupWire() } as never,
    });
    renderPanel();
    expect(screen.queryByTestId("ai-intel-receipt-strip")).not.toBeInTheDocument();
  });

  it("轴 E：顶栏芯片只在有降级时出现，且**分母写明是 3**（第 4 行只在 REST 上）", () => {
    useAppStore.setState({
      aiTradeGroup: {
        ...makeGroup([makeSession("BTC-USDT-SWAP", {
          truthSources: { scope: "process", trackedCount: 3, degradedCount: 2,
                          keys: ["feeRates", "decisionModel"] } })]),
        lifecycle: panelGroupWire(),
      } as never,
    });
    renderPanel();
    const chip = screen.getByTestId("ai-truth-sources-chip");
    expect(chip).toHaveTextContent("3 项真值源中 2 项降级");
    expect(chip.textContent ?? "").not.toContain("**");
  });

  it("全部正常 ⇒ 芯片不出现（一条常驻的彩色横幅会在两天内变成背景噪声）", () => {
    useAppStore.setState({
      aiTradeGroup: {
        ...makeGroup([makeSession("BTC-USDT-SWAP", {
          truthSources: { scope: "process", trackedCount: 3, degradedCount: 0, keys: [] } })]),
        lifecycle: panelGroupWire(),
      } as never,
    });
    renderPanel();
    expect(screen.queryByTestId("ai-truth-sources-chip")).not.toBeInTheDocument();
  });
});
