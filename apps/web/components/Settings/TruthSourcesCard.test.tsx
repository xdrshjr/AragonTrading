import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import TruthSourcesCard from "./TruthSourcesCard";
import type { TruthSourceRow } from "@/lib/truthSources";

const truthSourcesMock = vi.fn();
const refreshFeeRatesMock = vi.fn();

vi.mock("@/lib/aiTradeApi", () => ({
  aiTradeApi: {
    truthSources: () => truthSourcesMock(),
    refreshFeeRates: () => refreshFeeRatesMock(),
  },
}));

function row(over: Partial<TruthSourceRow>): TruthSourceRow {
  return {
    key: "feeRates", label: "交易所费率", state: "ok",
    reading: "交易所官方档位 Lv1：maker 2.00 / taker 5.00 bps",
    reason: "", since: 0, action: null, ...over,
  };
}

const DEGRADED = row({
  state: "degraded",
  reading: "本地保守常数：maker 2.00 / taker 5.00 bps",
  reason: "交易所拒绝了费率查询：51000 Parameter instId error",
  action: { kind: "retry", label: "重新查询费率",
            endpoint: "/api/ai-trade/fee-rates/refresh" },
});

beforeEach(() => {
  cleanup();
  truthSourcesMock.mockReset();
  refreshFeeRatesMock.mockReset();
});

describe("TruthSourcesCard（silent-degradation-repair 轴 E · E-2）", () => {
  it("默认折叠为一行摘要——一张常驻的四行诊断表会在两天内变成背景噪声", async () => {
    truthSourcesMock.mockResolvedValue({
      generatedTs: 1, sources: [DEGRADED, row({ key: "privateQuota", label: "账户接口配额" })],
    });
    render(<TruthSourcesCard />);
    await waitFor(() =>
      expect(screen.getByTestId("truth-sources-summary")).toHaveTextContent("1 项降级"));
    expect(screen.queryByTestId("truth-source-row-feeRates")).not.toBeInTheDocument();
  });

  it("展开后：状态不靠颜色单独承载（色点 + **文字标签** + 读数）", async () => {
    truthSourcesMock.mockResolvedValue({ generatedTs: 1, sources: [DEGRADED] });
    render(<TruthSourcesCard />);
    await waitFor(() => expect(screen.getByTestId("truth-sources-summary")).toBeInTheDocument());
    fireEvent.click(screen.getByText("真值源"));
    const line = screen.getByTestId("truth-source-row-feeRates");
    expect(line).toHaveAttribute("data-state", "degraded");
    expect(line).toHaveTextContent("降级");
    expect(line).toHaveTextContent("本地保守常数");
    // `reason` 是一句人话，原始码只是它的补充。
    expect(line).toHaveTextContent("交易所拒绝了费率查询");
  });

  it("`action` 为 null ⇒ **不画按钮**——一个点了没反应的按钮比没有按钮更糟", async () => {
    truthSourcesMock.mockResolvedValue({
      generatedTs: 1, sources: [row({ state: "unknown", reading: "未在运行，暂无读数" })],
    });
    render(<TruthSourcesCard />);
    await waitFor(() => expect(screen.getByTestId("truth-sources-summary")).toBeInTheDocument());
    fireEvent.click(screen.getByText("真值源"));
    expect(screen.queryByRole("button", { name: "重新查询费率" })).not.toBeInTheDocument();
  });

  it("`action` 不为 null ⇒ 按钮真的调那个端点", async () => {
    truthSourcesMock.mockResolvedValue({ generatedTs: 1, sources: [DEGRADED] });
    refreshFeeRatesMock.mockResolvedValue({ ok: true, feeRates: null });
    render(<TruthSourcesCard />);
    await waitFor(() => expect(screen.getByTestId("truth-sources-summary")).toBeInTheDocument());
    fireEvent.click(screen.getByText("真值源"));
    fireEvent.click(screen.getByRole("button", { name: "重新查询费率" }));
    await waitFor(() => expect(refreshFeeRatesMock).toHaveBeenCalledTimes(1));
    expect(screen.getByTestId("truth-sources-notice")).toHaveTextContent("已重新向交易所查询");
  });

  it("端点不存在（总闸关 ⇒ 路由不注册 ⇒ 404）⇒ 整卡不渲染，但说清为什么是空的", async () => {
    truthSourcesMock.mockRejectedValue(new Error("404"));
    const { container } = render(<TruthSourcesCard />);
    await waitFor(() =>
      expect(screen.getByTestId("truth-sources-unavailable")).toBeInTheDocument());
    // 卡片本身不渲染（总闸关时逐字节回退的那一条）……
    expect(container.querySelector("[data-testid='truth-sources-card']")).toBeNull();
    // ……但这一屏是设置中心的一个独立分区，点进来什么都没有会被读成「界面坏了」。
    expect(screen.getByTestId("truth-sources-unavailable"))
      .toHaveTextContent("AI_TRADE_TRUTH_SOURCES_ENABLED");
  });

  it("失败如实转述（未在跑时后端回 409，那是一个**正确**的回答）", async () => {
    truthSourcesMock.mockResolvedValue({ generatedTs: 1, sources: [DEGRADED] });
    refreshFeeRatesMock.mockRejectedValue(new Error("当前没有在跑的 AI 自动交易分组"));
    render(<TruthSourcesCard />);
    await waitFor(() => expect(screen.getByTestId("truth-sources-summary")).toBeInTheDocument());
    fireEvent.click(screen.getByText("真值源"));
    fireEvent.click(screen.getByRole("button", { name: "重新查询费率" }));
    await waitFor(() =>
      expect(screen.getByTestId("truth-sources-notice")).toHaveTextContent("没有在跑"));
  });

  it("界面文本里绝不出现 markdown 粗体标记（本仓第 5 次同形复发的预防）", async () => {
    truthSourcesMock.mockResolvedValue({ generatedTs: 1, sources: [DEGRADED] });
    render(<TruthSourcesCard />);
    await waitFor(() => expect(screen.getByTestId("truth-sources-summary")).toBeInTheDocument());
    fireEvent.click(screen.getByText("真值源"));
    expect(screen.getByTestId("truth-sources-card").textContent).not.toContain("**");
  });
});
