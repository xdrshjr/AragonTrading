import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import QuantitySlider from "./QuantitySlider";

afterEach(cleanup);

function renderSlider(over: Partial<Parameters<typeof QuantitySlider>[0]> = {}) {
  const onChange = vi.fn();
  render(
    <QuantitySlider
      sz={0}
      maxSz={10}
      lotSz={1}
      ctValCcy="BTC"
      coinQty={0}
      notional={0}
      margin={0}
      onChange={onChange}
      {...over}
    />,
  );
  return { onChange };
}

describe("QuantitySlider — slider/input two-way linkage", () => {
  it("maps slider percentage to size on the lot grid", () => {
    const { onChange } = renderSlider();
    fireEvent.change(screen.getByLabelText("仓位数量百分比"), { target: { value: "50" } });
    // 50% of maxSz(10) on lotSz(1) grid = 5 张
    expect(onChange).toHaveBeenCalledWith(5);
  });

  it("commits manual input snapped down to the lot grid", () => {
    const { onChange } = renderSlider();
    const input = screen.getByLabelText("仓位数量（张）");
    fireEvent.change(input, { target: { value: "7.9" } });
    expect(onChange).toHaveBeenCalledWith(7);
  });

  it("quick percentage buttons jump to the matching size", () => {
    const { onChange } = renderSlider();
    fireEvent.click(screen.getByRole("button", { name: "100%" }));
    expect(onChange).toHaveBeenCalledWith(10);
  });

  it("reflects an externally controlled size in the slider position", () => {
    renderSlider({ sz: 5 });
    const slider = screen.getByLabelText("仓位数量百分比") as HTMLInputElement;
    // size 5 of maxSz 10 -> 50%
    expect(slider.value).toBe("50");
  });
});
