import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { factorApi } from "@/lib/factorApi";
import type { FactorSet } from "@/lib/factorTypes";
import { useAppStore } from "@/lib/store";
import FactorSetList from "./FactorSetList";

vi.mock("@/lib/factorApi", () => ({
  factorApi: { sets: { list: vi.fn(), remove: vi.fn(), score: vi.fn() } },
}));
vi.mock("@/lib/api", () => ({ api: { createStrategy: vi.fn() } }));
// FactorSetDetail 依赖 FactorLab 的 WorkbenchContext；列表测聚焦列表行为，故以轻量桩替代详情。
vi.mock("./FactorSetDetail", () => ({
  default: ({ set }: { set: FactorSet }) => <div data-testid="detail">{set.name}</div>,
}));

const setsApi = factorApi.sets as unknown as {
  list: ReturnType<typeof vi.fn>;
  remove: ReturnType<typeof vi.fn>;
  score: ReturnType<typeof vi.fn>;
};

function makeSet(over: Partial<FactorSet>): FactorSet {
  return {
    id: 1,
    name: "组合A",
    members: [{ factorKey: "mom_roc", params: {}, weight: 0.5, direction: "long" }],
    method: "linear",
    config: { thresholds: undefined },
    model: { weights: { mom_roc: 0.5 } },
    metrics: null,
    enabled: true,
    createdTs: 1,
    updatedTs: 1,
    ...over,
  };
}

function renderList() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <FactorSetList />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
  useAppStore.setState({ factorEditTarget: null, factorLabSection: "sets", activeView: "factor" });
});
afterEach(cleanup);

describe("FactorSetList", () => {
  it("renders one card per saved factor set", async () => {
    setsApi.list.mockResolvedValue([makeSet({ id: 1, name: "组合A", updatedTs: 2 }), makeSet({ id: 2, name: "组合B", updatedTs: 5 })]);
    renderList();
    await waitFor(() => expect(screen.getByText("组合A")).toBeTruthy());
    expect(screen.getByText("组合B")).toBeTruthy();
  });

  it("shows the empty state with a builder shortcut when there are no sets", async () => {
    setsApi.list.mockResolvedValue([]);
    renderList();
    await waitFor(() => expect(screen.getByText(/还没有因子组合/)).toBeTruthy());
    expect(screen.getByRole("button", { name: "去新建组合" })).toBeTruthy();
  });

  it("dispatches an edit handoff (set target + builder section) when 编辑 is clicked", async () => {
    setsApi.list.mockResolvedValue([makeSet({ id: 7, name: "组合A" })]);
    renderList();
    await waitFor(() => expect(screen.getByText("组合A")).toBeTruthy());

    fireEvent.click(screen.getByRole("button", { name: "编辑" }));
    const target = useAppStore.getState().factorEditTarget;
    expect(target).toMatchObject({ source: "set", setId: 7 });
    expect(useAppStore.getState().factorLabSection).toBe("builder");
  });

  it("deletes after confirm, calls sets.remove and clears the selected detail", async () => {
    setsApi.list.mockResolvedValue([makeSet({ id: 7, name: "组合A" })]);
    setsApi.remove.mockResolvedValue(undefined);
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
    renderList();
    await waitFor(() => expect(screen.getByText("组合A")).toBeTruthy());

    // 选中卡片 → 详情桩出现，随后删除应清空选中（详情桩消失）。
    fireEvent.click(screen.getByText("组合A"));
    expect(screen.getByTestId("detail")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "删除" }));
    expect(confirmSpy).toHaveBeenCalled();
    await waitFor(() => expect(setsApi.remove).toHaveBeenCalledWith(7));
    await waitFor(() => expect(screen.queryByTestId("detail")).toBeNull());
  });

  it("does not delete when the confirm dialog is dismissed", async () => {
    setsApi.list.mockResolvedValue([makeSet({ id: 7, name: "组合A" })]);
    vi.spyOn(window, "confirm").mockReturnValue(false);
    renderList();
    await waitFor(() => expect(screen.getByText("组合A")).toBeTruthy());

    fireEvent.click(screen.getByRole("button", { name: "删除" }));
    expect(setsApi.remove).not.toHaveBeenCalled();
  });
});
