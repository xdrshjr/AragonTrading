import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { TriggerExplain } from "@/lib/types";
import { tradingApi } from "@/lib/tradingApi";
import type { LiveFill } from "@/lib/tradingTypes";
import LiveFillsTable, { FillsTableView } from "./LiveFillsTable";

vi.mock("@/lib/tradingApi", () => ({
  tradingApi: { fills: vi.fn() },
}));

function factorExplain(): TriggerExplain {
  return {
    schema: 1,
    kind: "factor_score",
    side: "long",
    headline: "复合得分多头",
    summary: "因子组合复合得分触发。",
    metrics: [{ label: "复合得分", value: "0.42" }],
    composite: { score: 0.42, preSquash: 0.45, squash: true, enterLong: 0.3, enterShort: 0.3 },
    factors: [
      {
        factorKey: "mom_roc",
        name: "动量 ROC",
        direction: "long",
        value: 1.2,
        zscore: 1.4,
        weight: 0.6,
        contribution: 0.84,
        share: 1.0,
        isTopDriver: true,
      },
    ],
  };
}

function factorFill(over: Partial<LiveFill> = {}): LiveFill {
  return {
    ts: 1751200000,
    instId: "BTC-USDT-SWAP",
    side: "buy",
    fillPx: 64210.5,
    fillSz: 0.03,
    fee: -0.012,
    ordId: "1234567890",
    tradeId: "tid-1",
    clOrdId: "aragon18f3a2b1c004",
    positionPct: 0.12,
    explain: factorExplain(),
    ...over,
  };
}

function manualFill(over: Partial<LiveFill> = {}): LiveFill {
  return {
    ts: 1751100000,
    instId: "BTC-USDT-SWAP",
    side: "sell",
    fillPx: 64000,
    fillSz: 0.01,
    fee: -0.004,
    ordId: "999",
    tradeId: "tid-9",
    ...over,
  };
}

afterEach(cleanup);

describe("FillsTableView — expand / collapse", () => {
  it("renders an explain fill collapsed with its trigger-kind badge by default", () => {
    render(<FillsTableView fills={[factorFill()]} />);
    expect(screen.getByText("仓位占比")).toBeTruthy();
    expect(screen.getByText("12.00%")).toBeTruthy();
    const toggle = screen.getByRole("button", { expanded: false });
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    expect(screen.getByText("因子组合")).toBeTruthy();
    // 收起态不渲染详情摘要。
    expect(screen.queryByText(/因子组合复合得分触发/)).toBeNull();
  });

  it("expands a fill to reveal the explain detail and factor rows on click", () => {
    render(<FillsTableView fills={[factorFill()]} />);
    const toggle = screen.getByRole("button", { expanded: false });
    fireEvent.click(toggle);
    expect(toggle.getAttribute("aria-expanded")).toBe("true");
    expect(screen.getByText(/因子组合复合得分触发/)).toBeTruthy();
    expect(screen.getByText("动量 ROC")).toBeTruthy();
    expect(screen.getByText(/因子归因/)).toBeTruthy();
    expect(screen.getByText(/因子组合复合得分触发/).closest("td")?.getAttribute("colspan")).toBe("7");
  });

  it("renders a fill without explain as a plain '手动' row that is not expandable", () => {
    render(<FillsTableView fills={[manualFill()]} />);
    expect(screen.getByText("手动")).toBeTruthy();
    expect(screen.getByText("—")).toBeTruthy();
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("labels a position-percentage-only fill as automatic without making it expandable", () => {
    render(<FillsTableView fills={[manualFill({ positionPct: 0.08 })]} />);
    expect(screen.getByText("自动")).toBeTruthy();
    expect(screen.queryByText("手动")).toBeNull();
    expect(screen.getByText("8.00%")).toBeTruthy();
    expect(screen.queryByRole("button")).toBeNull();
  });
});

describe("FillsTableView — poll stability & partial fills", () => {
  it("keeps an expanded row open when a new fill is prepended (8s poll, keyed by tradeId)", () => {
    const a = factorFill({ tradeId: "tid-A", clOrdId: "aragonA" });
    const { rerender } = render(<FillsTableView fills={[a]} />);
    fireEvent.click(screen.getByRole("button", { expanded: false }));
    expect(screen.getByText("复合得分多头")).toBeTruthy();

    // 模拟轮询刷新：列表头部新增一行（不同 tradeId）。
    const b = factorFill({ tradeId: "tid-B", clOrdId: "aragonB" });
    rerender(<FillsTableView fills={[b, a]} />);

    // 原行仍展开（详情 headline 仍在），且仅有一行处于展开态。
    expect(screen.getByText("复合得分多头")).toBeTruthy();
    const expandedRows = screen
      .getAllByRole("button")
      .filter((el) => el.getAttribute("aria-expanded") === "true");
    expect(expandedRows.length).toBe(1);
  });

  it("renders partial fills sharing clOrdId/ordId as two independent rows without key clash", () => {
    const errSpy = vi.spyOn(console, "error").mockImplementation(() => {});
    const p1 = factorFill({ tradeId: "tid-p1", ordId: "ord-x", clOrdId: "aragonX" });
    const p2 = factorFill({ tradeId: "tid-p2", ordId: "ord-x", clOrdId: "aragonX", fillSz: 0.02 });
    render(<FillsTableView fills={[p1, p2]} />);

    expect(screen.getAllByRole("button").length).toBe(2);
    const dupKeyWarning = errSpy.mock.calls.some((c) =>
      String(c[0]).includes("same key") || String(c[0]).includes("Encountered two children"),
    );
    expect(dupKeyWarning).toBe(false);
    errSpy.mockRestore();
  });
});

describe("LiveFillsTable — query states", () => {
  function renderTable() {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    return render(
      <QueryClientProvider client={qc}>
        <LiveFillsTable instId="BTC-USDT-SWAP" />
      </QueryClientProvider>,
    );
  }

  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("shows the loading text while the first fetch is pending", () => {
    vi.mocked(tradingApi.fills).mockReturnValue(new Promise(() => {}));
    renderTable();
    expect(screen.getByText(/加载成交明细/)).toBeTruthy();
  });

  it("shows the empty-state text when there are no fills", async () => {
    vi.mocked(tradingApi.fills).mockResolvedValue({ fills: [] });
    renderTable();
    expect(await screen.findByText(/近三日无实盘成交记录/)).toBeTruthy();
  });

  it("shows an explicit error state when the fetch fails", async () => {
    vi.mocked(tradingApi.fills).mockRejectedValue(new Error("鉴权失败"));
    renderTable();
    expect(await screen.findByText(/加载成交失败/)).toBeTruthy();
  });
});
