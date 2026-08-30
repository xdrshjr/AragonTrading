import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { IntelQuote } from "@/lib/intelTypes";
import IntelQuoteBlock from "./IntelQuoteBlock";

const NOW_MS = 1_700_003_600_000;

function quote(over: Partial<IntelQuote> = {}): IntelQuote {
  return {
    id: 1, itemId: 10, instId: "BTC-USDT-SWAP", roundId: 3, role: "admit", ref: "D1",
    text: "The July core CPI print came in at 3.1% year over year",
    sourceField: "body", docUrl: "https://reuters.com/a", docDomain: "reuters.com",
    docPublishedTs: 1_700_000_000, hasImperative: false, verifiedTs: 1_700_000_100,
    ...over,
  };
}

afterEach(cleanup);

describe("IntelQuoteBlock", () => {
  it("是一个 <blockquote cite>，且以纯文本渲染原文", () => {
    render(<IntelQuoteBlock quote={quote()} stale={false} blocked={false} nowMs={NOW_MS} />);
    const block = screen.getByTestId("intel-quote-block").querySelector("blockquote")!;
    expect(block).toHaveAttribute("cite", "https://reuters.com/a");
    expect(block).toHaveTextContent("core CPI print came in at 3.1%");
  });

  it("等宽字体 + 左侧强调竖线：这是别人写的字，不是我们写的字", () => {
    render(<IntelQuoteBlock quote={quote()} stale={false} blocked={false} nowMs={NOW_MS} />);
    const block = screen.getByTestId("intel-quote-block").querySelector("blockquote")!;
    expect(block.className).toContain("font-mono");
    expect(block.className).toContain("border-l-2");
  });

  it("外链带 rel=\"noopener noreferrer\"", () => {
    render(<IntelQuoteBlock quote={quote()} stale={false} blocked={false} nowMs={NOW_MS} />);
    const link = screen.getByRole("link", { name: /原文/ });
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
  });

  it("域名已被拉黑 ⇒ 链接变灰不可点并说明原因（仍然可点的「已屏蔽来源」自相矛盾）", () => {
    render(<IntelQuoteBlock quote={quote()} stale={false} blocked nowMs={NOW_MS} />);
    expect(screen.queryByRole("link")).toBeNull();
    expect(screen.getByTestId("intel-quote-link-disabled"))
      .toHaveAttribute("title", "此源已被你屏蔽");
  });

  it("非 http(s) 的 URL 同样不渲染成链接（纵深防御）", () => {
    render(<IntelQuoteBlock quote={quote({ docUrl: "javascript:alert(1)" })} stale={false}
                            blocked={false} nowMs={NOW_MS} />);
    expect(screen.queryByRole("link")).toBeNull();
  });

  it("原文含劝告性措辞 ⇒ 中性标记，**不是**拒收", () => {
    render(<IntelQuoteBlock quote={quote({ hasImperative: true })} stale={false}
                            blocked={false} nowMs={NOW_MS} />);
    expect(screen.getByTestId("intel-quote-imperative"))
      .toHaveTextContent("原文含劝告性措辞，本系统不作任何建议");
  });

  it("快照过期时在出处行上说明（引文本身仍在）", () => {
    render(<IntelQuoteBlock quote={quote()} stale blocked={false} nowMs={NOW_MS} />);
    expect(screen.getByTestId("intel-quote-stale")).toHaveTextContent("原文快照已过期");
  });

  it("拉黑入口就在引文旁边：识破一条烂证据的那一刻正是最想拉黑这个源的那一刻", () => {
    const onBlock = vi.fn();
    render(<IntelQuoteBlock quote={quote()} stale={false} blocked={false} nowMs={NOW_MS}
                            onBlockSource={onBlock} />);
    fireEvent.click(screen.getByRole("button", { name: "不再采纳 reuters.com" }));
    expect(onBlock).toHaveBeenCalledWith("reuters.com");
  });

  it("缺省不渲染拉黑入口（只读场景）", () => {
    render(<IntelQuoteBlock quote={quote()} stale={false} blocked={false} nowMs={NOW_MS} />);
    expect(screen.queryByRole("button")).toBeNull();
  });
});
