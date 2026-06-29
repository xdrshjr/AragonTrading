import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Strategy } from "@/lib/types";
import CompareStrategyRow from "./CompareStrategyRow";

function strat(overrides: Partial<Strategy> = {}): Strategy {
  return {
    id: 1,
    templateKey: "t1",
    name: "顺势回踩做多",
    description: "趋势回踩进场",
    kind: "atomic",
    params: {},
    paramsSchema: {},
    enabled: true,
    builtin: true,
    ...overrides,
  };
}

function renderRow(strategy: Strategy, extra: { checked?: boolean; disabled?: boolean } = {}) {
  const onToggle = vi.fn();
  const onOpenDetail = vi.fn();
  render(
    <CompareStrategyRow
      strategy={strategy}
      checked={extra.checked ?? false}
      disabled={extra.disabled ?? false}
      onToggle={onToggle}
      onOpenDetail={onOpenDetail}
    />,
  );
  return { onToggle, onOpenDetail };
}

describe("CompareStrategyRow", () => {
  afterEach(cleanup);

  it("clicking 详情 opens the detail and does NOT toggle selection", () => {
    const { onToggle, onOpenDetail } = renderRow(strat());
    fireEvent.click(screen.getByText("详情"));
    expect(onOpenDetail).toHaveBeenCalledTimes(1);
    expect(onToggle).not.toHaveBeenCalled();
  });

  it("clicking the selection checkbox toggles selection", () => {
    const { onToggle } = renderRow(strat());
    fireEvent.click(screen.getByRole("checkbox"));
    expect(onToggle).toHaveBeenCalledWith(1);
  });

  it("unavailable disables the checkbox but keeps 详情 clickable", () => {
    const { onOpenDetail } = renderRow(strat({ unavailable: true }), { disabled: true });
    expect((screen.getByRole("checkbox") as HTMLInputElement).disabled).toBe(true);
    fireEvent.click(screen.getByText("详情"));
    expect(onOpenDetail).toHaveBeenCalled();
  });

  it("renders the combo badge", () => {
    renderRow(strat({ kind: "combo", builtin: false }));
    expect(screen.getByText("组合")).toBeTruthy();
  });

  it("renders factor / 内置 / 不可用 badges", () => {
    renderRow(strat({ kind: "factor", builtin: true, unavailable: true }), { disabled: true });
    expect(screen.getByText("因子")).toBeTruthy();
    expect(screen.getByText("内置")).toBeTruthy();
    expect(screen.getByText("不可用")).toBeTruthy();
  });
});
