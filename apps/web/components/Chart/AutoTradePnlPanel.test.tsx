import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { type LiveAutoGroupState, useAppStore } from "@/lib/store";
import type { Trade, TriggerExplain } from "@/lib/types";
import AutoTradePnlPanel from "./AutoTradePnlPanel";

const NOW_SEC = 1_790_000_000;
const DAY = 86_400;

function trade(o: Partial<Trade> & Pick<Trade, "ts" | "side">): Trade {
  return {
    id: o.id,
    runId: o.runId ?? 1,
    ts: o.ts,
    side: o.side,
    price: o.price ?? 100,
    qty: o.qty ?? 1,
    fee: o.fee ?? 0,
    pnl: o.pnl ?? 0,
    pnlPct: o.pnlPct,
    positionAfter: o.positionAfter ?? 0,
    reason: o.reason ?? "",
    explain: o.explain ?? null,
  };
}

const CLOSE_EXPLAIN: TriggerExplain = {
  schema: 1,
  kind: "atomic",
  side: "close",
  headline: "信号平仓",
  metrics: [],
};

// 单成员、两条完整往返：15 天前亏 4、10 分钟前盈 12（后者带 explain 可展开）；当前持仓浮盈 +7。
function makeGroup(): LiveAutoGroupState {
  return {
    groupId: "g1",
    strategyId: 1,
    strategyName: "s",
    bar: "1H",
    totalBudgetUsd: 100,
    leverage: 3,
    state: "running",
    startedTs: NOW_SEC - 30 * DAY,
    members: {
      "BTC-USDT-SWAP": {
        instId: "BTC-USDT-SWAP",
        sessionId: "a",
        runId: 1,
        allocationPct: 100,
        budgetUsd: 100,
        status: { state: "running", sessionPnlUsd: 19 },
        position: { side: "long", size: 1, avgEntry: 100, markPrice: 107, unrealizedPnl: 7 },
        trades: [
          trade({ id: 1, ts: NOW_SEC - 15 * DAY - 100, side: "long", price: 100 }),
          trade({ id: 2, ts: NOW_SEC - 15 * DAY, side: "close", price: 96, pnl: -4, pnlPct: -0.12 }),
          trade({ id: 3, ts: NOW_SEC - 700, side: "long", price: 100 }),
          trade({
            id: 4,
            ts: NOW_SEC - 600,
            side: "close",
            price: 110,
            pnl: 12,
            pnlPct: 0.36,
            explain: CLOSE_EXPLAIN,
          }),
        ],
      },
    },
  };
}

beforeEach(() => {
  localStorage.clear();
  vi.spyOn(Date, "now").mockReturnValue(NOW_SEC * 1000);
  useAppStore.setState({ liveAutoGroup: null });
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("AutoTradePnlPanel", () => {
  it("shows a not-started hint when no group is running", () => {
    render(<AutoTradePnlPanel open onClose={() => {}} />);
    expect(screen.getByText(/尚未启动自动交易/)).toBeTruthy();
  });

  it("aggregates total pnl, return rate and count across the all window", () => {
    useAppStore.setState({ liveAutoGroup: makeGroup() });
    render(<AutoTradePnlPanel open onClose={() => {}} />);
    const dialog = screen.getByRole("dialog");
    // total = -4 + 12 = 8；总收益率 = 8 / 100 = 8.00%；2 笔完整交易
    expect(within(dialog).getByText("+8.00")).toBeTruthy();
    expect(within(dialog).getByText("8.00%")).toBeTruthy();
    expect(within(dialog).getByText("2 笔")).toBeTruthy();
  });

  it("recomputes the summary and list when switching the time window", () => {
    useAppStore.setState({ liveAutoGroup: makeGroup() });
    render(<AutoTradePnlPanel open onClose={() => {}} />);
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByText("2 笔")).toBeTruthy();
    fireEvent.click(within(dialog).getByRole("button", { name: "近7日" }));
    // 仅近 10 分钟那笔落在 7 日内：1 笔、亏损行（-12.00%）消失、盈利行（36.00%）保留
    expect(within(dialog).queryByText("2 笔")).toBeNull();
    expect(within(dialog).getByText("1 笔")).toBeTruthy();
    expect(within(dialog).queryByText("-12.00%")).toBeNull();
    expect(within(dialog).getByText("36.00%")).toBeTruthy();
  });

  it("shows the unrealized floating pnl from positions, decoupled from the time window", () => {
    useAppStore.setState({ liveAutoGroup: makeGroup() });
    render(<AutoTradePnlPanel open onClose={() => {}} />);
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByText("+7.00 USDT")).toBeTruthy();
    fireEvent.click(within(dialog).getByRole("button", { name: "今日" }));
    expect(within(dialog).getByText("+7.00 USDT")).toBeTruthy(); // 切窗口浮盈不变
  });

  it("expands a round-trip row to reveal the close trigger explanation", () => {
    useAppStore.setState({ liveAutoGroup: makeGroup() });
    render(<AutoTradePnlPanel open onClose={() => {}} />);
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).queryByText("信号平仓")).toBeNull();
    fireEvent.click(within(dialog).getByText("36.00%")); // 点近往返行（带 explain）
    expect(within(dialog).getByText("信号平仓")).toBeTruthy();
  });

  it("shows an earlier-than-start hint for a custom window before the group started", () => {
    useAppStore.setState({ liveAutoGroup: makeGroup() });
    render(<AutoTradePnlPanel open onClose={() => {}} />);
    const dialog = screen.getByRole("dialog");
    fireEvent.click(within(dialog).getByRole("button", { name: "自定义" }));
    fireEvent.change(within(dialog).getByLabelText("自定义起始日期"), {
      target: { value: "2000-01-01" },
    });
    fireEvent.change(within(dialog).getByLabelText("自定义结束日期"), {
      target: { value: "2000-12-31" },
    });
    expect(within(dialog).getByText(/早于本组启动时间/)).toBeTruthy();
  });
});
