// 交易所侧兜底自愈的前端中文化（backstop-repair §6.2 U-1~14）。
//
// 本组守两件事：**七个 `state` 的标签与色调**，以及**全部 `blockedReason` 都有中文标签**。
// 后者是上一轮踩过的坑的直接护栏——后端加了一个新码而前端没跟进时，用户会在卡片上看到一串
// 原始英文，而那一格恰恰是他最需要读懂的时候。
import { describe, expect, it } from "vitest";
import { describeRepair, REPAIR_BLOCK_LABEL } from "./aiTradeBackstopRepair";
import type { AiTradeBackstopRepairWire } from "./aiTradeTypes";

function wire(over: Partial<AiTradeBackstopRepairWire> = {}): AiTradeBackstopRepairWire {
  return {
    mode: "plan",
    state: "planned",
    blockedReason: null,
    plannedStopPx: 61000,
    plannedSz: 0.3,
    coveredSz: 0,
    positionSz: 0.3,
    attemptsEpoch: 0,
    maxPerEpoch: 2,
    attemptsSession: 0,
    maxPerSession: 8,
    coolingSeconds: null,
    lastResult: null,
    ...over,
  };
}

describe("describeRepair · 七个 state", () => {
  it("U-1 键缺席 ⇒ null ⇒ 整行不渲染（未装配的会话上绝不挂一个空壳）", () => {
    expect(describeRepair(null)).toBeNull();
    expect(describeRepair(undefined)).toBeNull();
  });

  it("U-2 `idle`（无需补挂）同样不渲染——「一切正常」不需要占一行", () => {
    expect(describeRepair(wire({ state: "idle" }))).toBeNull();
  });

  it("U-3 `planned`：`accent` 而不是红——「有方案待执行」不是坏消息，是可行动状态", () => {
    const row = describeRepair(wire());
    expect(row?.value).toBe("方案就绪");
    expect(row?.tone).toBe("accent");
    expect(row?.action?.label).toBe("立即补挂");
    expect(row?.detail).toContain("61000");
    expect(row?.detail).toContain("0.3 张");
  });

  it("U-4 `placing` / `placed` 一律说「已发出 · 核实中」，**绝不说「已补挂」**", () => {
    for (const state of ["placing", "placed"] as const) {
      const row = describeRepair(wire({ state }));
      expect(row?.value).toBe("已发出 · 核实中");
      expect(row?.detail).toContain("正在向交易所核实");
      expect(row?.detail).not.toContain("已补挂");
      expect(row?.action).toBeNull();       // 核实中不给按钮：重复点只会浪费频率配额
    }
  });

  it("U-5 `verified` 才是绿的，且张数取**回执**里的实际值（不是方案里的预览值）", () => {
    const row = describeRepair(
      wire({ state: "verified", plannedSz: 0.9, lastResult: { ok: true, ts: 1, sz: 0.3 } }),
    );
    expect(row?.value).toBe("已补挂 · 已核实");
    expect(row?.tone).toBe("long");
    expect(row?.detail).toContain("0.3 张");
  });

  it("U-6 `failed`：红 + **中文原因（不是裸码）** + 一颗重试按钮", () => {
    const row = describeRepair(
      wire({
        state: "failed",
        lastResult: {
          ok: false, ts: 1, code: "INSUFFICIENT_BALANCE", codeLabel: "可用余额 / 保证金不足",
        },
      }),
    );
    expect(row?.value).toBe("补挂失败");
    expect(row?.tone).toBe("short");
    expect(row?.detail).toContain("可用余额 / 保证金不足");
    // 归一码的值域由 `okx/trading.py` 拥有并随交易所错误码演进——卡片上出现一个原始英文串，
    // 正是「后端加了新码、前端显示原始英文」那个坑的翻版，故这里反向断言它不在场。
    expect(row?.detail).not.toContain("INSUFFICIENT_BALANCE");
    expect(row?.detail).toContain("仍无持久化兜底");
    expect(row?.action?.label).toBe("重试");
  });

  it("U-6b 后端没给中文标签（旧快照 / 未翻译的新码）⇒ 退回码本身，绝不显示一句猜的话", () => {
    const row = describeRepair(
      wire({ state: "failed", lastResult: { ok: false, ts: 1, code: "51008" } }),
    );
    expect(row?.detail).toContain("51008");
  });

  it("U-7 `blocked` 的两档色调：`px_through` / `verify_failed` 是红，其余是灰", () => {
    expect(
      describeRepair(wire({ state: "blocked", blockedReason: "px_through" }))?.tone,
    ).toBe("short");
    expect(
      describeRepair(wire({ state: "blocked", blockedReason: "verify_failed" }))?.tone,
    ).toBe("short");
    expect(describeRepair(wire({ state: "blocked", blockedReason: "cooling" }))?.tone).toBe(
      "muted",
    );
  });
});

