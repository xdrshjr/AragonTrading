import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import type { DiscoveryAssetCheckRow } from "@/lib/discoveryAssetTypes";
import DiscoveryHealthTrend from "./DiscoveryHealthTrend";

function check(over: Partial<DiscoveryAssetCheckRow>): DiscoveryAssetCheckRow {
  return {
    id: 1,
    checkedTs: 1_700_000_000,
    status: "healthy",
    reasons: [],
    retention: 0.9,
    clipMode: "",
    error: "",
    windowStartTs: 0,
    windowEndTs: 0,
    freshStartTs: 0,
    freshBars: 200,
    warmupBars: 400,
    metrics: {},
    ...over,
  };
}

afterEach(cleanup);

describe("DiscoveryHealthTrend", () => {
  it("不可判定的点画空心且不与相邻点连线", () => {
    // 把 unknown / error 连进折线，等于替系统编造一段它根本没有结论的历史。
    const { container } = render(
      <DiscoveryHealthTrend
        checks={[
          check({ id: 1, checkedTs: 100, retention: 0.9 }),
          check({ id: 2, checkedTs: 200, status: "unknown", retention: null }),
          check({ id: 3, checkedTs: 300, retention: 0.7 }),
        ]}
      />,
    );
    const circles = container.querySelectorAll("circle");
    expect(circles).toHaveLength(3);
    expect(circles[1].getAttribute("data-undecided")).toBe("true");
    expect(circles[1].getAttribute("fill")).toBe("none");
    // 三个点里中间那个不可判定 ⇒ 一条线段都连不起来。
    expect(container.querySelectorAll("path")).toHaveLength(0);
  });

  it("相邻两个都可判定时才连线段", () => {
    const { container } = render(
      <DiscoveryHealthTrend
        checks={[
          check({ id: 1, checkedTs: 100, retention: 0.9 }),
          check({ id: 2, checkedTs: 200, retention: 0.8 }),
          check({ id: 3, checkedTs: 300, status: "error", retention: null }),
        ]}
      />,
    );
    expect(container.querySelectorAll("path")).toHaveLength(1);
  });

  it("单个点不崩，也不画出无意义的线", () => {
    const { container } = render(
      <DiscoveryHealthTrend checks={[check({ retention: 0.5 })]} />,
    );
    expect(container.querySelectorAll("circle")).toHaveLength(1);
    expect(container.querySelectorAll("path")).toHaveLength(0);
  });

  it("全是 unknown 时给文案而不是一张空白图", () => {
    render(
      <DiscoveryHealthTrend
        checks={[
          check({ id: 1, status: "unknown", retention: null }),
          check({ id: 2, status: "unknown", retention: null }),
        ]}
      />,
    );
    expect(screen.getByText(/暂不评级/)).toBeTruthy();
    expect(screen.getByText(/这不是坏消息/)).toBeTruthy();
  });

  it("没有任何体检记录时解释「为什么还没有」", () => {
    render(<DiscoveryHealthTrend checks={[]} />);
    expect(screen.getByText(/攒够新数据才会做第一次体检/)).toBeTruthy();
  });
});
