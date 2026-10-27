import { describe, expect, it } from "vitest";
import {
  AI_TRADE_LAUNCH_RISK_IDS,
  collectRisks,
  effectiveMinHoldSeconds,
  risksForStep,
  unguardedHorizon,
  type AiTradeLaunchRiskInput,
} from "./aiTradeLaunchRisk";
import type { AiTradeHorizonProfile } from "./aiTradeTypes";

/**
 * 风险注册表的纯函数用例（ai-trade-guided-launch §8.1 U-3 ~ U-6 / U-8）。
 *
 * 这里守的是不变式 I-1 的**上游**：注册表里那十条各自的触发 / 不触发断面，以及
 * 「`collectRisks()` 只会产出注册表里的 id」——否则第 6 步的清单就成了一个谁都能往里塞东西的
 * 自由文本区，而 W-13 / W-14 的等值判据也就守不住任何东西。
 */

/** 全部不触发的基线断面：`collectRisks()` 在它上面必须返回空数组。 */
const CLEAN: AiTradeLaunchRiskInput = {
  mode: "live",
  gearNote: null,
  tradabilityLevel: "ok",
  tradabilityHeadline: "约 12.0% 的开仓意图会被否决。",
  profitGuardEnabled: true,
  profitLockEnabled: true,
  horizonEnabled: true,
  effectiveMinHoldSeconds: 7200,
  interlockArmed: false,
  inertHardTakeProfitText: null,
  allocationOversubscribedPct: null,
  budgetClampText: null,
  truncatedFrom: null,
  maxSymbols: 5,
  reasoningDepth: "standard",
  wakeMinutes: 30,
  experienceEnabled: true,
  wakeFloorDivisor: 0,
  targetHoldMinSeconds: 14400,
  evidencePolicy: "strict",
};

function ids(over: Partial<AiTradeLaunchRiskInput>): string[] {
  return collectRisks({ ...CLEAN, ...over }).map((r) => r.id);
}

const PROFILES: AiTradeHorizonProfile[] = [
  { id: "intraday_swing", title: "日内波段", description: "", minHoldSeconds: 7200 },
  // `profile_wire` 对 `target_hold_min_s is None` 的条目下发 `null`（【v2 · P2-2】）。
  { id: "custom", title: "自定义", description: "", minHoldSeconds: null },
];

