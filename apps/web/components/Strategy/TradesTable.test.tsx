import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import type { Trade } from "@/lib/types";
import TradesTable from "./TradesTable";

const BASE_TRADE: Trade = {
  ts: 1609459200,
  side: "long",
  price: 100,
  qty: 0.5,
  fee: 0.1,
  pnl: 0,
  positionAfter: 0.5,
  reason: "entry",
};

describe("TradesTable risk columns", () => {
  it("renders stop-loss and take-profit percentages with trigger prices", () => {
    const markup = renderToStaticMarkup(
      <TradesTable
        trades={[
          {
            ...BASE_TRADE,
            stopLossPct: 0.02,
            stopLossPrice: 98,
            takeProfitPct: 0.04,
            takeProfitPrice: 104,
            maxHoldBars: 12,
          },
        ]}
      />,
    );

    expect(markup).toContain("止损(SL)");
    expect(markup).toContain("止盈(TP)");
    expect(markup).toContain("2.00% @ 98.00");
    expect(markup).toContain("4.00% @ 104.00");
    expect(markup).toContain("title=\"最长持有 12 根K线\"");
  });

  it("renders dashes for missing risk fields", () => {
    const markup = renderToStaticMarkup(<TradesTable trades={[BASE_TRADE]} />);

    expect(markup).toContain("<td class=\"py-1.5 pr-2 text-right tabular\" title=\"\">-</td>");
    expect(markup).toContain("<td class=\"py-1.5 text-right tabular\" title=\"\">-</td>");
  });
});

describe("TradesTable realized return percentage", () => {
  it("renders the realized return percentage on a close row carrying pnlPct", () => {
    const markup = renderToStaticMarkup(
      <TradesTable
        trades={[{ ...BASE_TRADE, side: "close", pnl: 19.05, pnlPct: 0.0148, positionAfter: 0 }]}
      />,
    );

    expect(markup).toContain("+19.05");
    expect(markup).toContain("1.48%");
  });

  it("omits the percentage when a close row has no pnlPct (backtest / paper)", () => {
    const markup = renderToStaticMarkup(
      <TradesTable trades={[{ ...BASE_TRADE, side: "close", pnl: 5, positionAfter: 0 }]} />,
    );

    expect(markup).toContain("+5.00");
    expect(markup).not.toContain("%");
  });
});
