import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describeDegradeReason } from "./aiTrade";
import {
  describeHorizonGate,
  describeHorizonStatus,
  horizonCoherenceHints,
  horizonContractLines,
  horizonConfirmWindow,
  horizonEstimatedNote,
  horizonKpis,
  horizonLossRelease,
  horizonReleaseHint,
  horizonVetoLine,
  validateHorizon,
} from "./aiTradeHorizon";
import type {
  AiTradeHorizonProfile,
  AiTradeHorizonSnapshot,
  AiTradeHorizonStats,
  AiTradeRoundTrace,
  AiTradeSessionRow,
  AiTradeTraceEvent,
} from "./aiTradeTypes";
import { applyAiTradeTracePhase } from "./store";

// 交易周期模式的前端镜像（ai-trade-holding-horizon-modes §6.2）。
// 四条被冻结的性质：校验边界与后端 schema 一致、契约摘要**现算**（[P1-10]）、reducer 不吞新相位
// （[P1-4]）、本文件**不含任何预设数值常量**（R-3 / G-10）。

const INTRADAY: AiTradeHorizonProfile = {
  id: "intraday_swing",
  title: "日内波段",
  description: "在小时级别上捕捉一段趋势。",
  targetLabel: "4 – 12 小时",
  bar: "1H",
  wakeIntervalMinutes: 30,
  minHoldSeconds: 7200,
  exitConfirmRounds: 2,
  turnoverHysteresisK: 1.5,
  targetHoldMinSeconds: 14400,
  targetHoldMaxSeconds: 43200,
  expectedHoldBars: 12,
  confirmWindowSeconds: 3600,
  leverageMaxHint: 3,
};

const CUSTOM: AiTradeHorizonProfile = {
  id: "custom",
  title: "自定义",
  description: "自行设定。",
  targetLabel: null,
  bar: null,
  wakeIntervalMinutes: null,
  minHoldSeconds: null,
  exitConfirmRounds: null,
  turnoverHysteresisK: null,
  targetHoldMinSeconds: null,
  targetHoldMaxSeconds: null,
  expectedHoldBars: null,
  confirmWindowSeconds: null,
  leverageMaxHint: null,
};

function contract(over: Partial<Parameters<typeof horizonContractLines>[0]> = {}) {
  return horizonContractLines({
    profile: INTRADAY,
    bar: "1H",
    leverage: 3,
    stopLossPct: 0.05,
    defaultStopLossPct: 0.05,
    estimatedCostBps: 13,
    lossReleaseFrac: 0.5,
    lossReleaseFallbackPct: 0.1,
    ...over,
  });
}

function snapshot(over: Partial<AiTradeHorizonSnapshot> = {}): AiTradeHorizonSnapshot {
  return {
    profileId: "intraday_swing",
    title: "日内波段",
    targetLabel: "4 – 12 小时",
    status: "holding",
    holdSeconds: 5400,
    barsInPosition: 2,
    targetHoldMinSeconds: 14400,
    targetHoldMaxSeconds: 43200,
    minHoldSeconds: 7200,
    minHoldRemainingSeconds: 1800,
    exitConfirmRounds: 2,
    exitConfirmStreak: 1,
    confirmWindowSeconds: 3600,
    confirmWindowRemainingSeconds: 1200,
    confirmWindowElapsed: false,
    turnoverHysteresisK: 1.5,
    moveBps: 8.2,
    costBps: 13,
    requiredMoveBps: 19.5,
    returnPct: 0.02,
    lossReleaseReturnPct: 0.075,
    released: false,
    releaseReason: null,
    releasedGates: [],
    blockedStreak: 0,
    expectedHoldBars: 12,
    openedWallEstimated: false,
    gateReverse: true,
    asOfWall: 1_700_000_000,
    ...over,
  };
}

