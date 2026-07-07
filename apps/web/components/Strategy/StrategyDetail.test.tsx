import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { DEFAULT_BACKTEST_CONFIG } from "@/lib/backtestConfig";
import type { FactorMember } from "@/lib/factorTypes";
import { useAppStore } from "@/lib/store";
import type { BacktestConfig, Strategy } from "@/lib/types";
import StrategyDetail from "./StrategyDetail";

const BACKTEST_CONFIG: BacktestConfig = {
  ...DEFAULT_BACKTEST_CONFIG,
  rangePreset: "custom",
  startTs: 1_700_000_000,
  endTs: 1_700_086_400,
};

const MEMBERS: FactorMember[] = [
  { factorKey: "mom_roc", params: {}, weight: 0.6, direction: "long" },
  { factorKey: "rev_rsi", params: {}, weight: 0.4, direction: "short" },
];

function factorStrategy(params: Record<string, unknown>): Strategy {
  return {
    id: 11,
    templateKey: "factor_score",
    name: "我的因子组合 · 得分策略",
    description: "因子组合得分策略",
    kind: "factor",
    params,
    paramsSchema: {},
    enabled: true,
    builtin: false,
  };
}

function renderDetail(strategy: Strategy) {
  // StrategyDetail 挂载 BacktestConfigModal（内部 useQuery），故需 QueryClientProvider 祖先。
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <StrategyDetail
        strategy={strategy}
        mode="backtest"
        busy={false}
        paperActive={false}
        backtestConfig={BACKTEST_CONFIG}
        onRunBacktest={vi.fn()}
        onStartPaper={vi.fn()}
        onStopPaper={vi.fn()}
        onSaveAs={vi.fn()}
      />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  localStorage.clear();
  useAppStore.setState({ factorEditTarget: null, factorLabSection: "catalog", activeView: "trade" });
});
afterEach(cleanup);

describe("StrategyDetail — 因子策略「在因子实验室编辑」回链", () => {
  it("with factorSetId → set handoff target that still carries the snapshot fallback (P1-1)", () => {
    renderDetail(
      factorStrategy({
        members: MEMBERS,
        thresholds: { enterLong: 0.3, enterShort: 0.3, exitBand: 0.1, normWindow: 200, squash: true },
        factorSetId: 7,
      }),
    );
    fireEvent.click(screen.getByRole("button", { name: "在因子实验室编辑" }));

    const target = useAppStore.getState().factorEditTarget;
    expect(target).toMatchObject({ source: "set", setId: 7, strategyId: 11 });
    expect(target?.source === "set" && target.snapshot != null).toBe(true);
    expect(useAppStore.getState().factorLabSection).toBe("builder");
    expect(useAppStore.getState().activeView).toBe("factor");
  });

  it("banner reminds that params are managed in the factor lab (set is the source of truth)", () => {
    renderDetail(
      factorStrategy({
        members: MEMBERS,
        thresholds: { enterLong: 0.3, enterShort: 0.3, exitBand: 0.1, normWindow: 200, squash: true },
        factorSetId: 7,
      }),
    );
    expect(screen.getByText(/参数由因子实验室的因子组合管理/)).toBeTruthy();
  });

  it("without factorSetId → snapshot handoff target with members from the strategy", () => {
    renderDetail(
      factorStrategy({
        members: MEMBERS,
        thresholds: { enterLong: 0.3, enterShort: 0.3, exitBand: 0.1, normWindow: 200, squash: true },
      }),
    );
    fireEvent.click(screen.getByRole("button", { name: "在因子实验室编辑" }));

    const target = useAppStore.getState().factorEditTarget;
    expect(target).toMatchObject({ source: "snapshot", strategyId: 11 });
    expect(target?.source === "snapshot" && target.snapshot.members).toEqual(MEMBERS);
    expect(useAppStore.getState().factorLabSection).toBe("builder");
    expect(useAppStore.getState().activeView).toBe("factor");
  });
});
