import { describe, expect, it } from "vitest";
import { ratioFromRightEdge, widthFromRightEdge } from "./panelResize";

describe("panelResize — widthFromRightEdge", () => {
  it("widthFromRightEdge_subtracts_pointer_from_right", () => {
    // 右锚减法方向：宽度随指针左移而增大、右移而减小。
    expect(widthFromRightEdge(1000, 620)).toBe(380);
    expect(widthFromRightEdge(1000, 400)).toBe(600);
  });

  it("widthFromRightEdge_returns_non_positive_when_pointer_past_right_edge", () => {
    // 指针越过右边缘 → 非正值，交由 store 夹取到下限（防符号错时仍可被守住）。
    expect(widthFromRightEdge(1000, 1000)).toBe(0);
    expect(widthFromRightEdge(1000, 1200)).toBe(-200);
  });
});

describe("panelResize — ratioFromRightEdge", () => {
  it("ratioFromRightEdge_divides_remainder_by_width", () => {
    expect(ratioFromRightEdge(1200, 1200, 1020)).toBeCloseTo(0.15, 10);
    expect(ratioFromRightEdge(1200, 1200, 900)).toBeCloseTo(0.25, 10);
  });

  it("ratioFromRightEdge_guards_zero_width", () => {
    // 除零守卫：宽度为 0（或负）时返回 0，不产生 NaN / Infinity。
    expect(ratioFromRightEdge(1000, 0, 850)).toBe(0);
    expect(ratioFromRightEdge(1000, -10, 850)).toBe(0);
  });
});
