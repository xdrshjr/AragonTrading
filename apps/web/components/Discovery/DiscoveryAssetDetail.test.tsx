import "@testing-library/jest-dom/vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiRequestError } from "@/lib/api";
import type { DiscoveryAssetDetailRow } from "@/lib/discoveryAssetTypes";
import DiscoveryAssetDetail from "./DiscoveryAssetDetail";

const get = vi.fn();
const checkNow = vi.fn();
const archive = vi.fn();
const promote = vi.fn();
const patch = vi.fn();

vi.mock("@/lib/discoveryAssetApi", () => ({
  discoveryAssetApi: {
    get: (...args: unknown[]) => get(...args),
    checkNow: (...args: unknown[]) => checkNow(...args),
    archive: (...args: unknown[]) => archive(...args),
    promote: (...args: unknown[]) => promote(...args),
    patch: (...args: unknown[]) => patch(...args),
  },
  CHECK_TIMEOUT_MS: 65_000,
}));

function detail(over: Partial<DiscoveryAssetDetailRow> = {}): DiscoveryAssetDetailRow {
  return {
    id: 12,
    assetKey: "f:9c1e",
    kind: "factor",
    label: "挖掘因子 9c1e",
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
    genome: {},
    minedWindow: { startTs: 1, endTs: 2, isRatio: 0.7, windowBars: 4000, splitTs: 3 },
    acceptThresholds: { minAbsIc: 0.03 },
    trialsAtAccept: 812,
    sources: [
      {
        runId: 3,
        candidateId: 91,
        iterationIdx: 47,
        foundTs: 1_700_000_000,
        source: "mutate",
        genomeHash: "7f3adeadbeef",
        acceptSummary: { isRankIc: 0.044 },
        runExists: true,
      },
      {
        runId: 4,
        candidateId: 92,
        iterationIdx: 5,
        foundTs: 1_700_000_400,
        source: "crossover",
        genomeHash: "aa11bb22",
        acceptSummary: { isRankIc: 0.041 },
        runExists: false,
      },
    ],
    checks: [
      {
        id: 1,
        checkedTs: 1_700_000_900,
        status: "watch",
        reasons: ["H5"],
        retention: 0.61,
        clipMode: "",
        error: "",
        windowStartTs: 0,
        windowEndTs: 0,
        freshStartTs: 0,
        freshBars: 288,
        warmupBars: 400,
        metrics: {
          fresh: { rankIc: 0.021, ir: 0.31 },
          history: { rankIc: 0.044, ir: 0.62 },
        },
      },
    ],
    checksTotal: 1,
    checksPruned: 0,
    ...over,
  };
}

beforeEach(() => {
  get.mockReset().mockResolvedValue(detail());
  checkNow.mockReset();
  archive.mockReset().mockResolvedValue({ id: 12, archived: true, archivedTs: 1 });
  promote.mockReset();
  patch.mockReset();
});
afterEach(cleanup);

const open = () =>
  render(<DiscoveryAssetDetail assetId={12} onClose={vi.fn()} onChanged={vi.fn()} />);

describe("DiscoveryAssetDetail", () => {
  it("双栏口径说明句存在且带上 fresh 根数", async () => {
    open();
    expect(
      await screen.findByText(/只统计挖掘完成后新产生的 288 根 K 线/),
    ).toBeTruthy();
    expect(screen.getByText(/真正的样本外/)).toBeTruthy();
    // 登记时 vs 最近一次体检两栏并排
    expect(screen.getByText("0.0440")).toBeTruthy();
    expect(screen.getByText(/0\.0210/)).toBeTruthy();
  });

  it("悬空溯源渲染「任务已删除」且不渲染跳转", async () => {
    open();
    expect(await screen.findByText("任务已删除")).toBeTruthy();
    expect(screen.getByText("任务 #3")).toBeTruthy();
  });

  it("立即体检 409 时如实提示，不把失败当成功", async () => {
    checkNow.mockRejectedValue(
      new ApiRequestError("DISCOVERY_HEALTH_BUSY", "该资产正在体检中，请稍后再试", 409),
    );
    open();
    await screen.findByText(/立即体检/);
    await act(async () => {
      fireEvent.click(screen.getByText("立即体检"));
    });
    await waitFor(() => expect(screen.getByText(/正在体检中/)).toBeTruthy());
  });

  it("已归档资产隐藏「立即体检」按钮（归档即退出体检队列）", async () => {
    get.mockResolvedValue(detail({ archived: true }));
    open();
    await screen.findByText(/成果资产详情/);
    await waitFor(() => expect(screen.queryByText("立即体检")).toBeNull());
    expect(screen.getByText("取消归档")).toBeTruthy();
  });

  it("多基因合并时把「体检口径只取首次那一组」写在脸上（P1-7 的诚实性红线）", async () => {
    get.mockResolvedValue(detail({ variantCount: 3 }));
    open();
    expect(await screen.findByText(/合并了 3 组不同权重的成果/)).toBeTruthy();
    expect(screen.getByText(/体检口径取首次登记的那一组/)).toBeTruthy();
  });

  it("回填资产标注「挖掘窗口的切点未知」", async () => {
    get.mockResolvedValue(detail({ backfilled: true }));
    open();
    expect(await screen.findByText(/挖掘窗口的切点未知/)).toBeTruthy();
  });

  it("已入库时不再显示「入库」按钮，避免重复动作", async () => {
    get.mockResolvedValue(
      detail({ promoted: { kind: "factor", entityId: 18, companionStrategyId: null } }),
    );
    open();
    await screen.findByText(/成果资产详情/);
    await waitFor(() => expect(screen.queryByText("入库")).toBeNull());
  });

  it("留存度为 null 时显示「—」而不是 0（分母不可用 ≠ 跌到 0）", async () => {
    get.mockResolvedValue(
      detail({ health: { ...detail().health, retention: null } }),
    );
    open();
    await screen.findByText(/成果资产详情/);
    expect(screen.getAllByText("—").length).toBeGreaterThan(0);
  });
});
