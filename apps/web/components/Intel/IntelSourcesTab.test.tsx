import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { IntelFeedbackRow, IntelSourceRow } from "@/lib/intelTypes";
import IntelSourcesTab from "./IntelSourcesTab";

const INST = "BTC-USDT-SWAP";
const NOW_S = Math.round(Date.now() / 1000);

let sourceRows: IntelSourceRow[];
let feedbackRows: IntelFeedbackRow[];
const sourcesMock = vi.fn(async () => ({ rows: sourceRows }));
const feedbackMock = vi.fn(async () => ({ rows: feedbackRows }));
const writeMock = vi.fn(async () => ({ ok: true, archived: 3 }));
const previewMock = vi.fn(async () => ({ affected: 3 }));
const clearMock = vi.fn(async () => undefined);

vi.mock("@/lib/intelApi", () => ({
  intelApi: {
    sources: (...a: unknown[]) => sourcesMock(...(a as [])),
    feedback: (...a: unknown[]) => feedbackMock(...(a as [])),
    writeFeedback: (...a: unknown[]) => writeMock(...(a as [])),
    previewFeedback: (...a: unknown[]) => previewMock(...(a as [])),
    clearFeedback: (...a: unknown[]) => clearMock(...(a as [])),
  },
}));

beforeEach(() => {
  sourceRows = [
    { domain: "coinmarketwatch.example", itemCount: 3, lastTs: NOW_S - 3600, state: "",
      stateInstId: "", confirmed: 0, refuted: 0, unresolved: 0, accuracy: null },
    // 这一行的取舍是**全局**的（`stateInstId: ""`），而列表本身是按标的取的 —— 撤销必须按
    // 记录自己的作用域发，否则删不中那一行。
    { domain: "coindesk.com", itemCount: 5, lastTs: NOW_S - 7200, state: "trusted",
      stateInstId: "", confirmed: 0, refuted: 0, unresolved: 0, accuracy: null },
  ];
  feedbackRows = [
    { id: 1, scope: "domain", key: "coindesk.com", action: "trust", instId: "",
      createdTs: NOW_S },
  ];
  [sourcesMock, feedbackMock, writeMock, previewMock, clearMock].forEach((m) => m.mockClear());
});
afterEach(cleanup);

function renderTab() {
  return render(<IntelSourcesTab instId={INST} onChanged={vi.fn()} />);
}

describe("IntelSourcesTab", () => {
  it("列出库存域名与它们的当前状态", async () => {
    renderTab();
    expect(await screen.findByText(/coinmarketwatch.example/)).toBeInTheDocument();
    // 「更信任」既是徽章文案也是动作按钮 —— 这里断言的是**徽章**（状态），不是那颗按钮。
    const badge = screen.getAllByText("更信任").find((el) => el.tagName === "SPAN");
    expect(badge).toBeDefined();
  });

  it("「更信任」是可逆动作，一次点击即写入（不走确认框）", async () => {
    renderTab();
    fireEvent.click(await screen.findByRole("button", { name: "更信任" }));

    await waitFor(() => expect(writeMock).toHaveBeenCalledWith({
      key: "coinmarketwatch.example", action: "trust", instId: INST }));
  });

  it("「不再采纳」走确认框，且确认框显示受影响条数", async () => {
    renderTab();
    fireEvent.click(await screen.findByRole("button", { name: "不再采纳" }));

    expect(await screen.findByText(/不再采纳 coinmarketwatch.example？/)).toBeInTheDocument();
    await waitFor(() => expect(previewMock).toHaveBeenCalled());
    expect(await screen.findByText(/已入库的 3 条情报/)).toBeInTheDocument();
    expect(screen.getByText(/可在「已归档」中恢复/)).toBeInTheDocument();
    expect(writeMock).not.toHaveBeenCalled();
  });

  it("确认之后回执里的归档条数进提示（用户必须知道后果真的发生了）", async () => {
    renderTab();
    fireEvent.click(await screen.findByRole("button", { name: "不再采纳" }));
    fireEvent.click(await screen.findByRole("button", { name: "不再采纳此源" }));

    await waitFor(() => expect(writeMock).toHaveBeenCalledWith({
      key: "coinmarketwatch.example", action: "block", instId: INST }));
    // ★ 回归：这句提示曾被收尾时无条件写入的那句通用文案在同一个 tick 里覆盖掉，于是确认框
    // 承诺过的「有多少条会被归档」在动作真的发生之后反而看不见了。
    expect(await screen.findByText(/3 条情报已归档，可在「已归档」中恢复/)).toBeInTheDocument();
  });

  it("撤销一条**全局**取舍时按它自己的作用域发（硬写本标的会删不中且不报错）", async () => {
    renderTab();
    // 上半区那一行的状态来自一条全局记录；它的「撤销」必须发空 instId。
    const undo = await screen.findAllByRole("button", { name: "撤销" });
    fireEvent.click(undo[0]);

    await waitFor(() => expect(clearMock).toHaveBeenCalledWith("coindesk.com", ""));
  });

  it("全局取舍在徽章上标出作用域（两种记录撤销之后的后果不同）", async () => {
    renderTab();
    expect(await screen.findByText(/（全局）/)).toBeInTheDocument();
  });

  it("勾选「对所有标的生效」⇒ 请求不带 instId（作用域由用户显式选择）", async () => {
    renderTab();
    fireEvent.click(await screen.findByRole("button", { name: "不再采纳" }));
    fireEvent.click(await screen.findByRole("checkbox"));
    fireEvent.click(screen.getByRole("button", { name: "不再采纳此源" }));

    await waitFor(() => expect(writeMock).toHaveBeenCalledWith({
      key: "coinmarketwatch.example", action: "block", instId: "" }));
  });

  it("撤销取舍时明说「已归档的不会自动复活」", async () => {
    renderTab();
    const undo = await screen.findAllByRole("button", { name: "撤销" });
    fireEvent.click(undo[0]);

    await waitFor(() => expect(clearMock).toHaveBeenCalled());
    expect(await screen.findByText(/已归档的条目不会自动复活/)).toBeInTheDocument();
  });

  it("黑白名单区把全局与单标的分开说清楚", async () => {
    renderTab();
    expect(await screen.findByText("黑白名单")).toBeInTheDocument();
    expect(screen.getByText(/全局生效/)).toBeInTheDocument();
  });
});

