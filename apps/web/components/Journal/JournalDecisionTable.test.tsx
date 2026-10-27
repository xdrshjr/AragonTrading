import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { DecisionBrief } from "@/lib/journalTypes";
import JournalDecisionTable from "./JournalDecisionTable";

/**
 * 决策轮次表（ai-trade-audit-journal §六 第 1/2 条）。
 *
 * 三态齐全 / `↑↓` 改选中且 `aria-selected` 跟随 / **空态文案随原因变化**——
 * 一句放之四海而皆准的「暂无数据」在审计台上等于什么都没说。
 */
function brief(over: Partial<DecisionBrief> = {}): DecisionBrief {
  return {
    id: 1,
    groupId: "grp-1",
    runId: 7,
    instId: "BTC-USDT-SWAP",
    bar: "15m",
    roundNo: 412,
    ts: 1788000900,
    wallTs: 1788000912,
    trigger: "interval",
    action: "open_long",
    finalAction: "open_long",
    sizeFraction: 0.5,
    confidence: 0.72,
    degradeReason: null,
    reflectionVerdict: "confirm",
    executed: true,
    linkConfidence: "exact",
    ...over,
  };
}

const noop = () => undefined;

function renderTable(props: Partial<React.ComponentProps<typeof JournalDecisionTable>> = {}) {
  return render(
    <JournalDecisionTable
      rows={[brief(), brief({ id: 2, roundNo: 411 }), brief({ id: 3, roundNo: 410 })]}
      selectedId={1}
      onSelect={noop}
      loading={false}
      error=""
      onRetry={noop}
      hasMore={false}
      onLoadMore={noop}
      emptyReason="没有匹配的决策轮次。"
      {...props}
    />,
  );
}

afterEach(cleanup);

describe("JournalDecisionTable — 三态", () => {
  it("载入态出骨架，不出表", () => {
    renderTable({ rows: [], loading: true });
    expect(screen.queryByRole("table")).toBeNull();
  });

  it("错误态出错误文案与重试按钮", () => {
    const onRetry = vi.fn();
    renderTable({ rows: [], error: "JOURNAL_DOWN", onRetry });
    expect(screen.getByText(/JOURNAL_DOWN/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "重试" }));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it("空态说清**为什么**空，而不是一句「暂无数据」", () => {
    renderTable({ rows: [], emptyReason: "交易日志采集当前处于关闭状态，故没有任何留痕。" });
    expect(screen.getByText(/采集当前处于关闭状态/)).toBeTruthy();

    cleanup();
    renderTable({ rows: [], emptyReason: "当前筛选下没有决策轮次。放宽一两个过滤条件再看看。" });
    expect(screen.getByText(/放宽一两个过滤条件/)).toBeTruthy();
  });
});

describe("JournalDecisionTable — 选择与键盘", () => {
  it("选中行带 aria-selected，未选中行不带", () => {
    renderTable();
    const rows = screen.getAllByRole("row").slice(1); // 去掉表头行
    expect(rows[0]).toHaveAttribute("aria-selected", "true");
    expect(rows[1]).toHaveAttribute("aria-selected", "false");
  });

  it("↓ 选下一条、↑ 选上一条（连续上下走是日志浏览的核心动作）", () => {
    const onSelect = vi.fn();
    renderTable({ onSelect });
    const body = screen.getAllByRole("row")[1];
    fireEvent.keyDown(body, { key: "ArrowDown" });
    expect(onSelect).toHaveBeenCalledWith(expect.objectContaining({ id: 2 }));
    onSelect.mockClear();
    fireEvent.keyDown(body, { key: "ArrowUp" });
    expect(onSelect).toHaveBeenCalledWith(expect.objectContaining({ id: 1 }));
  });

  it("点行即选中", () => {
    const onSelect = vi.fn();
    renderTable({ onSelect });
    fireEvent.click(screen.getAllByRole("row")[2]);
    expect(onSelect).toHaveBeenCalledWith(expect.objectContaining({ id: 2 }));
  });

  it("行可 Tab 到（tabIndex=0），键盘用户不被排除在外", () => {
    renderTable();
    for (const row of screen.getAllByRole("row").slice(1)) {
      expect(row).toHaveAttribute("tabindex", "0");
    }
  });
});

describe("JournalDecisionTable — 行内语义", () => {
  it("模型动作与最终动作不同时并排显示并标注被改写", () => {
    renderTable({ rows: [brief({ action: "open_short", finalAction: "hold" })] });
    expect(screen.getByText("开空")).toBeTruthy();
    expect(screen.getByText("观望")).toBeTruthy();
    expect(screen.getByText("（被改写）")).toBeTruthy();
  });

  it("未成交轮明说「未下单」，不留空白", () => {
    renderTable({ rows: [brief({ executed: false, linkConfidence: "none" })] });
    expect(screen.getByText("未下单")).toBeTruthy();
  });

  it("关联置信度是形状 + 文案，不靠颜色单独承载语义", () => {
    renderTable({ rows: [brief({ linkConfidence: "heuristic" })] });
    expect(screen.getByText(/≈/)).toBeTruthy();
    expect(screen.getByText(/启发式关联/)).toBeTruthy();
  });

  it("分页「加载更多」是真按钮（不是无限滚动——审计需要可预期的位置）", () => {
    const onLoadMore = vi.fn();
    renderTable({ hasMore: true, onLoadMore });
    fireEvent.click(screen.getByRole("button", { name: "加载更多" }));
    expect(onLoadMore).toHaveBeenCalledTimes(1);
  });

  it("没有更多时如实说「已到末尾」，不留一个点不动的按钮", () => {
    renderTable({ hasMore: false });
    expect(screen.queryByRole("button", { name: "加载更多" })).toBeNull();
    expect(screen.getByText(/已到本次筛选的末尾/)).toBeTruthy();
  });
});
