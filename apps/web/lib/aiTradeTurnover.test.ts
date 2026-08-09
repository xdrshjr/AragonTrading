import { describe, expect, it } from "vitest";
import {
  budgetProgress,
  describeBackstop,
  describeBudget,
  describeCooldown,
  describeTurnoverChip,
  describeVetoes,
  turnoverSeverity,
} from "./aiTradeTurnover";
import { describeDegradeReason } from "./aiTrade";
import type { AiTradeBackstopWire, AiTradeTurnoverWire } from "./aiTradeTypes";

function turnover(over: Partial<AiTradeTurnoverWire> = {}): AiTradeTurnoverWire {
  return {
    cooldownActive: false,
    cooldownRemainingSeconds: null,
    cooldownSide: null,
    cooldownScope: "same_side",
    cooldownSeconds: 3600,
    budgetEnabled: true,
    budgetScope: "session",
    tokens: 3,
    capacity: 3,
    perDay: 6,
    nextTokenSeconds: null,
    blocked: false,
    reverseWouldDowngrade: false,
    reentryVetoes: 0,
    budgetVetoes: 0,
    opensCounted: 0,
    ...over,
  };
}

function backstop(over: Partial<AiTradeBackstopWire> = {}): AiTradeBackstopWire {
  return {
    basis: "exchange",
    unavailableReason: null,
    checkedAgoSeconds: 42,
    stopLossPx: 62140,
    takeProfitPx: null,
    hasStop: true,
    coveredSz: 3,
    positionSz: 3,
    coverageRatio: 1,
    orders: 1,
    localStopLossPx: 62140,
    localTakeProfitPx: 68000,
    mismatch: false,
    ...over,
  };
}

describe("describeCooldown", () => {
  it("未装配（冷却时长为 0）⇒ null ⇒ 整行不渲染", () => {
    expect(describeCooldown(turnover({ cooldownSeconds: null }))).toBeNull();
    expect(describeCooldown(null)).toBeNull();
  });

  it("激活时给出剩余时间与豁免范围——只说「被拦」会让用户以为仓位也平不掉", () => {
    const row = describeCooldown(
      turnover({ cooldownActive: true, cooldownRemainingSeconds: 2892, cooldownSide: "long" }),
    );
    expect(row?.value).toContain("冷却中");
    expect(row?.detail).toContain("做多开仓暂停");
    expect(row?.detail).toContain("反向不受限");
    expect(row?.tone).toBe("accent");
  });

  it("scope=both 时如实说两个方向都暂停", () => {
    const row = describeCooldown(
      turnover({ cooldownActive: true, cooldownRemainingSeconds: 60, cooldownScope: "both" }),
    );
    expect(row?.detail).toContain("两个方向");
  });
});

describe("describeBudget", () => {
  it("措辞必须是「本会话」口径——令牌桶重启即回满，写「每日」就是一句做不到的话", () => {
    const row = describeBudget(turnover({ tokens: 0.42, nextTokenSeconds: 2520 }));
    expect(row?.value).toBe("0.4 / 3");
    expect(row?.detail).toContain("本会话上限 6 次/日");
    expect(row?.detail).toContain("重启后重新计算");
    expect(row?.detail).toContain("下一枚");
    expect(row?.tone).toBe("short");
  });

  it("未启用 ⇒ null；令牌充足 ⇒ 常态灰色", () => {
    expect(describeBudget(turnover({ budgetEnabled: false }))).toBeNull();
    expect(describeBudget(turnover())?.tone).toBe("muted");
    expect(budgetProgress(turnover({ tokens: 1.5 }))).toBeCloseTo(0.5);
    expect(budgetProgress(turnover({ budgetEnabled: false }))).toBeNull();
  });
});

describe("describeVetoes", () => {
  it("零拦截 ⇒ null——健康的长周期会话本就可能一次都不撞闸，不该显示一行 0", () => {
    expect(describeVetoes(turnover())).toBeNull();
    const row = describeVetoes(turnover({ reentryVetoes: 3, budgetVetoes: 7 }));
    expect(row?.value).toBe("10 次");
    expect(row?.detail).toContain("冷却 3");
    expect(row?.detail).toContain("预算 7");
  });
});

