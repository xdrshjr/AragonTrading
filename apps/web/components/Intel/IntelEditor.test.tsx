import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import IntelEditor from "./IntelEditor";

afterEach(cleanup);

function open(onSubmit = vi.fn(async () => {})) {
  render(<IntelEditor open item={null} onClose={() => {}} onSubmit={onSubmit} />);
  return onSubmit;
}

describe("IntelEditor", () => {
  it("必填未填时保存按钮禁用（不把用户的时间浪费在一次必然 422 的往返上）", () => {
    open();
    expect(screen.getByRole("button", { name: "保存" })).toBeDisabled();
  });

  it("字数计数在输入时就说清楚上限", () => {
    open();
    fireEvent.change(screen.getByLabelText("标题"), { target: { value: "一二三" } });
    expect(screen.getByText("3 / 40")).toBeInTheDocument();
  });

  it("标题超上限即报错并挡住保存（后端是拒收不截断，前端必须先说）", () => {
    open();
    fireEvent.change(screen.getByLabelText("标题"), { target: { value: "字".repeat(41) } });
    fireEvent.change(screen.getByLabelText(/摘要/), { target: { value: "有效摘要" } });
    expect(screen.getByText(/标题最多 40 字（当前 41）/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "保存" })).toBeDisabled();
  });

  it("来源行可增可删", () => {
    open();
    expect(screen.getAllByLabelText(/来源链接/)).toHaveLength(1);
    fireEvent.click(screen.getByRole("button", { name: "+ 添加来源" }));
    expect(screen.getAllByLabelText(/来源链接/)).toHaveLength(2);
    fireEvent.click(screen.getAllByRole("button", { name: /删除来源/ })[0]);
    expect(screen.getAllByLabelText(/来源链接/)).toHaveLength(1);
  });

  it("非 http(s) 来源被挡下并给出人话", () => {
    open();
    fireEvent.change(screen.getByLabelText("标题"), { target: { value: "标题" } });
    fireEvent.change(screen.getByLabelText(/摘要/), { target: { value: "摘要" } });
    fireEvent.change(screen.getByLabelText("来源链接 1"), {
      target: { value: "javascript:alert(1)" },
    });
    expect(screen.getByText("来源链接必须是 http(s) 绝对地址")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "保存" })).toBeDisabled();
  });

  it("提交时把空的来源行剔掉，并 trim 掉首尾空白", async () => {
    const onSubmit = open();
    fireEvent.change(screen.getByLabelText("标题"), { target: { value: "  标题  " } });
    fireEvent.change(screen.getByLabelText(/摘要/), { target: { value: " 摘要正文 " } });
    fireEvent.click(screen.getByRole("button", { name: "+ 添加来源" }));
    fireEvent.change(screen.getByLabelText("来源链接 1"), {
      target: { value: "https://a.com/x" },
    });
    fireEvent.click(screen.getByRole("button", { name: "保存" }));
    await waitFor(() =>
      expect(onSubmit).toHaveBeenCalledWith(expect.objectContaining({
        headline: "标题",
        summary: "摘要正文",
        sources: [{ url: "https://a.com/x", title: "" }],
      })));
  });

  it("明说「用户编辑」的语义：它改变用户对「我改这一条会不会白改」的预期", () => {
    open();
    expect(screen.getByText(/自动整理与自动清理都不会动它/)).toBeInTheDocument();
  });

  it("编辑模式预填既有值", () => {
    const item = {
      id: 9, instId: "BTC-USDT-SWAP", tier: "mid" as const, category: "macro" as const,
      headline: "原标题", summary: "原摘要", stance: "bullish" as const,
      impact: "low" as const, confidence: 0.5, eventTs: 0, capturedTs: 0, updatedTs: 0,
      expiresTs: 0, status: "active" as const, pinned: false, userEdited: true,
      undated: true, isScheduled: false, origin: "user" as const,
      sources: [{ url: "https://a.com/x", title: "T", publisher: "a.com", domain: "a.com", publishedTs: 0 }],
      sourceCount: 1, domainCount: 1, dedupKey: "k", supersedesId: null, promoteCount: 0,
      demoteCount: 0, lastCuratedTs: 0, curationNote: "", createdTs: 0,
      citeCount: 0, citeEntryCount: 0, settledCount: 0, winCount: 0, netPnlUsd: 0,
      lastCitedTs: 0, verdict: "" as const, verdictTs: 0, verdictNote: "", verifyCount: 0,
      quoted: false, quoteCount: 0, quoteState: "" as const, scheduleResolvedTs: 0,
    };
    render(<IntelEditor open item={item} onClose={() => {}} onSubmit={vi.fn(async () => {})} />);
    expect(screen.getByText("编辑情报")).toBeInTheDocument();
    expect(screen.getByLabelText("标题")).toHaveValue("原标题");
    expect(screen.getByLabelText("来源链接 1")).toHaveValue("https://a.com/x");
  });
});
