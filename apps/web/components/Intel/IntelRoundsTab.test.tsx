import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import type { IntelRound } from "@/lib/intelTypes";
import IntelRoundsTab from "./IntelRoundsTab";

const NOW_MS = 1_800_000_000_000;
const NOW_S = NOW_MS / 1000;

function round(over: Partial<IntelRound> = {}): IntelRound {
  return {
    id: 1, instId: "BTC-USDT-SWAP", kind: "collect", groupId: "g", startedTs: NOW_S - 600,
    finishedTs: NOW_S - 590, status: "ok", searchProvider: "tavily", queries: ["BTC news"],
    docCount: 16, domainCount: 6, accepted: 2, rejected: 1, merged: 0,
    rejects: { bad_ref: 1 }, actions: {}, inputTokens: 100, outputTokens: 20,
    degradeCode: "", errorText: "",
    ...over,
  };
}

afterEach(cleanup);

describe("IntelRoundsTab · 核查轮", () => {
  it("核查轮的徽章是「核查」，与采集 / 整理并列", () => {
    render(<IntelRoundsTab rounds={[round({ kind: "verify", actions: { confirmed: 1 } })]}
      loading={false} nowMs={NOW_MS} />);
    expect(screen.getByText("核查")).toBeInTheDocument();
  });

  it("行摘要说的是三个判定，而不是「执行 N 个动作」", () => {
    render(<IntelRoundsTab
      rounds={[round({ kind: "verify",
        actions: { confirmed: 1, refuted: 1, unresolved: 1 } })]}
      loading={false} nowMs={NOW_MS} />);
    expect(screen.getByText("核查 3 条 → 证实 1 · 证伪 1 · 待定 1")).toBeInTheDocument();
  });

  it("没有待核查条目时说的是它自己的话", () => {
    render(<IntelRoundsTab rounds={[round({ kind: "verify", actions: { verify_skipped: 1 } })]}
      loading={false} nowMs={NOW_MS} />);
    expect(screen.getByText("本轮没有待核查条目")).toBeInTheDocument();
  });

  it("`verdict_no_ref > 0` ⇒ 展开后那一行灰字出现（模型不能空口断言的可见证据）", () => {
    render(<IntelRoundsTab
      rounds={[round({ kind: "verify",
        actions: { refuted: 1, unresolved: 1, verdict_no_ref: 1 } })]}
      loading={false} nowMs={NOW_MS} />);
    fireEvent.click(screen.getByRole("button"));

    expect(screen.getByTestId("intel-verdict-no-ref"))
      .toHaveTextContent("1 条判定因未指出依据被降为待定");
  });

  it("`verdict_no_ref` 为 0 ⇒ 那一行整块不渲染", () => {
    render(<IntelRoundsTab rounds={[round({ kind: "verify", actions: { confirmed: 1 } })]}
      loading={false} nowMs={NOW_MS} />);
    fireEvent.click(screen.getByRole("button"));

    expect(screen.queryByTestId("intel-verdict-no-ref")).toBeNull();
  });

  it("四个核查降级码各有中文，不显示裸码", () => {
    const codes = ["verify_no_provider", "verify_search_failed", "verify_no_corpus",
      "verify_llm_failed"];
    codes.forEach((code, index) => {
      const view = render(<IntelRoundsTab
        rounds={[round({ id: index + 1, kind: "verify", status: "degraded",
          degradeCode: code, actions: {} })]}
        loading={false} nowMs={NOW_MS} />);
      fireEvent.click(screen.getByRole("button"));
      const text = screen.getByText(/^降级：/).textContent ?? "";
      expect(text).not.toContain(code);
      view.unmount();
    });
  });

  it("采集轮的行摘要一个字都没变（零回归）", () => {
    render(<IntelRoundsTab rounds={[round()]} loading={false} nowMs={NOW_MS} />);
    expect(screen.getByText(
      "16 篇材料 / 6 个来源 → 采纳 2、合并 0、拒收 1")).toBeInTheDocument();
  });

  it("整理轮的新码有中文（存量扫除 / 引用裁剪 / 举证责任）", () => {
    render(<IntelRoundsTab
      rounds={[round({ kind: "curate", rejects: {},
        actions: { blocked_sweep: 2, citations_pruned: 9, archive_profitable: 1 } })]}
      loading={false} nowMs={NOW_MS} />);
    fireEvent.click(screen.getByRole("button"));

    expect(screen.getByText(/来源已拉黑，存量归档 × 2/)).toBeInTheDocument();
    expect(screen.getByText(/引用明细到期裁剪 × 9/)).toBeInTheDocument();
    expect(screen.getByText(/理由不足（被驳回） × 1/)).toBeInTheDocument();
  });
});
