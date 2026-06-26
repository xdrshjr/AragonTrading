import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import type { TradeMarkerEvent } from "@/lib/types";
import TradeTooltip from "./TradeTooltip";

function eventWithRisk(overrides: Partial<TradeMarkerEvent["trade"]> = {}): TradeMarkerEvent {
  return {
    id: "event-1",
    kind: "entry-long",
    direction: "long",
    trade: {
      ts: 1609459200,
      side: "long",
      price: 100,
      qty: 0.5,
      fee: 0.1,
      pnl: 0,
      positionAfter: 0.5,
      reason: "entry",
      ...overrides,
    },
  };
}

describe("TradeTooltip risk details", () => {
  it("shows stop-loss and take-profit risk values for marker events", () => {
    const markup = renderToStaticMarkup(
      <TradeTooltip
        events={[
          eventWithRisk({
            stopLossPct: 0.02,
            stopLossPrice: 98,
            takeProfitPct: 0.04,
            takeProfitPrice: 104,
            maxHoldBars: 12,
          }),
        ]}
        x={0}
        y={0}
      />,
    );

    expect(markup).toContain("回测成交");
    expect(markup).toContain("止损(SL) 2.00% @ 98.00");
    expect(markup).toContain("止盈(TP) 4.00% @ 104.00");
    expect(markup).toContain("最长持有 12 根K线");
  });

  it("renders without risk values when fields are absent", () => {
    const markup = renderToStaticMarkup(<TradeTooltip events={[eventWithRisk()]} x={0} y={0} />);

    expect(markup).toContain("止损(SL) -");
    expect(markup).toContain("止盈(TP) -");
    expect(markup).not.toContain("最长持有");
  });
});
