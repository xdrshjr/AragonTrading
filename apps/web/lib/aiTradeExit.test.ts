import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import {
  MANUAL_EXIT_CONFIRM_LINES,
  describeExitCommitment,
  describeExitPriority,
  describeVetoedProposal,
  formatExitAge,
} from "./aiTradeExit";
import { EXIT_SOURCE_LABELS } from "./aiTradeAcceptance";
import { AXIS_VALUES, VALUE_LABEL_ZH } from "./experience";
import type { AiTradeExitCommitmentWire, AiTradeExitPriorityWire } from "./aiTradeTypes";

const ROOT = join(__dirname, "..", "..", "..");

function wire(over: Partial<AiTradeExitPriorityWire> = {}): AiTradeExitPriorityWire {
  return {
    level: "expedite",
    declared: "high",
    downgradeReason: null,
    releasedGates: ["G2"],
    intentStreak: 2,
    intentLapses: 1,
    holdSeconds: 3600,
    overrideRounds: 2,
    overrideMinHoldSeconds: 60,
    overridesUsed: 1,
    overridesRemaining: 5,
    maxOverridesPerSession: 6,
    adjudicated: false,
    mayPend: false,
    pending: false,
    failOpenReason: null,
    reason: "exit_priority_expedite(high)",
    ...over,
  };
}

function commitment(over: Partial<AiTradeExitCommitmentWire> = {}): AiTradeExitCommitmentWire {
  return {
    armed: true,
    attempts: 2,
    maxAttempts: 6,
    ageSeconds: 24,
    maxAgeSeconds: 300,
    retryIntervalSeconds: 10,
    priorityLevel: "override",
    reasonCode: "exit_priority_override(final,2/2)",
    lastError: null,
    stuck: false,
    ...over,
  };
}

// ---------------- 未装配 ⇒ 全部返回空（X-7 的前端一侧） ----------------
describe("未装配时整块不渲染", () => {
  it("键缺席 / level=none / 未武装 一律返回 null", () => {
    expect(describeExitPriority(null)).toBeNull();
    expect(describeExitPriority(undefined)).toBeNull();
    expect(describeExitPriority(wire({ level: "none" }))).toBeNull();
    expect(describeExitCommitment(null)).toBeNull();
    expect(describeExitCommitment(commitment({ armed: false }))).toBeNull();
    expect(describeVetoedProposal(null, "horizon")).toBeNull();
  });
});

// ---------------- 层名泛化（ai-trade-directional-symmetry B-3）----------------
describe("describeVetoedProposal 的层名", () => {
  it("有 layerLabel ⇒ 用后端下发的中文层名", () => {
    expect(describeVetoedProposal("open_short", "consensus", "同向共识门槛")).toBe(
      "模型提议的是「开空」，被同向共识门槛降级",
    );
  });

  it("老快照（无 layerLabel）：horizon 走既有映射，其余层回落「门禁」——**绝不显示英文 key**", () => {
    expect(describeVetoedProposal("close", "horizon")).toContain("交易周期层");
    const generic = describeVetoedProposal("open_short", "consensus")!;
    expect(generic).toContain("门禁");
    expect(generic).not.toContain("consensus");
    expect(describeVetoedProposal("open_long", "baseRuleMaBand")).not.toContain("baseRuleMaBand");
  });
});

// ---------------- 五档中文化 ----------------
describe("describeExitPriority", () => {
  it("expedite 只报 G2，且徽标用 accent（放行不是拦截，绝不用红色）", () => {
    const view = describeExitPriority(wire())!;
    expect(view.label).toContain("加速");
    expect(view.label).toContain("离场连续确认");
    expect(view.label).not.toContain("换手迟滞");
    expect(view.tone).toBe("accent");
  });

  it("override 报出三道闸全解", () => {
    const view = describeExitPriority(
      wire({ level: "override", declared: "final", releasedGates: ["G1", "G2", "G3"] }),
    )!;
    expect(view.label).toContain("最高优先级");
    expect(view.label).toContain("最短持仓");
    expect(view.label).toContain("换手迟滞");
  });

  it("adjudicated 与 override 同为 P3 但标签可区分（事后要能分开算账）", () => {
    const a = describeExitPriority(wire({ level: "adjudicated" }))!;
    const o = describeExitPriority(wire({ level: "override" }))!;
    expect(a.label).not.toBe(o.label);
  });

  it("normal 不报解闸、tone 为 muted", () => {
    const view = describeExitPriority(wire({ level: "normal", releasedGates: [] }))!;
    expect(view.label).not.toContain("已解除");
    expect(view.tone).toBe("muted");
  });

  it("账本行带上衰减说明；lapses=0 时不提衰减（避免噪音）", () => {
    expect(describeExitPriority(wire())!.detail).toContain("衰减");
    expect(describeExitPriority(wire({ intentLapses: 0 }))!.detail).not.toContain("衰减");
  });

  it("四种降档原因逐一写清（静默降档会让模型重复同一个错误）", () => {
    for (const reason of [
      "evidence_missing",
      "ledger_short",
      "quota_exhausted",
      "min_hold_floor",
    ] as const) {
      const view = describeExitPriority(wire({ declared: "final", downgradeReason: reason }))!;
      expect(view.note).toBeTruthy();
      // 原始英文码绝不能直接露出给用户
      expect(view.note).not.toContain(reason);
    }
  });

  it("挂起与 fail-open 都有各自的说明", () => {
    expect(describeExitPriority(wire({ pending: true }))!.note).toContain("复核官");
    expect(describeExitPriority(wire({ failOpenReason: "reflection_timeout" }))!.note).toContain(
      "未经复核官复核",
    );
  });
});

