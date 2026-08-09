import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
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
  useAppStore.setState({ aiTradeGroup: null });
});

describe("AiTradePanel", () => {
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
