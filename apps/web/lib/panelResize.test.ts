import { describe, expect, it } from "vitest";
import {
  ratioFromRightEdge,
  ratioFromTopEdge,
  widthFromLeftEdge,
  widthFromRightEdge,
} from "./panelResize";

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

describe("panelResize — widthFromLeftEdge", () => {
  it("widthFromLeftEdge_adds_pointer_from_left", () => {
    // 左锚加法方向：宽度随指针右移而增大（与右锚对偶）。
    expect(widthFromLeftEdge(200, 440)).toBe(240);
    expect(widthFromLeftEdge(200, 620)).toBe(420);
  });

  it("widthFromLeftEdge_returns_non_positive_when_pointer_past_left_edge", () => {
    // 指针越过左边缘 → 非正值，交由 store 夹取到下限（防符号错时仍可被守住）。
    expect(widthFromLeftEdge(200, 200)).toBe(0);
    expect(widthFromLeftEdge(200, 120)).toBe(-80);
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

describe("panelResize — ratioFromTopEdge", () => {
  it("ratioFromTopEdge_maps_pointer_offset_to_ratio", () => {
    // 上锚除法方向：上区占比随指针下移而增大。
    expect(ratioFromTopEdge(100, 400, 300)).toBeCloseTo(0.5, 10);
    expect(ratioFromTopEdge(0, 500, 400)).toBeCloseTo(0.8, 10);
  });

  it("ratioFromTopEdge_guards_zero_height", () => {
    // 除零守卫：高度为 0（或负）时返回 0，不产生 NaN / Infinity。
    expect(ratioFromTopEdge(100, 0, 300)).toBe(0);
    expect(ratioFromTopEdge(100, -10, 300)).toBe(0);
  });
});