// ---------------- 兑现三态 ----------------
describe("describeExitCommitment", () => {
  it("正在兑现 ⇒ accent + 进度；受阻 ⇒ short + 进度满", () => {
    const armed = describeExitCommitment(commitment())!;
    expect(armed.tone).toBe("accent");
    expect(armed.stuck).toBe(false);
    expect(armed.progress).toBeCloseTo(2 / 6);
    const stuck = describeExitCommitment(commitment({ stuck: true, attempts: 6 }))!;
    expect(stuck.tone).toBe("short");
    expect(stuck.progress).toBe(1);
    // 受阻文案必须说清「交易所侧止损止盈仍生效」——否则用户会以为此刻完全裸奔
    expect(stuck.detail).toContain("交易所侧止损止盈仍生效");
  });
});

describe("formatExitAge", () => {
  it("秒 / 分 / 小时三档，缺失 ⇒ 破折号（绝不显示 0s 冒充「刚刚」）", () => {
    expect(formatExitAge(null)).toBe("—");
    expect(formatExitAge(24)).toBe("24s");
    expect(formatExitAge(90)).toBe("1m 30s");
    expect(formatExitAge(7260)).toBe("2h 1m");
  });
});

// ---------------- F-6：提议保真行 ----------------
describe("describeVetoedProposal", () => {
  it("把「模型提议 close、被交易周期层降级」说成人话", () => {
    const text = describeVetoedProposal("close", "horizon")!;
    expect(text).toContain("平仓");
    expect(text).toContain("交易周期层");
  });
});

// ---------------- 【立即离场】确认文案（三句缺一不可） ----------------
describe("二次确认文案", () => {
  it("三句话必须同时在场：市价平仓 / 会话继续 / 止盈损一并撤销", () => {
    expect(MANUAL_EXIT_CONFIRM_LINES).toHaveLength(3);
    const joined = MANUAL_EXIT_CONFIRM_LINES.join("");
    expect(joined).toContain("市价");
    expect(joined).toContain("继续运行");
    expect(joined).toContain("撤销");
  });
});

// ---------------- G-9：口径一致（两条断言，缺一不可） ----------------
describe("G-9 口径一致", () => {
  it("① 呈现层不出现硬编码的阈值数字（全部由后端 wire 下发）", () => {
    const src = readFileSync(join(__dirname, "aiTradeExit.ts"), "utf8");
    // `core.autocrlf=true`：写死 `\n` 会在干净检出上恒红，故一律 `\r?\n`（本仓库成文教训）。
    const lines = src.split(/\r?\n/);
    // 只扫**语句行**（跳过注释与文档），断言不出现本层的四个阈值默认值。
    const code = lines.filter((l) => !/^\s*(\*|\/\/|\/\*)/.test(l)).join("\n");
    for (const magic of ["overrideRounds: 2", "maxOverridesPerSession: 6", "60.0", "300.0"]) {
      expect(code).not.toContain(magic);
    }
  });

  it("② EXIT_SOURCES 的每一个值都能在前端两张标签表与 AXIS_VALUES.exit 里查到（双向覆盖）", () => {
    const py = readFileSync(
      join(ROOT, "services", "quant", "app", "ai_auto", "exit_attribution.py"),
      "utf8",
    );
    const block = py.split("EXIT_SOURCES: tuple[str, ...] = (")[1].split(")")[0];
    const sources = Array.from(block.matchAll(/"([A-Za-z]+)"/g)).map((m) => m[1]);
    // 常量表引用（`EXIT_SOURCE_AGENT_OVERRIDE`）不带引号，故单独补上它的值。
    if (!sources.includes("agentOverride")) sources.push("agentOverride");
    expect(sources).toContain("agentOverride");
    for (const source of sources) {
      // 漏一处**不会报错**，只会静默显示一个裸英文码 / 让该类在预演选择器里选不到。
      expect(EXIT_SOURCE_LABELS[source], `EXIT_SOURCE_LABELS 缺 ${source}`).toBeTruthy();
      expect(VALUE_LABEL_ZH[source], `VALUE_LABEL_ZH 缺 ${source}`).toBeTruthy();
      expect(AXIS_VALUES.exit, `AXIS_VALUES.exit 缺 ${source}`).toContain(source);
    }
  });

  it("③ RELEASE_GATES 的每一个键都能在前端 HORIZON_RELEASE_LABEL 里查到", () => {
    const py = readFileSync(
      join(ROOT, "services", "quant", "app", "ai_auto", "hold_horizon.py"),
      "utf8",
    );
    const ts = readFileSync(join(__dirname, "aiTradeHorizon.ts"), "utf8");
    const block = py.split("RELEASE_GATES: dict[str, tuple[str, ...]] = {")[1].split("\n}")[0];
    const keys = Array.from(block.matchAll(/^\s*RELEASE_([A-Z_]+):/gm)).map((m) => m[1]);
    expect(keys.length).toBeGreaterThanOrEqual(5); // 四条既有 + exit_pending
    expect(ts).toContain("exit_pending:");
  });
});
