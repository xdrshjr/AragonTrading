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
});

