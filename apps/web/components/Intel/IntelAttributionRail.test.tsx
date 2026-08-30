import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import type { IntelItem } from "@/lib/intelTypes";
import IntelAttributionRail from "./IntelAttributionRail";

function item(over: Partial<IntelItem> = {}): IntelItem {
  return {
    id: 1, instId: "BTC-USDT-SWAP", tier: "ultra", category: "risk", headline: "h",
    summary: "s", stance: "neutral", impact: "high", confidence: 0.6, eventTs: 0,
    capturedTs: 0, updatedTs: 0, expiresTs: 0, status: "active", pinned: false,
    userEdited: false, undated: false, isScheduled: false, origin: "agent", sources: [],
    sourceCount: 0, domainCount: 0, dedupKey: "k", supersedesId: null, promoteCount: 0,
    demoteCount: 0, lastCuratedTs: 0, curationNote: "", createdTs: 0,
    citeCount: 0, citeEntryCount: 0, settledCount: 0, winCount: 0, netPnlUsd: 0,
    lastCitedTs: 0, verdict: "", verdictTs: 0, verdictNote: "", verifyCount: 0,
    ...over,
  };
}

afterEach(cleanup);

describe("IntelAttributionRail", () => {
  it("零引用 ⇒ 整行不渲染（一条「引用 0 次」的灰字是纯噪音）", () => {
    const { container } = render(<IntelAttributionRail item={item()} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("被引用过就渲染，并写出入场与已结算两个计数", () => {
    render(<IntelAttributionRail item={item({
      citeCount: 12, citeEntryCount: 5, settledCount: 4, winCount: 3, netPnlUsd: 18.4,
    })} />);
    const rail = screen.getByTestId("intel-attribution-rail");
    expect(rail).toHaveTextContent("引用 12");
    expect(rail).toHaveTextContent("入场 5");
    expect(rail).toHaveTextContent("已结算 4");
  });

  it("负净额用 --down 配色", () => {
    render(<IntelAttributionRail item={item({
      citeCount: 2, citeEntryCount: 1, settledCount: 1, winCount: 0, netPnlUsd: -3.5,
    })} />);
    const text = screen.getByText(/关联净额/);
    expect(text).toHaveStyle({ color: "var(--down)" });
  });

  it("正净额用 --up 配色", () => {
    render(<IntelAttributionRail item={item({
      citeCount: 2, citeEntryCount: 1, settledCount: 1, winCount: 1, netPnlUsd: 3.5,
    })} />);
    expect(screen.getByText(/关联净额/)).toHaveStyle({ color: "var(--up)" });
  });

  it("尚未结算 ⇒ 不着盈亏色（0.00 被画成绿色会被读成一次小胜）", () => {
    render(<IntelAttributionRail item={item({ citeCount: 3, citeEntryCount: 1 })} />);
    expect(screen.getByTestId("intel-attribution-rail")).not.toHaveTextContent("关联净额");
  });

  it("title 给出完整口径，并明说这是共现而不是因果", () => {
    render(<IntelAttributionRail item={item({
      citeCount: 12, citeEntryCount: 5, settledCount: 4, winCount: 3, netPnlUsd: 18.4,
    })} />);
    const title = screen.getByTestId("intel-attribution-rail").getAttribute("title") ?? "";
    expect(title).toContain("入场引用 5 次中已结算 4 次");
    expect(title).toContain("胜 3 负 1");
    expect(title).toContain("不是因果归因");
  });
});
