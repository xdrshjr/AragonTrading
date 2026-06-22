import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { createSyncGroup } from "./chartSync";

// 极简桩 chart：记录订阅的 handler 与 setVisibleLogicalRange 调用次数；
// setVisibleLogicalRange 会回调自身 handler（模拟真实库在程序化设置范围时重新触发事件），
// 以此验证 createSyncGroup 的 applying guard 能阻断 A→B→A… 回环（R5）。
// priceScale(id) 桩用于覆盖缩放后价轴等宽重对齐：width() 返回当前像素宽，applyOptions 记录回写
// 并就地更新宽度以模拟「设下限后宽度收敛」，供断言重对齐把窄轴撑到与宽轴一致。
function makeStub(widths: { left: number; right: number } = { left: 64, right: 64 }) {
  let rangeHandler: ((r: unknown) => void) | undefined;
  const setRangeCalls: unknown[] = [];
  const applyCalls: { id: "left" | "right"; minimumWidth: number }[] = [];
  const w = { ...widths };
  const makeScale = (id: "left" | "right") => ({
    width: () => w[id],
    applyOptions: ({ minimumWidth }: { minimumWidth: number }) => {
      applyCalls.push({ id, minimumWidth });
      w[id] = minimumWidth;
    },
  });
  const scales = { left: makeScale("left"), right: makeScale("right") };
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
    priceScale: (id: "left" | "right") => scales[id],
    subscribeCrosshairMove: () => {},
    unsubscribeCrosshairMove: () => {},
    setCrosshairPosition: () => {},
    clearCrosshairPosition: () => {},
  };
  return {
    chart,
    fireRange: (r: unknown) => rangeHandler?.(r),
    setRangeCalls,
    applyCalls,
  };
}

// 捕获 requestAnimationFrame 回调而不自动执行，使「每帧至多一次重对齐」可被确定性断言。
let rafQueue: Array<(t: number) => void>;
let originalRaf: typeof globalThis.requestAnimationFrame;

beforeEach(() => {
  rafQueue = [];
  originalRaf = globalThis.requestAnimationFrame;
  globalThis.requestAnimationFrame = ((cb: (t: number) => void) => {
    rafQueue.push(cb);
    return rafQueue.length; // 返回非 0 句柄
  }) as typeof globalThis.requestAnimationFrame;
});

afterEach(() => {
  globalThis.requestAnimationFrame = originalRaf;
});

function flushRaf() {
  const q = rafQueue;
  rafQueue = [];
  for (const cb of q) cb(0);
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

  it("coalesces visible-range changes into one rAF realign per frame and equalizes price-scale widths", () => {
    const a = makeStub({ left: 64, right: 80 }); // 主图右轴更宽（高价标的缩放后标签变长）
    const b = makeStub({ left: 64, right: 64 }); // 副图右轴较窄 → 重对齐后应被撑到 80 与主图一致
    const group = createSyncGroup();
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    group.register(a.chart as any);
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    group.register(b.chart as any);
    rafQueue.length = 0; // 丢弃 register 时的初始范围同步 rAF，仅观察缩放路径的重对齐调度

    // 同一帧内多次范围事件（模拟滚轮缩放连发）只应合并调度一次重对齐
    a.fireRange({ from: 0, to: 100 });
    a.fireRange({ from: 1, to: 99 });
    a.fireRange({ from: 2, to: 98 });
    expect(rafQueue.length).toBe(1);

    flushRaf();

    // 较窄的副图右轴被回写到与主图一致的 80；已是最宽的主图右轴不重复写入（已对齐则不动 → 断反馈环）
    expect(b.applyCalls).toContainEqual({ id: "right", minimumWidth: 80 });
    expect(a.applyCalls.some((c) => c.id === "right")).toBe(false);

    // 帧结束后再次缩放 → 重新调度（是「每帧一次」而非永久封锁）
    a.fireRange({ from: 3, to: 97 });
    expect(rafQueue.length).toBe(1);

    // 收敛后再对齐应不再回写任何价轴宽（不动点 → 杜绝自激反馈环）
    b.applyCalls.length = 0;
    flushRaf();
    expect(a.applyCalls.length).toBe(0);
    expect(b.applyCalls.length).toBe(0);
  });
});
