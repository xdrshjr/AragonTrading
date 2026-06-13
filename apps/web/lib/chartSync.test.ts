import { describe, expect, it } from "vitest";
import { createSyncGroup } from "./chartSync";

// 极简桩 chart：记录订阅的 handler 与 setVisibleLogicalRange 调用次数；
// setVisibleLogicalRange 会回调自身 handler（模拟真实库在程序化设置范围时重新触发事件），
// 以此验证 createSyncGroup 的 applying guard 能阻断 A→B→A… 回环（R5）。
function makeStub() {
  let rangeHandler: ((r: unknown) => void) | undefined;
  const setRangeCalls: unknown[] = [];
  const timeScale = {
    subscribeVisibleLogicalRangeChange: (h: (r: unknown) => void) => {
      rangeHandler = h;
    },
    unsubscribeVisibleLogicalRangeChange: () => {
      rangeHandler = undefined;
    },
    setVisibleLogicalRange: (r: unknown) => {
      setRangeCalls.push(r);
      rangeHandler?.(r); // 模拟事件重新触发
    },
    getVisibleLogicalRange: () => null,
  };
  const chart = {
    timeScale: () => timeScale,
    subscribeCrosshairMove: () => {},
    unsubscribeCrosshairMove: () => {},
    setCrosshairPosition: () => {},
    clearCrosshairPosition: () => {},
  };
  return {
    chart,
    fireRange: (r: unknown) => rangeHandler?.(r),
    setRangeCalls,
  };
}

describe("createSyncGroup", () => {
  it("broadcasts a range change to every other chart exactly once (guard blocks re-entry)", () => {
    const a = makeStub();
    const b = makeStub();
    const c = makeStub();
    const group = createSyncGroup();
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    group.register(a.chart as any);
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    group.register(b.chart as any);
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    group.register(c.chart as any);

    a.fireRange({ from: 0, to: 10 });

    // 源图不回写自身；其余两图各被设置一次（无回环死循环）
    expect(a.setRangeCalls.length).toBe(0);
    expect(b.setRangeCalls.length).toBe(1);
    expect(c.setRangeCalls.length).toBe(1);
    expect(b.setRangeCalls[0]).toEqual({ from: 0, to: 10 });
  });

  it("unregister stops further broadcasts to the removed chart", () => {
    const a = makeStub();
    const b = makeStub();
    const group = createSyncGroup();
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    group.register(a.chart as any);
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const unregisterB = group.register(b.chart as any);

    unregisterB();
    a.fireRange({ from: 1, to: 5 });
    expect(b.setRangeCalls.length).toBe(0);
    expect(group.getCharts().length).toBe(1);
  });
});
