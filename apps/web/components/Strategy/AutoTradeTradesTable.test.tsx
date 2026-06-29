import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import type { Trade, TriggerExplain } from "@/lib/types";
import { useAppStore } from "@/lib/store";
import AutoTradeTradesTable from "./AutoTradeTradesTable";

function setTrades(trades: Trade[]) {
  useAppStore.setState({ liveAutoTrades: trades });
}

function factorTrade(): Trade {
  const explain: TriggerExplain = {
    schema: 1,
    kind: "factor_score",
    side: "long",
    headline: "复合得分多头",
    summary: "因子组合复合得分触发。",
    metrics: [{ label: "复合得分", value: "0.42" }],
    composite: { score: 0.42, preSquash: 0.45, squash: true, enterLong: 0.3, enterShort: 0.3 },
    factors: [
      {
        factorKey: "mom_roc",
        name: "动量 ROC",
        direction: "long",
        value: 1.2,
        zscore: 1.4,
        weight: 0.6,
        contribution: 0.84,
        share: 1.0,
        isTopDriver: true,
      },
    ],
  };
  return {
    ts: 1751200000,
    side: "long",
    price: 64210.5,
    qty: 0.03,
    fee: 0,
    pnl: 0,
    positionAfter: 0.03,
    reason: "复合得分多头",
    explain,
  };
}

beforeEach(() => {
  useAppStore.setState({ liveAutoTrades: [] });
});
afterEach(cleanup);

describe("AutoTradeTradesTable", () => {
  it("shows an empty-state hint when there are no auto-trade trades", () => {
    render(<AutoTradeTradesTable />);
    expect(screen.getByText(/暂无自动交易成交/)).toBeTruthy();
  });

  it("renders one collapsed row per trade by default", () => {
    setTrades([factorTrade()]);
    render(<AutoTradeTradesTable />);
    const toggle = screen.getByRole("button", { expanded: false });
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    // 收起态不渲染详情摘要。
    expect(screen.queryByText(/因子组合复合得分触发/)).toBeNull();
  });

  it("expands a row to reveal the explain detail and factor rows on click", () => {
    setTrades([factorTrade()]);
    render(<AutoTradeTradesTable />);
    const toggle = screen.getByRole("button", { expanded: false });
    fireEvent.click(toggle);
    expect(toggle.getAttribute("aria-expanded")).toBe("true");
    expect(screen.getByText(/因子组合复合得分触发/)).toBeTruthy();
    // factor_score 成交展开后渲染因子归因行。
    expect(screen.getByText("动量 ROC")).toBeTruthy();
    expect(screen.getByText(/因子归因/)).toBeTruthy();
  });

  it("collapses again when toggled twice", () => {
    setTrades([factorTrade()]);
    render(<AutoTradeTradesTable />);
    const toggle = screen.getByRole("button", { expanded: false });
    fireEvent.click(toggle);
    fireEvent.click(toggle);
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    expect(screen.queryByText(/因子组合复合得分触发/)).toBeNull();
  });
});
