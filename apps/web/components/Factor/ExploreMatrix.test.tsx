import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import type { FactorMember } from "@/lib/factorTypes";
import type { CandidateState } from "@/lib/weightBatchTypes";
import { assembleMatrix, localPlan, planLocalCombos } from "@/lib/weightExplore";
import ExploreMatrix from "./ExploreMatrix";

const M = (key: string): FactorMember => ({ factorKey: key, params: {}, weight: 0, direction: "long" });
const POOL = [M("a"), M("b"), M("c")];

function candidate(
  runId: number,
  variantLabel: string,
  method: "linear" | "xgboost",
  status: CandidateState["status"],
  icOut: number | null,
): CandidateState {
  return {
    runId, method, variantLabel, status, phase: null, curve: [], liveWeights: null, result: null,
    metrics: icOut == null ? null : { icIn: 0, icOut, r2In: 0, r2Out: 0, nTrain: 1, nValid: 1 },
    error: null,
  };
}

function buildMatrix(states: CandidateState[]) {
  const combos = planLocalCombos(POOL, "sizes", { sizes: [2] });
  const plan = localPlan(combos, ["linear", "xgboost"], ["a", "b", "c"], "sizes", { sizes: [2] });
  return assembleMatrix(plan, states);
}

describe("ExploreMatrix", () => {
  afterEach(cleanup);

  it("maps candidate status to cell content and marks the best with 🏆 + IC", () => {
    const states = [
      candidate(1, "C0", "linear", "succeeded", 0.08), // best
      candidate(2, "C0", "xgboost", "failed", null),
      candidate(3, "C1", "linear", "queued", null),
    ];
    render(<ExploreMatrix matrix={buildMatrix(states)} bestRunId={1} />);
    expect(screen.getByText("🏆")).toBeTruthy();
    expect(screen.getByText("+0.080")).toBeTruthy(); // succeeded IC 标注
    expect(screen.getByText("✕")).toBeTruthy(); // failed
    expect(screen.getByText("排队")).toBeTruthy(); // queued
  });

  it("renders empty placeholder cells for (combo,method) pairs without a candidate", () => {
    // 仅提供 C0:linear，其余格应为占位（无候选态文本）。
    const matrix = buildMatrix([candidate(1, "C0", "linear", "succeeded", 0.05)]);
    render(<ExploreMatrix matrix={matrix} bestRunId={1} />);
    // 3 组合 × 2 方法 = 6 格，仅 1 格有候选 → 至少 1 个占位单元格（aria-hidden div）。
    const placeholders = document.querySelectorAll("[aria-hidden].border-dashed");
    expect(placeholders.length).toBeGreaterThan(0);
  });
});
