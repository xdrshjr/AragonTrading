import { describe, expect, it } from "vitest";

import {
  describeGearResolution,
  gearResolutionNote,
  positionLadder,
  quantizeSizeFraction,
} from "./aiTradeGearResolution";

/**
 * 门禁 `G-C5`：前后端两份 `describeGearResolution` 在四档上**逐格一致**。
 *
 * 下表是**内联的后端产物**（`python -c "from app.ai_auto.gear_resolution import
 * describe_gear_resolution as d; print(d(step))"` 的实跑结果）。冻结的是判据而不是文案——
 * 两处的措辞各自服务不同的时刻（这里是启动之前，后端那份是纪律自检里的运行期读数）。
 */
const BACKEND_TABLE: {
  step: number;
  gears: number;
  minGear: number;
  erased: string[];
  deadGears: Record<string, number>;
}[] = [
  {
    step: 0.5,
    gears: 2,
    minGear: 0.5,
    erased: ["brakeSlow", "contrarian", "corrScale"],
    deadGears: { brakeSlow: 1, contrarian: 1, corrScale: 1 },
  },
  {
    step: 0.25,
    gears: 4,
    minGear: 0.25,
    erased: [],
    deadGears: { brakeSlow: 1, contrarian: 1, corrScale: 1 },
  },
  {
    step: 0.05,
    gears: 20,
    minGear: 0.05,
    erased: [],
    deadGears: { brakeSlow: 1, contrarian: 1, corrScale: 1 },
  },
  {
    step: 1,
    gears: 1,
    minGear: 1,
    erased: ["brakeSlow", "contrarian", "corrScale"],
    deadGears: { brakeSlow: 1, contrarian: 1, corrScale: 1 },
  },
];

describe("aiTradeGearResolution", () => {
  it.each(BACKEND_TABLE)("matches the backend verdict at step $step", (row) => {
    const res = describeGearResolution(row.step);
    expect(res.gears).toBe(row.gears);
    expect(res.minGear).toBeCloseTo(row.minGear, 9);
    expect(res.erased).toEqual(row.erased);
    expect(res.deadGears).toEqual(row.deadGears);
  });

  it("treats a continuous ladder as having nothing to erase", () => {
    const res = describeGearResolution(0);
    expect(res.gears).toBe(0);
    expect(res.erased).toEqual([]);
    expect(res.survives).toEqual(["brakeSlow", "contrarian", "corrScale"]);
  });

  it("mirrors the backend ladder exactly", () => {
    expect(positionLadder(0.5)).toEqual([0.5, 1]);
    expect(positionLadder(0.25)).toEqual([0.25, 0.5, 0.75, 1]);
    expect(positionLadder(0.3)).toEqual([0.3, 0.6, 0.9]);
    expect(positionLadder(0)).toEqual([]);
    expect(positionLadder(-1)).toEqual([]);
  });

  it("reproduces the banker's rounding python uses at exactly half a gear", () => {
    // Python: round(0.5) == 0 ⇒ max(1, 0) ⇒ ladder[0]。JS 的 Math.round(0.5) == 1 会给出
    // ladder[1]——前后端在这个断面上分叉，正是「两份镜像必然漂移」的具体形态。
    expect(quantizeSizeFraction(0.025, 0.05)).toBeCloseTo(0.05, 9);
    expect(quantizeSizeFraction(0.25, 0.5)).toBeCloseTo(0.5, 9);
    expect(quantizeSizeFraction(0.75, 0.5)).toBeCloseTo(1, 9);
    expect(quantizeSizeFraction(0.37, 0.25)).toBeCloseTo(0.25, 9);
    // 任何 >0 的意图至少落到最小一格（这条地板正是本轮轴 C 要在**吸附之前**拦住的东西）。
    expect(quantizeSizeFraction(0.0001, 0.5)).toBeCloseTo(0.5, 9);
    // step<=0 ⇒ 连续直通
    expect(quantizeSizeFraction(0.37, 0)).toBeCloseTo(0.37, 9);
  });

  it("names every erased mechanism in the note and stays silent on a fine ladder", () => {
    const note = gearResolutionNote(describeGearResolution(0.5));
    expect(note).toContain("回撤刹车第一档");
    expect(note).toContain("逆势通路强制降档");
    expect(note).toContain("相关性缩减");
    expect(note).toContain("每档 50%");
    expect(gearResolutionNote(describeGearResolution(0.05))).toBeNull();
    expect(gearResolutionNote(describeGearResolution(0.25))).toBeNull();
  });

  it("never emits literal markdown bold markers into user-facing copy", () => {
    // 后端文案在 JSX 里显字面星号是本仓库第 5 次复发的缺陷；前端这一半同样要守。
    for (const step of [0.5, 0.25, 0.1, 0.05, 1]) {
      const note = gearResolutionNote(describeGearResolution(step));
      expect(note ?? "").not.toContain("**");
    }
  });

  it("honours an explicitly supplied multiplier instead of the representative default", () => {
    // `contrarian_size_mult = 1.0` ⇒ 根本没有缩减这回事 ⇒ 绝不判它 erased。
    const res = describeGearResolution(0.5, { contrarian: 1 });
    expect(res.erased).not.toContain("contrarian");
    expect(res.erased).toContain("brakeSlow");
  });
});
