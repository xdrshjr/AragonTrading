import "@testing-library/jest-dom/vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { DiscoveryAssetRow } from "@/lib/discoveryAssetTypes";
import type { DiscoveryRunSummary } from "@/lib/discoveryTypes";
import { useAppStore } from "@/lib/store";
import DiscoveryAssets from "./DiscoveryAssets";

const contextValue = {
  runs: [] as DiscoveryRunSummary[],
  defaults: null,
  loading: false,
  error: "",
  activeRunId: 1 as number | null,
  selectRun: vi.fn(),
  refresh: vi.fn(),
  goSection: vi.fn(),
};

vi.mock("./DiscoveryLab", () => ({ useDiscovery: () => contextValue }));
vi.mock("./DiscoveryAssetDetail", () => ({
  default: ({ assetId }: { assetId: number | null }) => (
    <div data-testid="detail-open">{assetId ?? "none"}</div>
  ),
}));

const list = vi.fn();
const archive = vi.fn();

vi.mock("@/lib/discoveryAssetApi", () => ({
  discoveryAssetApi: {
    list: (...args: unknown[]) => list(...args),
    archive: (...args: unknown[]) => archive(...args),
    exportUrl: (query: Record<string, unknown>) =>
      `/api/py/discovery/assets/export?${JSON.stringify(query)}`,
  },
  CHECK_TIMEOUT_MS: 65_000,
}));

function row(over: Partial<DiscoveryAssetRow> = {}): DiscoveryAssetRow {
  return {
    id: 12,
    assetKey: "f:9c1e",
    kind: "factor",
    label: "挖掘因子 9c1e：跳空溢价背离",
    instId: "BTC-USDT-SWAP",
    bar: "15m",
    horizon: 6,
    direction: "long",
    exprText: "sub(ts_mean(overnight,60),delta(amplitude,5))",
    describe: "跳空溢价背离",
    mechanism: "gap_premium",
    mechanismCn: "跳空溢价",
    firstFoundTs: 1_700_000_000,
    lastFoundTs: 1_700_000_500,
    sourceCount: 2,
    variantCount: 1,
    backfilled: false,
    acceptMetrics: {},
    promoted: null,
    health: {
      status: "watch",
      retention: 0.61,
      consecutiveFail: 1,
      lastCheckTs: 1_700_000_900,
      nextCheckTs: 1_700_100_000,
      reasons: ["H5"],
      checksCount: 7,
    },
    archived: false,
    note: "",
    ...over,
  };
}

beforeEach(() => {
  list.mockReset();
  archive.mockReset().mockResolvedValue({ id: 12, archived: true, archivedTs: 1 });
  contextValue.runs = [];
  useAppStore.setState({ discoveryAssetId: null });
});
afterEach(cleanup);