describe("U-3 / U-4 十条风险的触发与不触发断面", () => {
  it("基线断面上一条都不触发", () => {
    expect(ids({})).toEqual([]);
  });

  it("gearResolution：档位过粗 ⇒ 触发并落在 capital 步", () => {
    const risks = collectRisks({ ...CLEAN, gearNote: "0.5 档位上三层风控的缩减会被吸附抹掉" });
    expect(risks.map((r) => r.id)).toEqual(["gearResolution"]);
    expect(risks[0].steps).toEqual(["capital"]);
    expect(risks[0].detail).toContain("吸附");
    expect(ids({ gearNote: null })).toEqual([]);
  });

  it("tradabilityInfeasible：仅 infeasible 触发，且要求一次显式勾选", () => {
    const risks = collectRisks({ ...CLEAN, tradabilityLevel: "infeasible" });
    expect(risks.map((r) => r.id)).toEqual(["tradabilityInfeasible"]);
    expect(risks[0].requiresAck).toBe(true);
    expect(risks[0].detail).toBe(CLEAN.tradabilityHeadline);
    for (const level of ["ok", "marginal", "unknown", null]) {
      expect(ids({ tradabilityLevel: level })).not.toContain("tradabilityInfeasible");
    }
  });

  it("profitGuardOff：判据是「利润保护未勾选」本身，与有没有周期锁无关", () => {
    // 极性注意：`unguardedHorizon()` 只决定 tone 与是否需要勾选，不决定这一条渲不渲染。
    const noHorizon = collectRisks({
      ...CLEAN,
      profitGuardEnabled: false,
      horizonEnabled: false,
      effectiveMinHoldSeconds: 0,
    });
    expect(noHorizon.map((r) => r.id)).toContain("profitGuardOff");
    expect(noHorizon.find((r) => r.id === "profitGuardOff")?.tone).toBe("warn");
    expect(noHorizon.find((r) => r.id === "profitGuardOff")?.requiresAck).toBeUndefined();
    expect(ids({ profitGuardEnabled: true })).not.toContain("profitGuardOff");
  });

  it("profitGuardOff：互锁武装 + 致命组合 ⇒ tone 升为 danger 且要求勾选，并说出是谁在拦你", () => {
    const armed = collectRisks({ ...CLEAN, profitGuardEnabled: false, interlockArmed: true });
    const hit = armed.find((r) => r.id === "profitGuardOff")!;
    expect(hit.tone).toBe("danger");
    expect(hit.requiresAck).toBe(true);
    expect(hit.detail).toContain("AI_TRADE_REQUIRE_PROFIT_EXIT_WITH_HORIZON");
    // 回测段不动真钱 ⇒ 不要求勾选（与既有 `unguardedBlocked` 的 live-only 极性一致）。
    const backtest = collectRisks({
      ...CLEAN,
      mode: "backtest",
      profitGuardEnabled: false,
      interlockArmed: true,
    });
    expect(backtest.find((r) => r.id === "profitGuardOff")?.requiresAck).toBeUndefined();
  });

  it("inertThresholds：保护层关 + 预设有硬止盈那一行 ⇒ 触发，整句取自后端下发的原文", () => {
    const risks = collectRisks({
      ...CLEAN,
      profitGuardEnabled: false,
      inertHardTakeProfitText: "利润保护·硬止盈 50%",
    });
    const hit = risks.find((r) => r.id === "inertThresholds")!;
    expect(hit.detail).toContain("利润保护·硬止盈 50%");
    // 保护层开着 ⇒ 那一行是作数的，不该说它失效。
    expect(ids({ inertHardTakeProfitText: "利润保护·硬止盈 50%" })).not.toContain("inertThresholds");
    // 预设本身没有这一行（如 aggressive 的硬止盈是 0）⇒ 不凭空造一条。
    expect(ids({ profitGuardEnabled: false, inertHardTakeProfitText: null }))
      .not.toContain("inertThresholds");
  });

  it("allocationOversubscribed / budgetClamped / membersTruncated 各自一正一反", () => {
    expect(ids({ allocationOversubscribedPct: 140 })).toContain("allocationOversubscribed");
    expect(ids({ allocationOversubscribedPct: null })).not.toContain("allocationOversubscribed");
    // 回测是单标的、第 1 步根本不渲染分配器：那里说「合计 140%」既为假，「去处理」也无处可去。
    expect(ids({ mode: "backtest", allocationOversubscribedPct: 140 }))
      .not.toContain("allocationOversubscribed");
    expect(ids({ budgetClampText: "已按上限夹紧" })).toContain("budgetClamped");
    expect(ids({ budgetClampText: null })).not.toContain("budgetClamped");
    expect(ids({ truncatedFrom: 7 })).toContain("membersTruncated");
    expect(ids({ truncatedFrom: null })).not.toContain("membersTruncated");
  });

  it("deepWakeTooShort：「极致」× 唤醒 < 6 分钟才触发，且成因与修法两步都渲染", () => {
    const risks = collectRisks({ ...CLEAN, reasoningDepth: "exhaustive", wakeMinutes: 3 });
    const hit = risks.find((r) => r.id === "deepWakeTooShort")!;
    expect(hit.steps).toEqual(["rhythm", "cognition"]);
    expect(risksForStep(risks, "rhythm").map((r) => r.id)).toContain("deepWakeTooShort");
    expect(risksForStep(risks, "cognition").map((r) => r.id)).toContain("deepWakeTooShort");
    expect(ids({ reasoningDepth: "exhaustive", wakeMinutes: 6 })).not.toContain("deepWakeTooShort");
    expect(ids({ reasoningDepth: "standard", wakeMinutes: 3 })).not.toContain("deepWakeTooShort");
    // 关闭定时唤醒（0 / null）⇒ 无从打架
    expect(ids({ reasoningDepth: "exhaustive", wakeMinutes: 0 })).not.toContain("deepWakeTooShort");
    expect(ids({ reasoningDepth: "exhaustive", wakeMinutes: null })).not.toContain("deepWakeTooShort");
  });

  it("experienceOff（【v2 · P1-3】补录）：判据是 `experience.enabled === false`", () => {
    const risks = collectRisks({ ...CLEAN, experienceEnabled: false });
    const hit = risks.find((r) => r.id === "experienceOff")!;
    expect(hit.steps).toEqual(["cognition"]);
    expect(hit.detail).toContain("既不读取也不沉淀");
    expect(ids({ experienceEnabled: true })).not.toContain("experienceOff");
    // 回测车道恒不装配经验库（经验卡在那里渲染的是「回测不读取也不写入经验库」那句常规说明，
    // `ai-trade-experience-off-note` 不可达）⇒ 这一条在回测下是一句假话，一个字都不该说。
    expect(ids({ mode: "backtest", experienceEnabled: false })).not.toContain("experienceOff");
  });

  it("wakeFloorRaised（【v2 · P1-3】补录）：除数为 0 时**恒不触发**", () => {
    // 除数默认 0（只观测不改行为），那时说一句「会被抬到 N 分钟」就是一句假话。
    expect(ids({ wakeFloorDivisor: 0, wakeMinutes: 1, targetHoldMinSeconds: 14400 }))
      .not.toContain("wakeFloorRaised");
    // 14400 / 24 = 600s = 10 分钟；唤醒 1 分钟 < 10 分钟 ⇒ 触发
    const risks = collectRisks({
      ...CLEAN,
      wakeFloorDivisor: 24,
      wakeMinutes: 1,
      targetHoldMinSeconds: 14400,
    });
    const hit = risks.find((r) => r.id === "wakeFloorRaised")!;
    expect(hit.detail).toContain("10.0 分钟");
    // 唤醒已在下限之上 ⇒ 不触发
    expect(ids({ wakeFloorDivisor: 24, wakeMinutes: 30, targetHoldMinSeconds: 14400 }))
      .not.toContain("wakeFloorRaised");
    // 目标持仓未知 ⇒ 不触发（算不出下限就不说下限）
    expect(ids({ wakeFloorDivisor: 24, wakeMinutes: 1, targetHoldMinSeconds: 0 }))
      .not.toContain("wakeFloorRaised");
  });

  it("profitLockOff：收益锁关闭才触发，产地是第 5 步，文案只引用可复算的取证数字", () => {
    expect(ids({ profitLockEnabled: true })).not.toContain("profitLockOff");
    const [risk] = collectRisks({ ...CLEAN, profitLockEnabled: false });
    expect(risk.id).toBe("profitLockOff");
    expect(risk.steps).toEqual(["guards"]);
    expect(risk.requiresAck).toBeUndefined();
    expect(risk.detail).toContain("131 笔");
    expect(risk.detail).not.toContain("**");
  });

  it("evidence*：observe ⇒ evidenceObserveOnly（warn）；balanced ⇒ evidenceBalanced（info）", () => {
    const [observe] = collectRisks({ ...CLEAN, evidencePolicy: "observe" });
    expect(observe.id).toBe("evidenceObserveOnly");
    expect(observe.tone).toBe("warn");
    expect(observe.steps).toEqual(["guards"]);
    expect(`${observe.title}：${observe.detail}`).toBe(
      "实证准入为「仅观察」：系统不会因为缺乏证据而停止下单。2026-09 的 281 次开仓意愿，" +
        "在 4–8 小时持有尺度上方向收益为负（−4.6 ~ −23.6 bps）。",
    );
    const [balanced] = collectRisks({ ...CLEAN, evidencePolicy: "balanced" });
    expect(balanced.id).toBe("evidenceBalanced");
    expect(balanced.tone).toBe("info");
    expect(balanced.steps).toEqual(["guards"]);
    expect(`${balanced.title}：${balanced.detail}`).toBe(
      "实证准入为「均衡」：证据不足的方向照常下单，只有被证明亏钱的方向会停下。" +
        "在严格档的回放里，这一档少挡掉了大部分亏损。",
    );
  });

  it("evidence*：strict（推荐档）与未装配（回测 / 缺省）都不产生风险项", () => {
    const evidenceIds = ["evidenceObserveOnly", "evidenceBalanced"];
    for (const evidencePolicy of ["strict", null, undefined] as const) {
      const got = ids({ evidencePolicy });
      for (const id of evidenceIds) expect(got).not.toContain(id);
    }
  });

  it("U-4：`collectRisks()` 只会产出注册表里的 id（清单外的一条都不许有）", () => {
    const everything = collectRisks({
      mode: "live",
      gearNote: "档位过粗",
      tradabilityLevel: "infeasible",
      tradabilityHeadline: "几乎开不出单",
      profitGuardEnabled: false,
      profitLockEnabled: false,
      horizonEnabled: true,
      effectiveMinHoldSeconds: 7200,
      interlockArmed: true,
      inertHardTakeProfitText: "利润保护·硬止盈 50%",
      allocationOversubscribedPct: 140,
      budgetClampText: "已夹紧",
      truncatedFrom: 7,
      maxSymbols: 5,
      reasoningDepth: "exhaustive",
      wakeMinutes: 2,
      experienceEnabled: false,
      wakeFloorDivisor: 24,
      targetHoldMinSeconds: 14400,
      evidencePolicy: "observe",
    });
    // 两条实证准入风险互斥（一次只能选一个档），故「全开断面」要并上 balanced 档那一条。
    const balanced = collectRisks({ ...CLEAN, evidencePolicy: "balanced" });
    const produced = [...everything, ...balanced].map((r) => r.id);
    expect(new Set(produced).size).toBe(produced.length);   // 不重复
    for (const id of produced) expect(AI_TRADE_LAUNCH_RISK_IDS).toContain(id);
    // 全开断面下十三条应当全部到齐——注册表少一条就说明有一条永远进不了第 6 步。
    expect(AI_TRADE_LAUNCH_RISK_IDS).toHaveLength(13);
    expect([...produced].sort()).toEqual([...AI_TRADE_LAUNCH_RISK_IDS].sort());
  });

  it("每条风险都有非空标题 / 说明，且不写 Markdown 强调符（JSX 里会原样显示星号）", () => {
    const all = collectRisks({
      ...CLEAN,
      gearNote: "档位过粗",
      tradabilityLevel: "infeasible",
      profitGuardEnabled: false,
      inertHardTakeProfitText: "利润保护·硬止盈 50%",
      allocationOversubscribedPct: 140,
      budgetClampText: "已夹紧",
      truncatedFrom: 7,
      reasoningDepth: "exhaustive",
      wakeMinutes: 2,
      experienceEnabled: false,
      wakeFloorDivisor: 24,
    }).concat(
      collectRisks({ ...CLEAN, evidencePolicy: "observe" }),
      collectRisks({ ...CLEAN, evidencePolicy: "balanced" }),
    );
    for (const risk of all) {
      expect(risk.title.trim().length).toBeGreaterThan(0);
      expect(risk.detail.trim().length).toBeGreaterThan(0);
      expect(risk.steps.length).toBeGreaterThan(0);
      expect(risk.title).not.toContain("**");
      expect(risk.detail).not.toContain("**");
    }
  });
});

