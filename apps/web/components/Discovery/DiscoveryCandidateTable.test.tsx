import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { DiscoveryCandidateRow } from "@/lib/discoveryTypes";
import DiscoveryCandidates from "./DiscoveryCandidateTable";

const listMock = vi.fn();
const promoteMock = vi.fn();

vi.mock("@/lib/discoveryApi", () => ({
  discoveryApi: {
    listCandidates: (...args: unknown[]) => listMock(...args),
    promote: (...args: unknown[]) => promoteMock(...args),
    getCandidate: vi.fn().mockResolvedValue(null),
  },
}));

const contextValue = { activeRunId: 1 as number | null, goSection: vi.fn() };

vi.mock("./DiscoveryLab", () => ({
  useDiscovery: () => contextValue,
}));

vi.mock("./DiscoveryCandidateDetail", () => ({
  default: ({ candidateId }: { candidateId: number | null }) =>
    candidateId ? <div data-testid="detail">详情 {candidateId}</div> : null,
}));

function row(over: Partial<DiscoveryCandidateRow> = {}): DiscoveryCandidateRow {
  return {
    id: 11,
    runId: 1,
    iterationIdx: 3,
    kind: "factor",
    genomeHash: "abc",
    exprText: "sub(ts_mean(overnight,20),delta(amplitude,5))",
    describe: "20 根跨根跳空均值 减去 5 根振幅差分",
    mechanism: "gap_premium",
    source: "mutate",
    verdict: "kept",
    rejectReason: "",
    demoted: false,
    trialsAtAccept: 42,
    createdTs: 1_700_000_000,
    metrics: {
      is: { rankIc: 0.0612, ic: null, ir: 0.83, tStat: null, monotonicity: null,
            topBottomSpread: null, signStability: null, samples: 800, bars: 800 },
      oos: { rankIc: 0.0451, ic: null, ir: null, tStat: null, monotonicity: null,
             topBottomSpread: null, signStability: null, samples: 350, bars: 350 },
      turnoverProxy: 0.221,
    },
    ...over,
  };
}

beforeEach(() => {
  contextValue.activeRunId = 1;
  listMock.mockReset();
  promoteMock.mockReset();
  listMock.mockResolvedValue({ items: [row()], total: 1 });
  promoteMock.mockResolvedValue({ promoted: { kind: "factor", entityId: 5, entityKind: "factor", reused: false } });
});
afterEach(cleanup);

describe("DiscoveryCandidates — 候选表", () => {
  it("默认只查 kept，并渲染关键指标列", async () => {
    render(<DiscoveryCandidates />);
    await waitFor(() => expect(listMock).toHaveBeenCalled());
    expect(listMock.mock.calls[0][1]).toMatchObject({ verdict: "kept", limit: 25, offset: 0 });
    expect(await screen.findByText(/sub\(ts_mean\(overnight,20\)/)).toBeTruthy();
    expect(screen.getByText("0.0612")).toBeTruthy();
    expect(screen.getByText("0.0451")).toBeTruthy();
    expect(screen.getByText("0.221")).toBeTruthy();
  });

  it("切换状态筛选会重新查询并把拒绝原因筛选放出来", async () => {
    render(<DiscoveryCandidates />);
    await waitFor(() => expect(listMock).toHaveBeenCalled());
    fireEvent.change(screen.getByLabelText("按状态筛选"), { target: { value: "rejected" } });
    await waitFor(() => expect(screen.getByLabelText("按拒绝原因筛选")).toBeTruthy());
    const last = listMock.mock.calls[listMock.mock.calls.length - 1][1];
    expect(last.verdict).toBe("rejected");
  });

  it("空态给出引导而不是空白表格", async () => {
    listMock.mockResolvedValue({ items: [], total: 0 });
    render(<DiscoveryCandidates />);
    expect(await screen.findByText(/当前筛选下没有候选/)).toBeTruthy();
  });

  it("批量入库：未选中时禁用；选中 kept 行后可用", async () => {
    render(<DiscoveryCandidates />);
    const button = await screen.findByRole("button", { name: /批量入库/ });
    expect(button).toBeDisabled();
    fireEvent.click(await screen.findByLabelText(/^选择 /));
    expect(screen.getByRole("button", { name: /批量入库（1）/ })).toBeEnabled();
  });

  it("批量入库逐条调用后端并回报结果", async () => {
    render(<DiscoveryCandidates />);
    fireEvent.click(await screen.findByLabelText(/^选择 /));
    fireEvent.click(screen.getByRole("button", { name: /批量入库（1）/ }));
    await waitFor(() => expect(promoteMock).toHaveBeenCalledWith(11));
    expect(await screen.findByText("已入库 1 条")).toBeTruthy();
  });

  it("被拒候选没有勾选框（只有通过全部过滤条件的才可入库）", async () => {
    listMock.mockResolvedValue({
      items: [row({ verdict: "rejected", rejectReason: "F10" })],
      total: 1,
    });
    render(<DiscoveryCandidates />);
    await waitFor(() => expect(listMock).toHaveBeenCalled());
    expect(screen.queryByLabelText(/^选择 /)).toBeNull();
    expect(await screen.findByText("F10")).toBeTruthy();
  });

  it("已退化候选带独立徽标", async () => {
    listMock.mockResolvedValue({ items: [row({ demoted: true })], total: 1 });
    render(<DiscoveryCandidates />);
    expect(await screen.findByText("已退化")).toBeTruthy();
  });

  it("点表达式打开详情抽屉", async () => {
    render(<DiscoveryCandidates />);
    fireEvent.click(await screen.findByText(/sub\(ts_mean\(overnight,20\)/));
    expect(await screen.findByTestId("detail")).toBeTruthy();
  });

  it("总数超过一页才显示分页控件", async () => {
    listMock.mockResolvedValue({ items: [row()], total: 60 });
    render(<DiscoveryCandidates />);
    expect(await screen.findByRole("button", { name: "下一页" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "上一页" })).toBeDisabled();
  });

  it("没有选中任务时给出引导", () => {
    contextValue.activeRunId = null;
    render(<DiscoveryCandidates />);
    expect(screen.getByText(/还没有选中挖掘任务/)).toBeTruthy();
  });
});
