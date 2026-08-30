import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { IntelItem, IntelQuoteState } from "@/lib/intelTypes";
import IntelEvidenceTrigger from "./IntelEvidenceTrigger";

function item(over: Partial<IntelItem> = {}): IntelItem {
  return {
    id: 1, instId: "BTC-USDT-SWAP", tier: "ultra", category: "macro", headline: "h",
    summary: "s", stance: "neutral", impact: "high", confidence: 0.6, eventTs: 0,
    capturedTs: 0, updatedTs: 0, expiresTs: 0, status: "active", pinned: false,
    userEdited: false, undated: false, isScheduled: false, origin: "agent", sources: [],
    sourceCount: 0, domainCount: 0, dedupKey: "k", supersedesId: null, promoteCount: 0,
    demoteCount: 0, lastCuratedTs: 0, curationNote: "", createdTs: 0,
    citeCount: 0, citeEntryCount: 0, settledCount: 0, winCount: 0, netPnlUsd: 0,
    lastCitedTs: 0, verdict: "", verdictTs: 0, verdictNote: "", verifyCount: 0,
    quoted: false, quoteCount: 0, quoteState: "", scheduleResolvedTs: 0,
    ...over,
  };
}

afterEach(cleanup);

describe("IntelEvidenceTrigger", () => {
  it("有引文 ⇒ 渲染一个带条数的 aria-expanded 触发器", () => {
    render(<IntelEvidenceTrigger item={item({ quoted: true, quoteCount: 2 })} open={false}
                                 controls="drawer-1" onToggle={vi.fn()} />);
    const trigger = screen.getByTestId("intel-evidence-trigger");
    expect(trigger).toHaveTextContent("原文引证 2");
    expect(trigger).toHaveAttribute("aria-expanded", "false");
    expect(trigger).toHaveAttribute("aria-controls", "drawer-1");
  });

  it("aria-expanded 往返", () => {
    const onToggle = vi.fn();
    const { rerender } = render(
      <IntelEvidenceTrigger item={item({ quoted: true, quoteCount: 1 })} open={false}
                            controls="d" onToggle={onToggle} />);
    fireEvent.click(screen.getByTestId("intel-evidence-trigger"));
    expect(onToggle).toHaveBeenCalledTimes(1);
    rerender(<IntelEvidenceTrigger item={item({ quoted: true, quoteCount: 1 })} open
                                   controls="d" onToggle={onToggle} />);
    expect(screen.getByTestId("intel-evidence-trigger"))
      .toHaveAttribute("aria-expanded", "true");
  });

  it("无引文 ⇒ **不渲染空触发器**，而是一枚带原因的极轻标记", () => {
    render(<IntelEvidenceTrigger item={item({ quoteState: "modelOmitted" })} open={false}
                                 controls="d" onToggle={vi.fn()} />);
    expect(screen.queryByTestId("intel-evidence-trigger")).toBeNull();
    const badge = screen.getByTestId("intel-no-evidence");
    expect(badge).toHaveTextContent("无引证");
    expect(badge).toHaveAttribute("title", "抓到了正文，但情报 Agent 本轮没有附引文");
  });

  it.each<[IntelQuoteState, string]>([
    ["legacy", "本条早于原文取证功能，没有留存原文"],
    ["modeOff", "原文引证要求已在设置中关闭"],
    ["noBody", "这几个来源都没能抓到正文"],
    ["verifyFailed", "附了引文，但没有一段能在原文里逐字对上"],
  ])("四种原因各有一句话：%s", (state, title) => {
    render(<IntelEvidenceTrigger item={item({ quoteState: state })} open={false}
                                 controls="d" onToggle={vi.fn()} />);
    expect(screen.getByTestId("intel-no-evidence")).toHaveAttribute("title", title);
  });

  it("既没有引文、原因也是空串（一条老的、说不清的条目）⇒ 整块不渲染", () => {
    const { container } = render(
      <IntelEvidenceTrigger item={item()} open={false} controls="d" onToggle={vi.fn()} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("展开态即便未 hover 也保持可见（收起后才交给 hover 显形）", () => {
    render(<IntelEvidenceTrigger item={item({ quoted: true, quoteCount: 1 })} open
                                 controls="d" onToggle={vi.fn()} />);
    expect(screen.getByTestId("intel-evidence-trigger").className).toContain("opacity-100");
  });
});