describe("validateHorizon", () => {
  it("enabled=false ⇒ 其余输入被忽略、一律合法（关闭态的残留值不该阻塞提交）", () => {
    expect(validateHorizon({ enabled: false, exitConfirmRounds: 999 })).toBeNull();
  });

  it("边界与后端 schema 一一对应：越界与目标区间倒置各报一条", () => {
    expect(validateHorizon({ enabled: true, exitConfirmRounds: 11 })).toContain("离场确认轮数");
    expect(validateHorizon({ enabled: true, minHoldSeconds: -1 })).toContain("最短持仓窗口");
    expect(validateHorizon({ enabled: true, turnoverHysteresisK: 11 })).toContain("换手迟滞");
    expect(
      validateHorizon({ enabled: true, targetHoldMinSeconds: 7200, targetHoldMaxSeconds: 3600 }),
    ).toBe("目标持仓下界不可大于上界");
    expect(validateHorizon({ enabled: true, minHoldSeconds: 0, exitConfirmRounds: 1 })).toBeNull();
  });
});

describe("契约摘要（[P1-10] 现算，无静态算例）", () => {
  it("四行都出现，且解除阈按「止损 × 杠杆 × frac」现算", () => {
    const lines = contract();
    expect(lines.map((l) => l.key)).toEqual(["minHold", "confirm", "hysteresis", "release"]);
    // 5% × 3× × 0.5 = 7.5%
    expect(lines[3].text).toContain("−7.50%");
    expect(lines[3].text).toContain("杠杆 3×");
  });

  it("改动杠杆或止损 ⇒ 第 4 行随之变化（这一行是用户判断锁安不安全的唯一依据）", () => {
    const base = contract()[3].text;
    expect(contract({ leverage: 5 })[3].text).not.toBe(base);
    expect(contract({ stopLossPct: 0.02 })[3].text).not.toBe(base);
  });

  it("显式把止损填 0 ⇒ 改述为兜底口径，绝不宣称一条并不存在的交易所止损", () => {
    const line = contract({ stopLossPct: 0 })[3].text;
    expect(line).toContain("兜底口径");
    expect(line).toContain("−10.00%");
  });

  it("确认窗口随所选执行周期变（4 根 5m = 20 分钟 ≪ 目标下界的四分之一）", () => {
    expect(horizonConfirmWindow(14400, "1H")).toBe(3600);
    expect(horizonConfirmWindow(14400, "5m")).toBe(1200);
    expect(horizonConfirmWindow(0, "1H")).toBe(0);
    expect(contract({ bar: "5m" })[1].text).toContain("20 分钟");
  });

  it("费率不可用 ⇒ 第 3 行如实说明，绝不编一个 bps", () => {
    expect(contract({ estimatedCostBps: null })[2].text).toContain("当前费率暂不可用");
  });

  it("custom（bar === null）⇒ 退化为一行说明，不编造任何数字", () => {
    const lines = contract({ profile: CUSTOM });
    expect(lines).toHaveLength(1);
    expect(lines[0].key).toBe("custom");
  });
});

describe("horizonLossRelease", () => {
  it("有 OCO 止损与杠杆 ⇒ oco 口径；缺任一 ⇒ 回落兜底常数", () => {
    const oco = horizonLossRelease(0.05, 3, 0.5, 0.1);
    expect(oco.basis).toBe("oco");
    expect(oco.pct).toBeCloseTo(0.075, 10);
    expect(horizonLossRelease(0, 3, 0.5, 0.1)).toEqual({ pct: 0.1, basis: "fallback" });
    expect(horizonLossRelease(0.05, null, 0.5, 0.1)).toEqual({ pct: 0.1, basis: "fallback" });
  });
});

describe("一致性提示（非阻断）", () => {
  it("唤醒长于最短窗口 / 唤醒远快于周期 / 杠杆越建议上限 各触发一条", () => {
    expect(horizonCoherenceHints({ profile: INTRADAY, bar: "1H", wakeMinutes: 180, leverage: 3 })
      .some((h) => h.includes("唤醒周期长于最短持仓窗口"))).toBe(true);
    expect(horizonCoherenceHints({ profile: INTRADAY, bar: "1H", wakeMinutes: 1, leverage: 3 })
      .some((h) => h.includes("唤醒远快于执行周期"))).toBe(true);
    expect(horizonCoherenceHints({ profile: INTRADAY, bar: "1H", wakeMinutes: 30, leverage: 10 })
      .some((h) => h.includes("建议上限"))).toBe(true);
  });

  it("执行周期偏粗（目标下界不足 2 根）也提示；custom 恒不提示", () => {
    expect(horizonCoherenceHints({ profile: INTRADAY, bar: "4H", wakeMinutes: 30, leverage: 3 })
      .some((h) => h.includes("执行周期偏粗"))).toBe(true);
    expect(horizonCoherenceHints({ profile: CUSTOM, bar: "1H", wakeMinutes: 1, leverage: 99 }))
      .toEqual([]);
  });
});

