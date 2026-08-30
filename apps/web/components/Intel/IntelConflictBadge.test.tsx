import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import type { IntelConflict, IntelItem } from "@/lib/intelTypes";
import IntelConflictBadge from "./IntelConflictBadge";

function item(over: Partial<IntelItem> = {}): IntelItem {
  return {
    id: 4, instId: "BTC-USDT-SWAP", tier: "ultra", category: "market",
    headline: "ETF 资金连续第 5 日净流入", summary: "s", stance: "bullish", impact: "high",
    confidence: 0.8, eventTs: 1, capturedTs: 1, updatedTs: 1, expiresTs: 2,
    status: "active", pinned: false, userEdited: false, undated: false, isScheduled: false,
    origin: "agent", sources: [], sourceCount: 0, domainCount: 2, dedupKey: "k",
    supersedesId: null, promoteCount: 0, demoteCount: 0, lastCuratedTs: 0,
    curationNote: "", createdTs: 1, citeCount: 0, citeEntryCount: 0, settledCount: 0,
    winCount: 0, netPnlUsd: 0, lastCitedTs: 0, verdict: "", verdictTs: 0, verdictNote: "",
    verifyCount: 0, quoted: false, quoteCount: 0, quoteState: "", scheduleResolvedTs: 0,
    ...over,
  };
}

const pair: IntelConflict = {
  aId: 4, bId: 9, overlap: 0.62, windowH: 24, category: "market", openRounds: 3,
};

afterEach(cleanup);

describe("IntelConflictBadge", () => {
  it("指向对方的 id，并带上悬置轮数", () => {
    render(<IntelConflictBadge item={item()} pair={pair} />);
    const badge = screen.getByTestId("intel-conflict-badge");
    expect(badge).toHaveTextContent("与 #9 矛盾");
    expect(badge).toHaveTextContent("悬置 3 轮");
  });

  it("从对方那一端看过来时指向的是本端", () => {
    render(<IntelConflictBadge item={item({ id: 9 })} pair={pair} />);
    expect(screen.getByTestId("intel-conflict-badge")).toHaveTextContent("与 #4 矛盾");
  });

  it("悬浮说明带对方标题，且明说系统不会自动删除任何一方", () => {
    render(<IntelConflictBadge item={item()} pair={pair}
      partner={item({ id: 9, headline: "ETF 单日净流出创年内新高" })} />);
    const title = screen.getByTestId("intel-conflict-badge").getAttribute("title") ?? "";
    expect(title).toContain("ETF 单日净流出创年内新高");
    expect(title).toContain("不会自动删除");
    // 这段文案原样进 `title=`：Markdown 星号会被显示成两颗字面星号。
    expect(title).not.toContain("**");
  });

  it("没有矛盾对 ⇒ **整枚徽标不渲染**（不是渲染一个「无冲突」）", () => {
    const { container } = render(<IntelConflictBadge item={item()} />);
    expect(container.firstChild).toBeNull();
  });

  it("悬置 0 轮时不显示那半句（刚被裁决过的对不该再喊「悬置 0 轮」）", () => {
    render(<IntelConflictBadge item={item()} pair={{ ...pair, openRounds: 0 }} />);
    expect(screen.getByTestId("intel-conflict-badge")).not.toHaveTextContent("悬置");
  });
});