describe("DiscoveryAssets", () => {
  it("渲染台账行：健康徽标、留存度、轴、溯源计数", async () => {
    list.mockResolvedValue({ items: [row()], total: 1 });
    render(<DiscoveryAssets />);
    await screen.findByText(/sub\(ts_mean/);
    // 「观察中」「因子」在筛选下拉里也各有一个同名 option，故用 getAllByText。
    expect(screen.getAllByText("观察中").length).toBeGreaterThan(0);
    expect(screen.getByText("61%")).toBeTruthy();
    expect(screen.getAllByText("因子").length).toBeGreaterThan(0);
    expect(screen.getByText("溯源 2 次")).toBeTruthy();
    expect(screen.getAllByText("未入库").length).toBeGreaterThan(0);
  });

  it("筛选条件进入查询参数（轴 / 健康状态 / 搜索词）", async () => {
    list.mockResolvedValue({ items: [row()], total: 1 });
    render(<DiscoveryAssets />);
    await screen.findByText(/sub\(ts_mean/);
    fireEvent.change(screen.getByLabelText("健康状态"), { target: { value: "decayed" } });
    await waitFor(() =>
      expect(list).toHaveBeenLastCalledWith(expect.objectContaining({ status: "decayed" })),
    );
    fireEvent.change(screen.getByLabelText("搜索轴"), { target: { value: "strategy_param" } });
    await waitFor(() =>
      expect(list).toHaveBeenLastCalledWith(
        expect.objectContaining({ kind: "strategy_param" }),
      ),
    );
  });

  it("排序切换会重置分页偏移（否则第 2 页的偏移会套到新排序上）", async () => {
    list.mockResolvedValue({ items: [row()], total: 1 });
    render(<DiscoveryAssets />);
    await screen.findByText(/sub\(ts_mean/);
    fireEvent.change(screen.getByLabelText("排序"), { target: { value: "retention" } });
    await waitFor(() =>
      expect(list).toHaveBeenLastCalledWith(
        expect.objectContaining({ sort: "retention", offset: 0 }),
      ),
    );
  });

  // ---- 空态三分：三种情况的下一步动作完全不同，绝不用一句「暂无数据」糊过去 ----
  it("从未挖到：引导去新建任务，并说明上线前回填的口径", async () => {
    list.mockResolvedValue({ items: [], total: 0 });
    render(<DiscoveryAssets />);
    expect(await screen.findByText("还没有挖到任何成果")).toBeTruthy();
    expect(screen.getByText(/已尽力回填/)).toBeTruthy();
  });

  it("台账为空但有任务在跑：解释「kept 之后才登记」", async () => {
    contextValue.runs = [{ runId: 1 } as DiscoveryRunSummary];
    list.mockResolvedValue({ items: [], total: 0 });
    render(<DiscoveryAssets />);
    expect(await screen.findByText(/已经有挖掘任务在跑/)).toBeTruthy();
    expect(screen.getByText(/通过全部过滤条件/)).toBeTruthy();
  });

  it("筛选无结果：给「清空筛选」而不是让用户以为资产没了", async () => {
    list.mockResolvedValue({ items: [row()], total: 1 });
    render(<DiscoveryAssets />);
    await screen.findByText(/sub\(ts_mean/);
    list.mockResolvedValue({ items: [], total: 0 });
    fireEvent.change(screen.getByLabelText("健康状态"), { target: { value: "decayed" } });
    expect(await screen.findByText("当前筛选条件下没有匹配的成果资产")).toBeTruthy();
    expect(screen.getAllByText("清空筛选").length).toBeGreaterThan(0);
  });

  it("三种空态的文案互不相同", async () => {
    const texts: string[] = [];
    list.mockResolvedValue({ items: [], total: 0 });
    const first = render(<DiscoveryAssets />);
    texts.push((await screen.findByText("还没有挖到任何成果")).textContent ?? "");
    first.unmount();
    contextValue.runs = [{ runId: 1 } as DiscoveryRunSummary];
    const second = render(<DiscoveryAssets />);
    texts.push((await screen.findByText(/已经有挖掘任务在跑/)).textContent ?? "");
    second.unmount();
    expect(new Set(texts).size).toBe(texts.length);
  });

  it("多选后批量归档，并在完成后重新拉取列表", async () => {
    list.mockResolvedValue({ items: [row()], total: 1 });
    render(<DiscoveryAssets />);
    await screen.findByText(/sub\(ts_mean/);
    fireEvent.click(screen.getByLabelText(/选择 挖掘因子/));
    await act(async () => {
      fireEvent.click(screen.getByText(/归档选中 1 条/));
    });
    await waitFor(() => expect(archive).toHaveBeenCalledWith(12, true));
    expect(list).toHaveBeenCalledTimes(2);
  });

  it("点行打开详情抽屉（指针写进瞬态 store）", async () => {
    list.mockResolvedValue({ items: [row()], total: 1 });
    render(<DiscoveryAssets />);
    fireEvent.click(await screen.findByText(/sub\(ts_mean/));
    expect(useAppStore.getState().discoveryAssetId).toBe(12);
    expect(screen.getByTestId("detail-open").textContent).toBe("12");
  });

  it("「加载更多」是累加而不是替换（否则已看的那一页会凭空消失）", async () => {
    list.mockResolvedValueOnce({ items: [row({ id: 1 })], total: 2 });
    render(<DiscoveryAssets />);
    await screen.findByText(/已显示 1 \/ 2/);
    list.mockResolvedValueOnce({
      items: [row({ id: 2, exprText: "ts_zscore(volume,20)" })],
      total: 2,
    });
    fireEvent.click(screen.getByText(/加载更多/));
    expect(await screen.findByText("ts_zscore(volume,20)")).toBeTruthy();
    expect(screen.getByText(/sub\(ts_mean/)).toBeTruthy();   // 第一页仍在
    expect(list).toHaveBeenLastCalledWith(expect.objectContaining({ offset: 50 }));
  });

  it("回填资产在列表上如实标注", async () => {
    list.mockResolvedValue({ items: [row({ backfilled: true })], total: 1 });
    render(<DiscoveryAssets />);
    expect(await screen.findByText("上线前回填")).toBeTruthy();
  });
});
