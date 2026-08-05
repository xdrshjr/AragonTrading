import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import type { DiscoveryIterationRow } from "@/lib/discoveryTypes";
import DiscoveryFunnel from "./DiscoveryFunnel";

function row(over: Partial<DiscoveryIterationRow> = {}): DiscoveryIterationRow {
  return {
    index: 7,
    startedTs: 0,
    finishedTs: 0,
    budget: { mutate: 0.25, crossover: 0.25, perturb: 0.15, random: 0.15, llm: 0.2 },
    generated: 40,
    ruleRejected: 6,
    llmRejected: 1,
    validated: 33,
    kept: 3,
    rejectedByReason: { S6: 9, F10: 4 },
    bySource: {},
    truncatedByTimeout: 0,
    overrun: false,
    skipped: "",
    durationMs: 1000,
    llm: { calls: 0, latencyMs: 0, degraded: false, degradedReason: "", promptChars: 0 },
    note: "",
    perAxis: {
      factor: { generated: 20, ruleRejected: 3, llmRejected: 0, validated: 17, kept: 2,
                truncated: 0, truncatedByBacktestCap: 0, rejectedByReason: {} },
      strategy_param: { generated: 20, ruleRejected: 3, llmRejected: 1, validated: 16,
                        kept: 1, truncated: 2, truncatedByBacktestCap: 3,
                        rejectedByReason: {} },
    },
    ...over,
  } as DiscoveryIterationRow;
}

afterEach(cleanup);

describe("DiscoveryFunnel — 漏斗 / 按轴分列 / 让渡行", () => {
  it("没有迭代时给引导文案而不是一根 0 宽的假柱子", () => {
    render(<DiscoveryFunnel row={null} />);
    expect(screen.getByText(/还没有完成的迭代/)).toBeTruthy();
  });

  it("逐段数字照常渲染", () => {
    render(<DiscoveryFunnel row={row()} />);
    // 「生成」「入库」在漏斗与按轴分列表头各出现一次，故用 getAllByText。
    expect(screen.getAllByText("生成").length).toBeGreaterThan(0);
    expect(screen.getAllByText("审出").length).toBe(1);
    expect(screen.getAllByText("入库").length).toBeGreaterThan(0);
  });

  it("多轴时按轴分列，并把两类截断合并成一列如实显示", () => {
    render(<DiscoveryFunnel row={row()} />);
    expect(screen.getByText("按轴分列")).toBeTruthy();
    expect(screen.getByText("常规策略")).toBeTruthy();
    expect(screen.getByText("5")).toBeTruthy();      // 2 次超时 + 3 次触顶
  });

  it("单轴时不画多余的分列表", () => {
    render(
      <DiscoveryFunnel
        row={row({
          perAxis: {
            factor: { generated: 40, ruleRejected: 6, llmRejected: 1, validated: 33, kept: 3,
                      truncated: 0, truncatedByBacktestCap: 0, rejectedByReason: {} },
          },
        })}
      />,
    );
    expect(screen.queryByText("按轴分列")).toBeNull();
  });

  it("让渡行是 BL-9 的可见性落点：让了多少、让给谁、为什么都必须在界面上", () => {
    render(
      <DiscoveryFunnel
        row={row({
          yieldedByPool: {
            strategy_combo: { count: 12, reason: "upstream_pool_too_small",
                              toAxis: "factor" },
          },
        })}
      />,
    );
    expect(screen.getByText("本轮让渡")).toBeTruthy();
    const text = screen.getByText(/策略轮动组合 12/).textContent ?? "";
    expect(text).toContain("因子");
    expect(text).toContain("常规策略池不足");
    expect(text).not.toContain("upstream_pool_too_small");
  });

  it("没有让渡时不渲染让渡区块", () => {
    render(<DiscoveryFunnel row={row()} />);
    expect(screen.queryByText("本轮让渡")).toBeNull();
  });

  it("拒绝原因 Top5 里的策略闸也显示中文（P1-5：绝不回显裸英文码）", () => {
    render(<DiscoveryFunnel row={row()} />);
    expect(screen.getByText("样本外崩塌")).toBeTruthy();
    expect(screen.getByText("与已入库因子高度相关")).toBeTruthy();
  });
});