describe("describeRepair · blockedReason 值域", () => {
  it("U-8~14 全部降级码都有中文标签（缺一即用户看到原始英文）", () => {
    const domain = [
      "not_applicable", "coverage_unknown", "no_local_px", "no_mark_px", "px_stale",
      "px_through", "px_too_close", "size_below_min", "order_in_flight", "cooling",
      "attempts_exhausted", "verify_failed",
    ];
    for (const reason of domain) {
      expect(REPAIR_BLOCK_LABEL[reason], reason).toBeTruthy();
      const row = describeRepair(wire({ state: "blocked", blockedReason: reason }));
      expect(row?.detail, reason).toContain(REPAIR_BLOCK_LABEL[reason]);
      expect(row?.detail, reason).not.toContain(reason);   // 绝不把裸码印出来
    }
  });

  it("`px_through` **不给重试按钮**——补一张会立刻触发的止损等于立刻市价平仓", () => {
    const row = describeRepair(wire({ state: "blocked", blockedReason: "px_through" }));
    expect(row?.action).toBeNull();
  });

  it("其余可重试的暂缓原因给按钮（冷却过去 / 挂单成交后再点一次即可）", () => {
    expect(
      describeRepair(wire({ state: "blocked", blockedReason: "order_in_flight" }))?.action?.label,
    ).toBe("重试");
  });

  it("冷却剩余秒数如实带出；未冷却 ⇒ 不出现「冷却剩余」字样（`null` ≠ `0`）", () => {
    const cooling = describeRepair(
      wire({ state: "blocked", blockedReason: "cooling", coolingSeconds: 42 }),
    );
    expect(cooling?.detail).toContain("冷却剩余 42s");
    const other = describeRepair(wire({ state: "blocked", blockedReason: "no_local_px" }));
    expect(other?.detail).not.toContain("冷却剩余");
  });

  it("未知的新码不崩、原样兜底显示（但用例会在上面那条值域断言里先红）", () => {
    const row = describeRepair(wire({ state: "blocked", blockedReason: "brand_new_code" }));
    expect(row?.value).toBe("已暂缓");
  });
});

describe("describeRepair · 数字口径", () => {
  it("`plannedStopPx` / `plannedSz` 算不出 ⇒ 显示「—」，绝不用 0 冒充", () => {
    const row = describeRepair(wire({ plannedStopPx: null, plannedSz: null }));
    expect(row?.detail).toContain("— / — 张");
  });

  it("配额如实带出（两个上限都来自后端，前端不硬编）", () => {
    const row = describeRepair(wire({ attemptsEpoch: 1, maxPerEpoch: 2, attemptsSession: 3 }));
    expect(row?.detail).toContain("本周期 1/2");
    expect(row?.detail).toContain("本会话 3/8");
  });

  it("行 key 恒为 `repair`——卡片按它定位第五行与那颗按钮", () => {
    expect(describeRepair(wire())?.key).toBe("repair");
    expect(describeRepair(wire())?.label).toBe("兜底自愈");
  });
});
