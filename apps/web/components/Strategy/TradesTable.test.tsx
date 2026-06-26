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

    expect(markup).toContain("SL");
    expect(markup).toContain("TP");
    expect(markup).toContain("2.00% @ 98.00");
    expect(markup).toContain("4.00% @ 104.00");
  });

  it("renders dashes for missing risk fields", () => {
    const markup = renderToStaticMarkup(<TradesTable trades={[BASE_TRADE]} />);

    expect(markup).toContain("<td class=\"py-1.5 pr-2 text-right tabular\" title=\"\">-</td>");
    expect(markup).toContain("<td class=\"py-1.5 text-right tabular\" title=\"\">-</td>");
  });
});