describe("U-5 `unguardedHorizon()` 与后端真值表逐行同构", () => {
  // 逐行照抄 `test_g_a9_unguarded_horizon_truth_table_is_frozen`。
  const armed = { interlockArmed: true };
  it("两者都未装配 ⇒ false", () => {
    expect(unguardedHorizon({
      ...armed, profitGuardEnabled: true, horizonEnabled: false, effectiveMinHoldSeconds: 0,
    })).toBe(false);
  });
  it("周期装了但没锁（minHold=0）⇒ false", () => {
    expect(unguardedHorizon({
      ...armed, profitGuardEnabled: false, horizonEnabled: true, effectiveMinHoldSeconds: 0,
    })).toBe(false);
  });
  it("周期锁 + 保护层关 ⇒ true（致命组合）", () => {
    expect(unguardedHorizon({
      ...armed, profitGuardEnabled: false, horizonEnabled: true, effectiveMinHoldSeconds: 7200,
    })).toBe(true);
  });
  it("保护层开着 ⇒ false", () => {
    expect(unguardedHorizon({
      ...armed, profitGuardEnabled: true, horizonEnabled: true, effectiveMinHoldSeconds: 7200,
    })).toBe(false);
  });
  it("总闸关 ⇒ **恒 false**（本轮默认；判据整段不执行）", () => {
    expect(unguardedHorizon({
      interlockArmed: false,
      profitGuardEnabled: false,
      horizonEnabled: true,
      effectiveMinHoldSeconds: 7200,
    })).toBe(false);
  });
});

