import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { DEFAULT_COMBO_POLICY } from "@/lib/combo";
import { useAppStore } from "@/lib/store";
import ComboView from "./ComboView";

const STRATEGIES = vi.hoisted(() => [
  { id: 1, templateKey: "trend_pullback_long", name: "顺势回踩做多", description: "", kind: "atomic",
    params: {}, paramsSchema: {}, enabled: true, builtin: true },
  { id: 2, templateKey: "range_scalping_strategy", name: "区间剥头皮", description: "", kind: "atomic",
    params: {}, paramsSchema: {}, enabled: true, builtin: true },
  { id: 9, templateKey: "regime_rotation", name: "趋势自适应策略组合", description: "", kind: "combo",
    params: {}, enabled: true, builtin: true,
    paramsSchema: {
      rebalanceBars: { type: "int", default: 24, min: 4, max: 240, step: 1, label: "再平衡间隔(根)" },
    } },
]);

vi.mock("@/lib/api", () => ({
  api: { strategies: vi.fn().mockResolvedValue(STRATEGIES) },
}));

function renderView() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <ComboView />
    </QueryClientProvider>,
  );
}

describe("ComboView", () => {
  beforeEach(() => {
    useAppStore.setState({ comboDraft: { memberIds: [], policy: { ...DEFAULT_COMBO_POLICY } } });
  });
  afterEach(cleanup);

  it("lists atomic member candidates and excludes combos from the picker", async () => {
    renderView();
    expect(await screen.findByText("顺势回踩做多")).toBeTruthy();
    expect(screen.getByText("区间剥头皮")).toBeTruthy();
    // 组合自身不应出现在成员候选列表中（不能把组合当成员）。
    expect(screen.queryByText("趋势自适应策略组合")).toBeNull();
  });

  it("disables the run button until at least 2 members are selected", async () => {
    renderView();
    const button = (await screen.findByText("运行组合回测")) as HTMLButtonElement;
    expect(button.disabled).toBe(true);
  });
});
