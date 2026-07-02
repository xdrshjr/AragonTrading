import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import type { FactorMember } from "@/lib/factorTypes";
import type { CandidateState } from "@/lib/weightBatchTypes";
import type { LocalCombo } from "@/lib/weightExplore";
import { computeInsights } from "@/lib/exploreInsights";
import ExploreInsightsPanel from "./ExploreInsightsPanel";

const M = (key: string): FactorMember => ({ factorKey: key, params: {}, weight: 0, direction: "long" });
const combo = (id: string, keys: string[]): LocalCombo => ({ id, label: keys.join("·"), members: keys.map(M) });
const labelOf = (m: FactorMember) => m.factorKey;

function cand(runId: number, variantLabel: string, icOut: number): CandidateState {
  return {
    runId,
    method: "linear",
    variantLabel,
    status: "succeeded",
    phase: null,
    curve: [],
    liveWeights: null,
    result: {
      method: "linear",
      weights: {},
      normStats: { normWindow: 200, clip: 4 },
      metrics: { icIn: 0, icOut, r2In: 0, r2Out: 0, nTrain: 1, nValid: 1 },
      trainerVersion: null,
      note: "",
      provenance: { instId: "X", bar: "1H", startTs: 0, endTs: 1, horizon: 6 },
    },
    metrics: { icIn: 0, icOut, r2In: 0, r2Out: 0, nTrain: 1, nValid: 1 },
    error: null,
  };
}

describe("ExploreInsightsPanel", () => {
  afterEach(cleanup);

  it("正负边际提升按 --long / --short 发散上色，数值经 formatIc", () => {
    const combos = [combo("C0", ["a", "b"]), combo("C1", ["a", "c"]), combo("C2", ["b", "c"])];
    const insights = computeInsights([cand(1, "C0", 0.1), cand(2, "C1", 0.2), cand(3, "C2", 0.06)], combos);
    render(<ExploreInsightsPanel insights={insights} labelOf={labelOf} />);
    expect(screen.getByTestId("insights-panel")).toBeTruthy();
    expect(document.querySelector('[data-tone="long"]')).toBeTruthy(); // a/c 正贡献
    expect(document.querySelector('[data-tone="short"]')).toBeTruthy(); // b 负贡献
    expect(screen.getByText("+0.090")).toBeTruthy(); // a 边际提升，formatIc
    expect(screen.getByText("-0.120")).toBeTruthy(); // b 边际提升
  });

  it("恒在因子画中性占位并标注「始终包含」，数值显示 —", () => {
    const combos = [combo("C0", ["a", "b"]), combo("C1", ["a", "c"])];
    const insights = computeInsights([cand(1, "C0", 0.1), cand(2, "C1", 0.2)], combos);
    render(<ExploreInsightsPanel insights={insights} labelOf={labelOf} />);
    expect(screen.getByText("始终包含")).toBeTruthy();
    const rowA = screen.getAllByTestId("contrib-row").find((r) => within(r).queryByText("a"))!;
    expect(within(rowA).getByText("—")).toBeTruthy(); // 无对比 → null → —（不伪造 0）
  });

  it("样本不足时渲染友好提示而非图", () => {
    const insights = computeInsights([cand(1, "C0", 0.1)], [combo("C0", ["a", "b"])]);
    render(<ExploreInsightsPanel insights={insights} labelOf={labelOf} />);
    expect(screen.getByTestId("insights-weak")).toBeTruthy();
    expect(screen.queryByTestId("contrib-row")).toBeNull();
  });
});
