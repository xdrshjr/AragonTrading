import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Strategy } from "@/lib/types";
import StrategyList from "./StrategyList";

afterEach(cleanup);

const ATOMIC: Strategy = {
  id: 1,
  templateKey: "alpha",
  name: "Alpha",
  description: "Atomic strategy",
  kind: "atomic",
  params: {},
  paramsSchema: {},
  enabled: true,
  builtin: true,
};

const FACTOR: Strategy = {
  id: 2,
  templateKey: "factor_score",
  name: "因子A",
  description: "Factor combo score",
  kind: "factor",
  params: { factorSetId: 1 },
  paramsSchema: {},
  enabled: true,
  builtin: false,
};

describe("StrategyList card actions", () => {
  it("routes edit / delete clicks to their callbacks without selecting the card", () => {
    const onSelect = vi.fn();
    const onEditStrategy = vi.fn();
    const onDeleteStrategy = vi.fn();
    render(
      <StrategyList
        strategies={[FACTOR]}
        activeId={null}
        returnsById={{}}
        onSelect={onSelect}
        onEditStrategy={onEditStrategy}
        onDeleteStrategy={onDeleteStrategy}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "编辑 因子A" }));
    expect(onEditStrategy).toHaveBeenCalledWith(FACTOR);
    expect(onSelect).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "删除 因子A" }));
    expect(onDeleteStrategy).toHaveBeenCalledWith(FACTOR);
    expect(onSelect).not.toHaveBeenCalled();
  });

  it("selects the card when its selection button is clicked", () => {
    const onSelect = vi.fn();
    render(
      <StrategyList
        strategies={[FACTOR]}
        activeId={null}
        returnsById={{}}
        onSelect={onSelect}
        onEditStrategy={vi.fn()}
        onDeleteStrategy={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /^因子A/ }));
    expect(onSelect).toHaveBeenCalledWith(2);
  });

  it("disables delete for builtin strategies but allows it for the rest", () => {
    render(
      <StrategyList
        strategies={[ATOMIC, FACTOR]}
        activeId={null}
        returnsById={{}}
        onSelect={vi.fn()}
        onEditStrategy={vi.fn()}
        onDeleteStrategy={vi.fn()}
      />,
    );

    expect((screen.getByRole("button", { name: "删除 Alpha" }) as HTMLButtonElement).disabled).toBe(
      true,
    );
    expect((screen.getByRole("button", { name: "删除 因子A" }) as HTMLButtonElement).disabled).toBe(
      false,
    );
  });

  it("omits the action cluster when no action callbacks are provided", () => {
    render(
      <StrategyList strategies={[FACTOR]} activeId={null} returnsById={{}} onSelect={vi.fn()} />,
    );

    expect(screen.queryByRole("button", { name: /编辑/ })).toBeNull();
    expect(screen.queryByRole("button", { name: /删除/ })).toBeNull();
  });

  it("keeps interactive elements as siblings, never nested inside the selection button (R-OK-1)", () => {
    render(
      <StrategyList
        strategies={[FACTOR]}
        activeId={null}
        returnsById={{}}
        onSelect={vi.fn()}
        onToggleMultiSelect={vi.fn()}
        onEditStrategy={vi.fn()}
        onDeleteStrategy={vi.fn()}
      />,
    );

    const selectButton = screen.getByRole("button", { name: /^因子A/ });
    expect(selectButton.querySelector("button")).toBeNull();
    expect(selectButton.querySelector("input")).toBeNull();

    const checkbox = screen.getByRole("checkbox", { name: "选择 因子A" });
    expect(checkbox.closest("button")).toBeNull();
  });
});