describe("describeHorizonGate / describeHorizonStatus", () => {
  it("未装配 ⇒ null（组件整段不渲染，老会话与老快照零影响）", () => {
    expect(describeHorizonGate(null, null)).toBeNull();
    expect(describeHorizonStatus(null)).toBeNull();
  });

  it("解除 ⇒ accent 而非 short：本层 fail-open，此刻主动平仓是**放行**的", () => {
    const view = describeHorizonGate("released", snapshot({ released: true, releaseReason: "loss_release" }));
    expect(view!.tone).toBe("accent");
    expect(view!.label).toContain("浮亏已达解除阈");
  });

  it("否决 ⇒ short；放行 / 空仓 ⇒ muted（常态不抢眼）", () => {
    expect(describeHorizonGate("veto", snapshot())!.tone).toBe("short");
    expect(describeHorizonGate("pass", snapshot())!.tone).toBe("muted");
    expect(describeHorizonGate("idle", snapshot({ status: "flat" }))!.label).toContain("不约束开仓");
  });

  it("chip：空仓态**不被吞掉**（新增边缘态必须验渲染门控没排除它，RV-2 教训）", () => {
    expect(describeHorizonStatus(snapshot({ status: "flat" }))).toContain("待入场");
    expect(describeHorizonStatus(snapshot())).toContain("还差");
    expect(describeHorizonStatus(snapshot({ released: true, releaseReason: "stale_px" })))
      .toContain("限制已解除");
  });
});

describe("降级码中文化", () => {
  it("三条前缀均命中且保留参数后缀（裸英文码会直接怼到用户脸上）", () => {
    expect(describeDegradeReason("horizon_min_hold(held=92m,min=120m)"))
      .toContain("(held=92m,min=120m)");
    expect(describeDegradeReason("horizon_exit_unconfirmed(1/2)")).toContain("(1/2)");
    expect(describeDegradeReason("horizon_turnover_hysteresis(move=8.2bps,req=19.5bps)"))
      .toContain("往返成本");
  });
});

describe("统计看板派生（[P2-3] 零样本呈现「—」）", () => {
  const stats: AiTradeHorizonStats = {
    profileId: "intraday_swing",
    title: "日内波段",
    minHoldSeconds: 7200,
    exitConfirmRounds: 2,
    turnoverHysteresisK: 1.5,
    expectedHoldBars: 12,
    minHoldVetoes: 3,
    unconfirmedVetoes: 5,
    hysteresisVetoes: 2,
    reverseVetoes: 1,
    releasedRounds: 4,
    releaseReasons: { loss_release: 3, stale_px: 1 },
    confirmWindowReleases: 2,
    roundTrips: 6,
    estimatedEpochs: 1,
    avgHoldSeconds: 9120,
    medianHoldSeconds: 8400,
    minHoldObservedSeconds: 3200,
    maxHoldObservedSeconds: 20100,
    withinTargetRate: 0.6667,
    turnoverPerDay: 1.8,
  };

  it("三格 KPI + 拦截计数行 + 豁免明细 title + 估算脚注（[P2-4] 全部有展示归宿）", () => {
    expect(horizonKpis(stats).map((k) => k.value)).toEqual(["2.53 小时", "1.80 次", "67%"]);
    expect(horizonVetoLine(stats)).toContain("最短窗口 3");
    expect(horizonReleaseHint(stats)).toContain("浮亏已达解除阈 3");
    expect(horizonReleaseHint(stats)).toContain("确认窗口到期放行 2");
    expect(horizonEstimatedNote(stats)).toContain("1 个持仓周期");
  });

  it("零样本 ⇒ 三格全「—」（绝不用 0% 冒充「还没有样本」）", () => {
    const empty = { ...stats, roundTrips: 0, avgHoldSeconds: null, withinTargetRate: null,
      turnoverPerDay: null, estimatedEpochs: 0 };
    expect(horizonKpis(empty).map((k) => k.value)).toEqual(["—", "—", "—"]);
    expect(horizonEstimatedNote(empty)).toBeNull();
  });

  it("未装配 ⇒ 三处全空（历史记录缺键即整段隐藏）", () => {
    expect(horizonKpis(null)).toEqual([]);
    expect(horizonVetoLine(null)).toBeNull();
    expect(horizonReleaseHint(null)).toBe("");
  });
});

