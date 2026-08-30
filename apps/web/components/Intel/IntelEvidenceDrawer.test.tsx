import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import type { IntelDocState, IntelEvidence, IntelQuote } from "@/lib/intelTypes";
import IntelEvidenceDrawer from "./IntelEvidenceDrawer";

const NOW_MS = 1_700_003_600_000;

function quote(over: Partial<IntelQuote> = {}): IntelQuote {
  return {
    id: 1, itemId: 10, instId: "BTC-USDT-SWAP", roundId: 3, role: "admit", ref: "D1",
    text: "准入时看到的那一段原文", sourceField: "body", docUrl: "https://reuters.com/a",
    docDomain: "reuters.com", docPublishedTs: 1_700_000_000, hasImperative: false,
    verifiedTs: 1_700_000_100, ...over,
  };
}

function doc(over: Partial<IntelDocState> = {}): IntelDocState {
  return {
    url: "https://reuters.com/a", finalUrl: "https://reuters.com/a", domain: "reuters.com",
    title: "t", status: "ok", httpStatus: 200, fetchedTs: 1_700_000_050, bodyChars: 900,
    truncated: false, ...over,
  };
}

function evidence(over: Partial<IntelEvidence> = {}): IntelEvidence {
  return { itemId: 10, quoteMode: "soft", quotes: [quote()], docs: [doc()], ...over };
}

function drawer(props: Partial<React.ComponentProps<typeof IntelEvidenceDrawer>> = {}) {
  return render(
    <IntelEvidenceDrawer id="d" evidence={evidence()} loading={false} error=""
                         blockedDomains={[]} nowMs={NOW_MS} {...props} />);
}

afterEach(cleanup);

describe("IntelEvidenceDrawer", () => {
  it("是一个具名的 region（读屏用户据此知道自己进了哪一块）", () => {
    drawer();
    const region = screen.getByRole("region", { name: "原文引证" });
    expect(region).toHaveAttribute("id", "d");
  });

  it("两组分开渲染：准入的原文与核查的原文不能混在一起", () => {
    drawer({
      evidence: evidence({
        quotes: [quote(), quote({ id: 2, role: "verify", text: "后来出现的那一段原文" })],
      }),
    });
    expect(screen.getByText("准入时的原文依据")).toBeInTheDocument();
    expect(screen.getByText("核查时的原文依据")).toBeInTheDocument();
    expect(screen.getByText("准入时看到的那一段原文")).toBeInTheDocument();
    expect(screen.getByText("后来出现的那一段原文")).toBeInTheDocument();
  });

  it("只有准入引文时不渲染一个空的「核查」组标题", () => {
    drawer();
    expect(screen.queryByText("核查时的原文依据")).toBeNull();
  });

  it("缓存已被淘汰 ⇒ 那句「仍为当时逐字留存」**必须出现**", () => {
    drawer({ evidence: evidence({ docs: [] }) });
    expect(screen.getByTestId("intel-evidence-stale"))
      .toHaveTextContent("原文快照已过期，上方引文仍为当时逐字留存");
  });

  it("缓存还在 ⇒ 不说那句话（说了会让用户以为原文没了）", () => {
    drawer();
    expect(screen.queryByTestId("intel-evidence-stale")).toBeNull();
  });

  it("一条引文都没有 ⇒ 一句诚实的空态（200 + 空数组是正常状态，不是失败）", () => {
    drawer({ evidence: evidence({ quotes: [] }) });
    expect(screen.getByText("这一条没有留存任何原文引证。")).toBeInTheDocument();
  });

  it("首次加载显示骨架屏而不是一个空抽屉", () => {
    drawer({ evidence: null, loading: true });
    expect(screen.queryByTestId("intel-evidence-drawer")).toBeNull();
    expect(screen.getByRole("region", { name: "原文引证" })).toBeInTheDocument();
  });

  it("取数失败说出原因，而不是安静地留一个空抽屉", () => {
    drawer({ evidence: null, loading: false, error: "读取原文引证失败" });
    expect(screen.getByText("读取原文引证失败")).toBeInTheDocument();
  });

  it("拉黑名单透传到每一段引文（外链变灰）", () => {
    drawer({ blockedDomains: ["reuters.com"] });
    expect(screen.queryByRole("link")).toBeNull();
    expect(screen.getByTestId("intel-quote-link-disabled")).toBeInTheDocument();
  });
});
