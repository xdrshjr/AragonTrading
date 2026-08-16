import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import type { Trade, TriggerExplain } from "@/lib/types";
import { useAppStore } from "@/lib/store";
import { TRADES_MAX } from "@/lib/wireCaps";
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

// 成交缓冲触到 wire 封顶时的诚实提示（resilience-and-hci-hardening D4）。
// 「看不到全部」必须是被说出来的事实，否则用户会以为自己的成交丢了。
describe("实时成交上限提示（D4）", () => {
  function plainTrade(ts: number): Trade {
    return { ts, side: "long", price: 100 + ts, qty: 1, fee: 0, pnl: 0, positionAfter: 1, reason: "entry" };
  }

  it("触到上限时表尾出现「仅保留最近 N 笔」提示", () => {
    setTrades(Array.from({ length: TRADES_MAX }, (_, i) => plainTrade(i)));
    render(<AutoTradeTradesTable />);
    expect(screen.getByText(new RegExp(`仅保留最近 ${TRADES_MAX} 笔`))).toBeTruthy();
  });

  it("未触到上限时不出提示（差一笔也不出）", () => {
    setTrades(Array.from({ length: TRADES_MAX - 1 }, (_, i) => plainTrade(i)));
    render(<AutoTradeTradesTable />);
    expect(screen.queryByText(new RegExp(`仅保留最近 ${TRADES_MAX} 笔`))).toBeNull();
  });
});