describe("U-6 / U-8 `effectiveMinHoldSeconds()`", () => {
  it("非 custom 取服务端下发值（前端不复制任何一个数）", () => {
    expect(effectiveMinHoldSeconds({
      profilesReady: true, profileId: "intraday_swing", profiles: PROFILES, customMinHoldRaw: "",
    })).toBe(7200);
  });

  it("custom 空串 ⇒ 0（与服务端「custom 预设 min_hold_s=0」逐字一致，是安全退出口）", () => {
    expect(effectiveMinHoldSeconds({
      profilesReady: true, profileId: "custom", profiles: PROFILES, customMinHoldRaw: "",
    })).toBe(0);
    expect(effectiveMinHoldSeconds({
      profilesReady: true, profileId: "custom", profiles: PROFILES, customMinHoldRaw: "  ",
    })).toBe(0);
    expect(effectiveMinHoldSeconds({
      profilesReady: true, profileId: "custom", profiles: PROFILES, customMinHoldRaw: "300",
    })).toBe(300);
  });

  it("U-8：`minHoldSeconds: null` 的条目返回 0（wire 上确实是 null，不是 undefined）", () => {
    expect(effectiveMinHoldSeconds({
      profilesReady: true, profileId: "custom", profiles: PROFILES, customMinHoldRaw: "",
    })).toBe(0);
    expect(effectiveMinHoldSeconds({
      profilesReady: true, profileId: "trend_hold", profiles: PROFILES, customMinHoldRaw: "",
    })).toBe(0);   // 表里没有这一项 ⇒ 0，不抛
  });

  it("U-8：预设表未落地 ⇒ **先于模式分支**返回 0（不确定一律不翻译成否决）", () => {
    // `horizonProfileId` 在预设表落地之前会回落成字面量 "custom"，先分支就会去读一个与服务端
    // 无关的用户输入——那是「判据取自不携带该信息的入参」的又一次现形。
    expect(effectiveMinHoldSeconds({
      profilesReady: false, profileId: "custom", profiles: [], customMinHoldRaw: "9999",
    })).toBe(0);
    expect(effectiveMinHoldSeconds({
      profilesReady: false, profileId: "intraday_swing", profiles: PROFILES, customMinHoldRaw: "",
    })).toBe(0);
  });
});