// ---------- [P1-4] reducer 守门：本轮最容易静默失效的一处，必须有专属用例 ----------
describe("applyAiTradeTracePhase 的 horizon 相位", () => {
  it("喂一条 phase=horizon 的事件 ⇒ 三键都能取到（漏写 case 不会报错，只会安静地什么都不显示）", () => {
    const wire = snapshot();
    const evt: AiTradeTraceEvent = {
      phase: "horizon",
      round: 1,
      ts: 1_700_000_001,
      status: "veto",
      reason: "horizon_min_hold(held=30m,min=120m)",
      horizon: wire,
    };
    const [trace] = applyAiTradeTracePhase([], evt);
    expect(trace.horizonStatus).toBe("veto");
    expect(trace.horizonReason).toBe("horizon_min_hold(held=30m,min=120m)");
    expect(trace.horizonWire).toEqual(wire);
  });

  it("round_start 建的桩轮次三键为 null ⇒ HorizonBlock 整段不渲染（未装配零影响）", () => {
    const [trace] = applyAiTradeTracePhase([], { phase: "round_start", round: 2, ts: 2 });
    expect(trace.horizonStatus ?? null).toBeNull();
    expect(trace.horizonWire ?? null).toBeNull();
  });

  it("status 事件整体浅合并进 session ⇒ member.session.horizon 拿得到（[P1-4] 第 2 处）", () => {
    // `patchAiTradeMemberStatus` 走 `{...m.session, ...status}`；这里以同款展开验证类型与形状。
    const session = { positionSide: "long" } as unknown as AiTradeSessionRow;
    const merged: AiTradeSessionRow = { ...session, ...{ state: "running", horizon: snapshot() } };
    expect(merged.horizon).not.toBeNull();
    expect(merged.horizon!.profileId).toBe("intraday_swing");
  });
});

// ---------- G-10：本文件与实现文件都不得硬编码模式预设数值 ----------
describe("预设表单一真源（R-3 / G-10）", () => {
  it("aiTradeHorizon.ts 不含任何模式预设数值（7200 / 14400 / 43200 / 172800 / 259200）", () => {
    const raw = readFileSync(join(process.cwd(), "lib", "aiTradeHorizon.ts"), "utf8");
    // `HORIZON_BAR_SECONDS` 是**周期换算表**（与后端 `okx.types.BAR_SECONDS` 镜像），不是模式预设
    // ——一致性提示要按周期折算，别处无此表。扫描前先把它剔掉，否则 12H 的 43200 会误伤。
    const source = raw.replace(
      /export const HORIZON_BAR_SECONDS[\s\S]*?\n};\n/,
      "",
    );
    expect(source).not.toContain("HORIZON_BAR_SECONDS =");
    for (const preset of ["7200", "14400", "43200", "172800", "259200", "hourly_swing"]) {
      expect(source).not.toContain(preset);
    }
  });
});

// 类型占位：确保 AiTradeRoundTrace 的三个新键确实存在（漏声明时 tsc 会在此报错）。
const _typeGuard: Pick<AiTradeRoundTrace, "horizonStatus" | "horizonReason" | "horizonWire"> = {
  horizonStatus: null,
  horizonReason: null,
  horizonWire: null,
};
void _typeGuard;
