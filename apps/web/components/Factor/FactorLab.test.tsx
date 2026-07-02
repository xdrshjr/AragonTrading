import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useAppStore } from "@/lib/store";
import FactorLab from "./FactorLab";

// 默认工作区 catalog 改为探针：读 useWorkbench().instId 渲染出来，断言下拉切换确实下发到工作台上下文。
// 其余工作区以桩替代，避免拉起各自的数据拉取依赖（本测聚焦工作台条的标的下拉）。
vi.mock("./FactorCatalog", async () => {
  // 经 live namespace 惰性取 useWorkbench：FactorLab ⇄ FactorCatalog 互引，工厂运行时 FactorLab 尚未初始化完，
  // 解构会拿到 undefined；改为渲染期访问 mod.useWorkbench 读 live binding。
  const mod = await vi.importActual<typeof import("./FactorLab")>("./FactorLab");
  return { default: () => <div data-testid="wb-inst">{mod.useWorkbench().instId}</div> };
});
vi.mock("./FactorTable", () => ({ default: () => <div data-testid="my-factors" /> }));
vi.mock("./FactorEvalPanel", () => ({ default: () => <div data-testid="evaluate" /> }));
vi.mock("./FactorMiningPanel", () => ({ default: () => <div data-testid="mining" /> }));
vi.mock("./FactorSetBuilder", () => ({ default: () => <div data-testid="builder" /> }));
vi.mock("./FactorSetList", () => ({ default: () => <div data-testid="sets" /> }));
vi.mock("./SymbolGroupModal", () => ({ default: () => <div data-testid="symbol-group-modal" /> }));

beforeEach(() => {
  localStorage.clear();
  useAppStore.setState({
    instId: "BTC-USDT-SWAP",
    factorLabSection: "catalog",
    watchlist: ["BTC-USDT-SWAP", "ETH-USDT-SWAP"],
    symbolGroups: [],
    factorLabTarget: null,
  });
});
afterEach(cleanup);

describe("FactorLab — 目标选择器（单标的 / 标的组）", () => {
  it("renders_watchlist_options", () => {
    render(<FactorLab active={false} />);
    const select = screen.getByLabelText("标的") as HTMLSelectElement;
    const options = within(select).getAllByRole("option").map((o) => o.textContent);
    expect(options).toEqual(["BTC-USDT-SWAP", "ETH-USDT-SWAP"]);
    // 默认选中 store 的 instId（single 指针，value=`single:<instId>`）。
    expect(select.value).toBe("single:BTC-USDT-SWAP");
  });

  it("switching_single_option_updates_workbench_focus", () => {
    render(<FactorLab active={false} />);
    const select = screen.getByLabelText("标的") as HTMLSelectElement;
    fireEvent.change(select, { target: { value: "single:ETH-USDT-SWAP" } });
    expect(select.value).toBe("single:ETH-USDT-SWAP");
    expect(screen.getByTestId("wb-inst").textContent).toBe("ETH-USDT-SWAP");
  });

  it("selecting_group_sets_focus_to_first_member", () => {
    useAppStore.setState({
      symbolGroups: [
        { id: "g1", name: "主流币", symbols: ["ETH-USDT-SWAP", "BTC-USDT-SWAP"], createdTs: 1, updatedTs: 1 },
      ],
    });
    render(<FactorLab active={false} />);
    const select = screen.getByLabelText("标的") as HTMLSelectElement;
    fireEvent.change(select, { target: { value: "group:g1" } });
    expect(select.value).toBe("group:g1");
    // 焦点标的 = 组内首个成员（ETH），驱动图表 / 单标的语义。
    expect(screen.getByTestId("wb-inst").textContent).toBe("ETH-USDT-SWAP");
    expect(screen.getByText("2 标的")).toBeInTheDocument();
  });

  it("empty_watchlist_disables_select", () => {
    useAppStore.setState({ watchlist: [] });
    render(<FactorLab active={false} />);
    const select = screen.getByLabelText("标的") as HTMLSelectElement;
    expect(select.disabled).toBe(true);
    expect(within(select).getByRole("option").textContent).toBe("无自选标的");
    expect(screen.getByText("请先在交易工作台添加自选标的")).toBeInTheDocument();
  });
});