describe("信誉三列（ai-trade-intel-actionability 轴 B）", () => {
  it("每行都带证实 / 证伪 / 待定与准确率——拉黑决策就在这一页上做", async () => {
    sourceRows = [
      { domain: "coindesk.com", itemCount: 9, lastTs: NOW_S - 60, state: "",
        stateInstId: "", confirmed: 8, refuted: 1, unresolved: 2, accuracy: 0.888 },
    ];
    render(<IntelSourcesTab instId={INST} onChanged={() => undefined} />);
    const row = await screen.findByTestId("intel-source-reputation");
    expect(row.textContent).toContain("证实 8");
    expect(row.textContent).toContain("证伪 1");
    expect(row.textContent).toContain("待定 2");
    expect(row.textContent).toContain("准确率 89%");
  });

  it("样本不足时写「样本不足」而不是一个用两条样本算出来的 100%", async () => {
    sourceRows = [
      { domain: "farm.example", itemCount: 2, lastTs: NOW_S - 60, state: "",
        stateInstId: "", confirmed: 2, refuted: 0, unresolved: 0, accuracy: null },
    ];
    render(<IntelSourcesTab instId={INST} onChanged={() => undefined} />);
    expect((await screen.findByTestId("intel-source-reputation")).textContent)
      .toContain("准确率 样本不足");
  });

  it("排序开关可切到按准确率，样本不足的行恒排最后（不与真实读数混排）", async () => {
    sourceRows = [
      { domain: "unknown.example", itemCount: 9, lastTs: 1, state: "", stateInstId: "",
        confirmed: 0, refuted: 0, unresolved: 0, accuracy: null },
      { domain: "good.example", itemCount: 2, lastTs: 1, state: "", stateInstId: "",
        confirmed: 9, refuted: 0, unresolved: 0, accuracy: 1 },
    ];
    render(<IntelSourcesTab instId={INST} onChanged={() => undefined} />);
    const toggle = await screen.findByTestId("intel-sources-sort");
    expect(toggle).toHaveTextContent("按条数排序");
    fireEvent.click(toggle);
    expect(toggle).toHaveTextContent("按准确率排序");
    const rows = screen.getAllByTestId("intel-source-reputation")
      .map((node) => node.parentElement?.textContent ?? "");
    expect(rows[0]).toContain("good.example");
    expect(rows[1]).toContain("unknown.example");
  });
});
