import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { FactorMember } from "@/lib/factorTypes";
import ExploreConfigPanel from "./ExploreConfigPanel";

vi.mock("@/lib/factorApi", () => ({
  factorApi: { trainers: vi.fn().mockResolvedValue([]), list: vi.fn().mockResolvedValue([]) },
}));

const M = (key: string): FactorMember => ({ factorKey: key, params: {}, weight: 0, direction: "long" });

function wrap(node: ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{node}</QueryClientProvider>);
}

describe("ExploreConfigPanel · 手动指定策略", () => {
  afterEach(cleanup);

  it("选「手动指定」渲染 ManualComboBuilder，未选组合时候选不足阻断启动", async () => {
    wrap(<ExploreConfigPanel library={[]} initialPool={[M("a"), M("b"), M("c")]} onLaunch={vi.fn()} launching={false} />);
    fireEvent.click(await screen.findByRole("button", { name: "手动指定" }));
    expect(screen.getByTestId("manual-combo-builder")).toBeTruthy();
    // 无手动组合 → planCombos("manual",{combos:[]}) 抛错 → safeCount=0 < 下限 → 启动禁用（绝不静默）。
    expect((screen.getByRole("button", { name: /开始探索训练/ }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("手动组合达下限后可启动，onLaunch 收到 manual 的 params.combos", async () => {
    const onLaunch = vi.fn();
    wrap(<ExploreConfigPanel library={[]} initialPool={[M("a"), M("b")]} onLaunch={onLaunch} launching={false} />);
    fireEvent.click(await screen.findByRole("button", { name: "手动指定" }));
    fireEvent.click(screen.getByRole("button", { name: "+ 新增组合" }));
    fireEvent.click(screen.getByRole("button", { name: "a" }));
    fireEvent.click(screen.getByRole("button", { name: "b" }));
    // 1 组合 × 3 默认方法 = 3 候选 ≥ 下限 2 → 启动解禁。
    const start = screen.getByRole("button", { name: /开始探索训练/ });
    await waitFor(() => expect((start as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(start);
    expect(onLaunch).toHaveBeenCalledWith(expect.objectContaining({ strategy: "manual", params: { combos: [[0, 1]] } }));
  });
});