describe("describeBackstop", () => {
  it("已核实且全覆盖 ⇒ 绿色，且只有这一档敢说「进程退出后仍生效」", () => {
    const row = describeBackstop(backstop());
    expect(row?.tone).toBe("long");
    expect(row?.detail).toContain("进程退出后仍生效");
    expect(row?.detail).toContain("42s 前核实");
  });

  it("mismatch ⇒ 红色，且明说此刻没有任何持久化兜底", () => {
    const row = describeBackstop(backstop({ hasStop: false, coverageRatio: 0, mismatch: true }));
    expect(row?.tone).toBe("short");
    expect(row?.detail).toContain("没有任何持久化兜底");
  });

  it("覆盖不足（有数且低于阈）⇒ 红色", () => {
    expect(describeBackstop(backstop({ coverageRatio: 2 / 3 }))?.tone).toBe("short");
  });

  it("覆盖未知（null）与 0 是两个结论：前者灰色、绝不说「没有兜底」", () => {
    const unknown = describeBackstop(backstop({ coverageRatio: null, coveredSz: null }));
    expect(unknown?.tone).toBe("muted");
    expect(unknown?.value).toContain("覆盖未知");
    expect(unknown?.detail).toContain("无法确认");
    expect(unknown?.detail).not.toContain("没有兜底");
    const naked = describeBackstop(backstop({ hasStop: false, coverageRatio: 0, mismatch: true }));
    expect(naked?.tone).not.toBe(unknown?.tone);
  });

  it("local / unavailable 一律灰色（黄色会被读成「快好了」），并如实给出原因", () => {
    for (const wire of [
      backstop({ basis: "local", unavailableReason: "not_run", stopLossPx: null }),
      backstop({ basis: "unavailable", unavailableReason: "no_creds", stopLossPx: null }),
      backstop({ basis: "unavailable", unavailableReason: "stale", stopLossPx: null }),
    ]) {
      const row = describeBackstop(wire);
      expect(row?.tone).toBe("muted");
      expect(row?.value).toBe("未核实");
      expect(row?.detail).toContain("不能据此假定");
    }
    expect(
      describeBackstop(backstop({ basis: "unavailable", unavailableReason: "no_creds" }))?.detail,
    ).toContain("未接入交易所凭证");
  });

  it("键缺席 ⇒ null ⇒ 整块不渲染（历史页据此不显示一个假的「已核实」绿勾）", () => {
    expect(describeBackstop(null)).toBeNull();
    expect(describeBackstop(undefined)).toBeNull();
  });
});

describe("severity 与 chip", () => {
  it("最紧要优先：mismatch > 覆盖不足 > 冷却中 > 预算耗尽 > 正常", () => {
    const order = [
      turnoverSeverity(turnover(), backstop({ mismatch: true, hasStop: false })),
      turnoverSeverity(turnover(), backstop({ coverageRatio: 0.5 })),
      turnoverSeverity(turnover({ cooldownActive: true }), backstop()),
      turnoverSeverity(turnover({ tokens: 0 }), backstop()),
      turnoverSeverity(turnover(), backstop()),
    ];
    expect(order).toEqual([...order].sort((a, b) => a - b));
    expect(new Set(order).size).toBe(5);
  });

  it("正常态 chip ⇒ null ⇒ 不占位；异常态只显示最紧要的一格", () => {
    expect(describeTurnoverChip(turnover(), backstop())).toBeNull();
    expect(
      describeTurnoverChip(turnover({ cooldownActive: true }), backstop({ mismatch: true, hasStop: false }))
        ?.label,
    ).toContain("核实为未挂");
    const budget = describeTurnoverChip(turnover({ tokens: 0, nextTokenSeconds: 2520 }), backstop());
    expect(budget?.label).toContain("换手预算已用尽");
    expect(budget?.tone).toBe("short");
  });

  it("覆盖未知不进 chip——它不是坏消息，不该抢占最紧要的那一格", () => {
    expect(describeTurnoverChip(turnover(), backstop({ coverageRatio: null }))).toBeNull();
  });
});

describe("降级码中文化", () => {
  it("三个新前缀已登记（漏加不会报错、只会静默显示原始码）", () => {
    expect(describeDegradeReason("horizon_reentry_cooldown(1800s,long)")).toContain("再入场冷却");
    expect(describeDegradeReason("horizon_turnover_budget(0.4/3,next=42m)")).toContain(
      "换手预算已用尽",
    );
    expect(describeDegradeReason("horizon_backstop_uncovered(62%)")).toContain("交易所兜底未覆盖");
  });

  it("参数后缀原样保留，供排查", () => {
    expect(describeDegradeReason("horizon_turnover_budget(0.4/3,next=42m)")).toContain(
      "(0.4/3,next=42m)",
    );
    expect(
      describeDegradeReason("horizon_reentry_cooldown(1800s,long,reverse→close)"),
    ).toContain("reverse→close");
  });

  it("文案必须写清只拦「开」——用户看到「拦截」时最怕的是仓位也平不掉", () => {
    for (const code of [
      "horizon_reentry_cooldown(1800s,long)",
      "horizon_turnover_budget(0/3,next=42m)",
      "horizon_backstop_uncovered(62%)",
    ]) {
      expect(describeDegradeReason(code)).toMatch(/不受影响/);
    }
  });
});
