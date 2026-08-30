import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { LiveAutoGroupState } from "@/lib/store";
import { useAppStore } from "@/lib/store";
import { tradingApi } from "@/lib/tradingApi";
import type { LiveFill, LiveOpenOrder, LivePosition } from "@/lib/tradingTypes";
import LiveBottomPanel from "./LiveBottomPanel";
import { FillsTableView } from "./LiveFillsTable";
import { OpenOrdersTableView } from "./LiveOpenOrdersTable";
import LivePositionsTable from "./LivePositionsTable";

/**
 * 实盘下方三张表的 Type C 契约冻结（workstation-text-nowrap-contract §9.1 B）。
 *
 * 这三张表是全仓最挤的一处——`LiveOpenOrdersTable` 有 10 列，实盘右栏在 1440 默认窗口下也只有
 * 约 578px，改前「随单止盈 / 随单止损 / 挂出时间」已经在折行。Type C 的承诺是：**表格超出容器
 * 就让容器横向滚动，绝不把表头劈成两行**；而展开的详情行仍必须换行。
 */

vi.mock("@/lib/tradingApi", () => ({
  tradingApi: {
    fills: vi.fn(),
    positions: vi.fn(),
    config: vi.fn(),
    openOrders: vi.fn(),
    placeOrder: vi.fn(),
  },
}));

const POSITION: LivePosition = {
  instId: "BTC-USDT-SWAP",
  instType: "SWAP",
  posSide: "long",
  pos: 2,
  avgPx: 30000,
  last: 30100,
  upl: 200,
  uplRatio: 0.01,
  lever: 2,
  marginMode: "cross",
};

const CONFIG = {
  accountId: 1,
  instId: POSITION.instId,
  posMode: "long_short_mode" as const,
  tdMode: "cross" as const,
  availUsdt: 1000,
  instMeta: { ctVal: 1, ctValCcy: "BTC", lotSz: 1, minSz: 1, tickSz: 0.1, maxLever: 10 },
  lastPx: 30100,
  instrumentSupported: true,
  liveTradingEnabled: true,
};

function autoGroup(): LiveAutoGroupState {
  return {
    groupId: "group-1",
    strategyId: 1,
    strategyName: "S",
    bar: "1H",
    totalBudgetUsd: 1000,
    leverage: 2,
    state: "running",
    startedTs: 1,
    members: {
      [POSITION.instId]: {
        instId: POSITION.instId,
        sessionId: "session-1",
        runId: 1,
        allocationPct: 100,
        budgetUsd: 1000,
        status: null,
        trades: [],
        position: {
          side: "long",
          size: 2,
          avgEntry: 30000,
          markPrice: 30100,
          unrealizedPnl: 200,
          positionPct: 0.4,
        },
      },
    },
  };
}

function fill(over: Partial<LiveFill> = {}): LiveFill {
  return {
    ts: 1_751_200_000,
    instId: "BTC-USDT-SWAP",
    side: "buy",
    fillPx: 64210.5,
    fillSz: 0.03,
    fee: -0.012,
    ordId: "1234567890",
    tradeId: "tid-1",
    clOrdId: "aragon18f3a2b1c004",
    positionPct: 0.12,
    ...over,
  };
}

function order(over: Partial<LiveOpenOrder> = {}): LiveOpenOrder {
  return {
    kind: "limit_open",
    instId: "BTC-USDT-SWAP",
    posSide: "long",
    side: "buy",
    px: 60000,
    triggerPx: null,
    sz: 1,
    state: "live",
    reduceOnly: false,
    ordId: "1003",
    algoId: null,
    createdTs: 1_719_500_002,
    ...over,
  };
}

// 改前实测的 10 个表头可访问名，逐字冻结（本轮承诺零文案变更）。
const OPEN_ORDER_HEADERS = [
  "合约",
  "类型",
  "方向",
  "委托价",
  "触发价",
  "数量",
  "随单止盈",
  "随单止损",
  "状态",
  "挂出时间",
];

function renderWithQuery(node: React.ReactNode) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={queryClient}>{node}</QueryClientProvider>);
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(tradingApi.fills).mockResolvedValue({ fills: [fill()] });
  vi.mocked(tradingApi.positions).mockResolvedValue({ positions: [POSITION] });
  vi.mocked(tradingApi.openOrders).mockResolvedValue({ orders: [] });
  vi.mocked(tradingApi.config).mockResolvedValue(CONFIG);
  useAppStore.setState({ liveAutoGroup: autoGroup(), aiTradeGroup: null, liveAutoPosition: null });
});

afterEach(cleanup);

describe("实盘数据表 — Type C 排版契约", () => {
  it("T11 成交记录表（实盘）表头与数据行不折行", () => {
    const { container } = render(<FillsTableView fills={[fill(), fill({ tradeId: "tid-2" })]} />);
    const table = container.querySelector("table")!;
    expect(table).toHaveClass("w-full");
    expect(table.className).toMatch(/min-w-\[\d+px\]/);
    expect(container.querySelector("thead")).toHaveClass("whitespace-nowrap");

    const bodyRows = Array.from(container.querySelectorAll("tbody tr"));
    expect(bodyRows).toHaveLength(2);
    for (const row of bodyRows) {
      expect(row).toHaveClass("whitespace-nowrap");
    }
  });

  it("T12 委托交易表 10 列表头一律不折行且可访问名零变更", () => {
    const { container } = render(
      <OpenOrdersTableView orders={[order()]} instId="BTC-USDT-SWAP" />,
    );
    const table = container.querySelector("table")!;
    expect(table).toHaveClass("w-full");
    expect(table.className).toMatch(/min-w-\[\d+px\]/);
    expect(container.querySelector("thead")).toHaveClass("whitespace-nowrap");

    const headers = screen.getAllByRole("columnheader");
    expect(headers.map((h) => h.textContent)).toEqual(OPEN_ORDER_HEADERS);

    for (const row of Array.from(container.querySelectorAll("tbody tr"))) {
      expect(row).toHaveClass("whitespace-nowrap");
    }
  });

  it("T13 持仓表数据行不折行，而展开的子委托行仍换行", async () => {
    const { container } = renderWithQuery(<LivePositionsTable instId={POSITION.instId} />);
    expect(await screen.findByText("仓位占比")).toBeTruthy();

    const table = container.querySelector("table")!;
    expect(table).toHaveClass("w-full");
    expect(table.className).toMatch(/min-w-\[\d+px\]/);
    expect(container.querySelector("thead")).toHaveClass("whitespace-nowrap");

    const dataRow = screen.getByText(POSITION.instId).closest("tr")!;
    expect(dataRow).toHaveClass("whitespace-nowrap");

    const detail = container.querySelector('td[colspan="8"]')!;
    expect(detail).toHaveClass("whitespace-normal");
  });

  it("T14 实盘右栏页签与内容容器：页签行不折行、内容宿主仍是 overflow-auto", async () => {
    const { container } = renderWithQuery(
      <LiveBottomPanel instId={POSITION.instId} markPrice={30100} />,
    );
    expect(await screen.findByRole("tab", { name: "委托交易" })).toBeTruthy();

    const tabRow = container.querySelector('[data-nowrap="row"]')!;
    expect(tabRow).toHaveClass("whitespace-nowrap");

    // 表格横向溢出的宿主：有人为了「让表格塞进去」把它改回 overflow-hidden，Type C 就静默失效。
    const host = tabRow.nextElementSibling!;
    expect(host).toHaveClass("overflow-auto");
  });
});
