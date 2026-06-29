import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Metrics, MultiSampleItem, MultiSampleResult } from "@/lib/types";
import { api } from "@/lib/api";
import MultiSampleResults from "./MultiSampleResults";

vi.mock("@/lib/api", () => ({
  api: { equity: vi.fn() },
}));

const METRICS: Metrics = {
  totalReturn: 0.12,
  winRate: 0.55,
  profitFactor: 1.6,
  maxDrawdown: -0.04,
  sharpe: 1.25,
  tradeCount: 11,
  avgPnl: 8,
};

function okItem(instId: string): MultiSampleItem {
  return { instId, status: "ok", runId: 1, metrics: METRICS, truncated: false };
}

function errItem(instId: string): MultiSampleItem {
  return { instId, status: "error", error: "No confirmed candles in requested range" };
}

function result(items: MultiSampleItem[]): MultiSampleResult {
  return { strategyId: 1, bar: "1H", startTs: 1, endTs: 2, items };
}

function renderResults(props: React.ComponentProps<typeof MultiSampleResults>) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <MultiSampleResults {...props} />
    </QueryClientProvider>,
  );
}

describe("MultiSampleResults", () => {
  beforeEach(() => {
    vi.mocked(api.equity).mockResolvedValue([]);
  });

  afterEach(cleanup);

  it("renders one tab per symbol and shows the first symbol's metrics", () => {
    renderResults({ result: result([okItem("BTC-USDT-SWAP"), okItem("ETH-USDT-SWAP")]) });
    expect(screen.getAllByRole("tab")).toHaveLength(2);
    // 首个标的（BTC）默认激活 → 绩效卡片渲染总收益率（"12.00%" 同时出现在 tab 微缩徽标与卡片）。
    expect(screen.getByText("总收益率")).toBeInTheDocument();
    expect(screen.getAllByText("12.00%").length).toBeGreaterThanOrEqual(1);
  });

  it("switches the active panel when another tab is selected", () => {
    renderResults({ result: result([okItem("BTC-USDT-SWAP"), errItem("ETH-USDT-SWAP")]) });
    expect(screen.queryByText("该标的回测失败")).toBeNull();

    fireEvent.click(screen.getByRole("tab", { name: /ETH-USDT-SWAP/ }));

    expect(screen.getByText("该标的回测失败")).toBeInTheDocument();
    expect(screen.getByText("No confirmed candles in requested range")).toBeInTheDocument();
  });

  it("marks the active tab via aria-selected", () => {
    renderResults({ result: result([okItem("BTC-USDT-SWAP"), okItem("ETH-USDT-SWAP")]) });
    const [first, second] = screen.getAllByRole("tab");
    expect(first).toHaveAttribute("aria-selected", "true");
    expect(second).toHaveAttribute("aria-selected", "false");
  });

  it("shows skeletons and no tabs while loading without a result", () => {
    renderResults({ result: null, loading: true });
    expect(screen.queryAllByRole("tab")).toHaveLength(0);
    expect(document.querySelectorAll(".skeleton").length).toBeGreaterThan(0);
  });

  it("degrades safely when items is empty", () => {
    renderResults({ result: result([]) });
    expect(screen.getByText("暂无多样本回测结果。")).toBeInTheDocument();
    expect(screen.queryAllByRole("tab")).toHaveLength(0);
  });
});
