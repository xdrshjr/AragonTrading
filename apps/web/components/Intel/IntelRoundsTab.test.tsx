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
    readerAttempt: 0, readerOk: 0, quotesProposed: 0, quotesVerified: 0,
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

  // ---------------- evidence-grounding §3.3.4：两列新指标 ----------------
  it("正文与引文两列按「命中 / 尝试」渲染", () => {
    render(<IntelRoundsTab rounds={[round({
      readerAttempt: 6, readerOk: 5, quotesProposed: 9, quotesVerified: 7,
    })]} loading={false} nowMs={NOW_MS} />);
    expect(screen.getByTestId("intel-round-reader")).toHaveTextContent("正文 5/6");
    expect(screen.getByTestId("intel-round-quotes")).toHaveTextContent("引文 7/9");
  });

  it("分母 0 ⇒ `—`：「这一轮没抓」与「抓了全失败」是两句不同的话", () => {
    render(<IntelRoundsTab rounds={[round()]} loading={false} nowMs={NOW_MS} />);
    expect(screen.getByTestId("intel-round-reader")).toHaveTextContent("正文 —");
    expect(screen.getByTestId("intel-round-quotes")).toHaveTextContent("引文 —");
  });

  it.each([
    ["quote_bad_ref", "引文指向了本条没声明的来源"],
    ["quote_too_short", "引文过短，不足以作为证据"],
    ["quote_not_found", "引文在原文里找不到（逐字比对未通过）"],
    ["quote_over_cap", "单条引文数超上限，多余的已丢弃"],
    ["quote_span_too_wide", "引文跨度过长，已丢弃"],
    ["no_verified_quote", "没有一条可核对的原文引证（严格模式）"],
    ["quote_orphan_dropped", "引文所属条目已被合并或截断，未落库"],
    ["need_quote", "升长期需要可核对的原文引证（被驳回）"],
    ["refute_without_quote", "证伪未附原文引证，已降为待定"],
  ])("新拒收码 %s 有中文文案，不落到裸码", (code, label) => {
    render(<IntelRoundsTab rounds={[round({ rejects: { [code]: 2 } })]} loading={false}
                           nowMs={NOW_MS} />);
    fireEvent.click(screen.getByRole("button", { name: /篇材料/ }));
    expect(screen.getByText(`${label} × 2`)).toBeInTheDocument();
  });
});
