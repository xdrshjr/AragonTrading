import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { FactorMember } from "@/lib/factorTypes";
import { factorApi } from "@/lib/factorApi";
import { weightModelApi } from "@/lib/weightModelApi";
import type { WeightModel } from "@/lib/weightModelTypes";
import WeightModelEditor from "./WeightModelEditor";

// 工作台 + 候选交互桩；模型名校验（H-F6）聚焦提交禁用门，不拉起整个实验室。
vi.mock("./FactorLab", () => ({
  useWorkbench: () => ({
    target: { kind: "single", instId: "BTC-USDT-SWAP" }, symbols: ["BTC-USDT-SWAP"],
    instId: "BTC-USDT-SWAP", bar: "1H", startTs: 1, endTs: 2,
    setTarget: () => {}, setBar: () => {}, setRange: () => {},
    evalTarget: null, setEvalTarget: () => {},
    pendingMembers: [], addPendingMember: () => {}, clearPendingMembers: () => {},
  }),
}));
vi.mock("./MemberPalette", () => ({ default: () => <div data-testid="palette" /> }));
vi.mock("@/lib/weightModelApi", () => ({
  weightModelApi: { get: vi.fn(), create: vi.fn(), update: vi.fn() },
}));
vi.mock("@/lib/factorApi", () => ({ factorApi: { library: vi.fn() } }));

const api = weightModelApi as unknown as Record<string, ReturnType<typeof vi.fn>>;
const MEMBERS: FactorMember[] = [{ factorKey: "mom_roc", params: {}, weight: 0.5, direction: "long" }];

function model(): WeightModel {
  return {
    id: 5, name: "动量组合", instId: "BTC-USDT-SWAP", bar: "1H", members: MEMBERS, method: "linear",
    config: {}, horizon: 6, source: "manual", factorSetId: null, latestRunId: null, bestValidIc: null,
    notes: "", enabled: true, latestRun: null, createdTs: 1, updatedTs: 2,
  };
}

function wrap(node: ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={client}>{node}</QueryClientProvider>);
}

beforeEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
  api.get.mockResolvedValue(model());
  (factorApi.library as ReturnType<typeof vi.fn>).mockResolvedValue([]);
});
afterEach(cleanup);

describe("WeightModelEditor — 模型名必填校验（H-F6）", () => {
  it("disables save on empty/whitespace name and re-enables on a valid name", async () => {
    wrap(<WeightModelEditor editingId={5} onSaved={() => {}} onCancel={() => {}} />);
    // 编辑态水合后名称有效（成员也有效）→ 保存可点。
    await waitFor(() => {
      const save = screen.getByRole("button", { name: "保存修改" }) as HTMLButtonElement;
      expect(save.disabled).toBe(false);
    });
    const nameInput = screen.getByDisplayValue("动量组合") as HTMLInputElement;

    // 纯空白名 → 禁用提交 + 显式错误。
    fireEvent.change(nameInput, { target: { value: "   " } });
    await waitFor(() => {
      expect((screen.getByRole("button", { name: "保存修改" }) as HTMLButtonElement).disabled).toBe(true);
    });
    expect(screen.getByText("请填写模型名称")).toBeTruthy();

    // 合法名 → 解禁。
    fireEvent.change(nameInput, { target: { value: "新名字" } });
    await waitFor(() => {
      expect((screen.getByRole("button", { name: "保存修改" }) as HTMLButtonElement).disabled).toBe(false);
    });
    expect(screen.queryByText("请填写模型名称")).toBeNull();
  });
});
