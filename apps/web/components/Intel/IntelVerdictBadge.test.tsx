import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { IntelItem, IntelVerdict } from "@/lib/intelTypes";
import IntelVerdictBadge from "./IntelVerdictBadge";

const NOW_S = Math.round(Date.now() / 1000);

function item(over: Partial<IntelItem> = {}): IntelItem {
  return {
    id: 1, instId: "BTC-USDT-SWAP", tier: "mid", category: "project", headline: "h",
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

describe("IntelVerdictBadge", () => {
  it("尚未核查过（`verdict === \"\"`）⇒ 整个徽章不渲染", () => {
    const { container } = render(
      <IntelVerdictBadge item={item()} onClearVerdict={vi.fn()} />);
    expect(container).toBeEmptyDOMElement();
  });

  it.each<[IntelVerdict, string, string]>([
    ["confirmed", "已证实", "var(--ok)"],
    ["refuted", "已证伪", "var(--danger)"],
    ["unresolved", "待定", "var(--muted)"],
  ])("%s ⇒ 「%s」并用 %s", (verdict, label, tone) => {
    render(<IntelVerdictBadge item={item({ verdict })} onClearVerdict={vi.fn()} />);
    const badge = screen.getByTestId("intel-verdict-badge");
    expect(badge).toHaveTextContent(label);
    expect(badge).toHaveStyle({ color: tone });
  });

  it("点开给出核查理由全文与已核查次数", () => {
    render(<IntelVerdictBadge
      item={item({ verdict: "refuted", verdictNote: "基金会已宣布升级无限期推迟",
        verdictTs: NOW_S - 3600, verifyCount: 2 })}
      onClearVerdict={vi.fn()} />);
    fireEvent.click(screen.getByTestId("intel-verdict-badge"));

    const popover = screen.getByTestId("intel-verdict-popover");
    expect(popover).toHaveTextContent("基金会已宣布升级无限期推迟");
    expect(popover).toHaveTextContent("已核查 2 次");
  });

  it("被证伪时「我不同意」明说它会恢复注入（撤销出口必须说清后果）", () => {
    render(<IntelVerdictBadge item={item({ verdict: "refuted" })} onClearVerdict={vi.fn()} />);
    fireEvent.click(screen.getByTestId("intel-verdict-badge"));
    expect(screen.getByRole("button", { name: "我不同意，恢复注入" })).toBeInTheDocument();
  });

  it("「我不同意」触发 clearVerdict 回调", () => {
    const onClearVerdict = vi.fn();
    const target = item({ verdict: "refuted" });
    render(<IntelVerdictBadge item={target} onClearVerdict={onClearVerdict} />);
    fireEvent.click(screen.getByTestId("intel-verdict-badge"));
    fireEvent.click(screen.getByRole("button", { name: "我不同意，恢复注入" }));

    expect(onClearVerdict).toHaveBeenCalledWith(target);
  });

  it("「重新核查这一条」只在给了回调时出现", () => {
    const onVerifyNow = vi.fn();
    render(<IntelVerdictBadge item={item({ verdict: "unresolved" })}
      onClearVerdict={vi.fn()} onVerifyNow={onVerifyNow} />);
    fireEvent.click(screen.getByTestId("intel-verdict-badge"));
    fireEvent.click(screen.getByRole("button", { name: "重新核查这一条" }));

    expect(onVerifyNow).toHaveBeenCalledTimes(1);
  });

  it("`verdictNote` 以纯文本渲染（模型写的自由文本绝不当 HTML 解释）", () => {
    render(<IntelVerdictBadge
      item={item({ verdict: "refuted", verdictNote: "<img src=x onerror=alert(1)>" })}
      onClearVerdict={vi.fn()} />);
    fireEvent.click(screen.getByTestId("intel-verdict-badge"));

    const popover = screen.getByTestId("intel-verdict-popover");
    expect(popover).toHaveTextContent("<img src=x onerror=alert(1)>");
    expect(popover.querySelector("img")).toBeNull();
  });

  it("徽章的 aria-label 带判定与时刻（只有视觉颜色的徽章对读屏用户等于不存在）", () => {
    render(<IntelVerdictBadge item={item({ verdict: "confirmed", verdictTs: NOW_S - 7200 })}
      onClearVerdict={vi.fn()} />);
    expect(screen.getByTestId("intel-verdict-badge").getAttribute("aria-label"))
      .toContain("已证实");
  });
});
