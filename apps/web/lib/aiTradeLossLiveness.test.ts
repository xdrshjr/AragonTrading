import { beforeEach, describe, expect, it } from "vitest";
import { AI_TRADE_PAUSE_REASON_TEXT, describeTradeReason } from "./aiTrade";
import { funnelStages, isFunnelClosed } from "./aiTradeFunnel";
import { describeHorizonRelease, horizonCoherenceHints } from "./aiTradeHorizon";
import { useAppStore } from "./store";
import type { AiTradeHorizonProfile, AiTradeOpenFunnel } from "./aiTradeTypes";

/**
 * 亏损根因与活性加固的前端纯函数用例（ai-trade-loss-and-liveness-hardening §八）。
 *
 * 三条**双向**用例是本文件的重点（它们守的是「老数据也要能读」）：
 * `G-E3` 新旧两种成交行都渲染得出可读文案；`E-7` 老后端缺 `expired` 键时漏斗逐字回退；
 * `A-2` 的两处剥离（写侧 + v31 迁移）缺一不可。
 */

// ============================ G-E3：新旧两种成交行都可读 ============================
describe("describeTradeReason（E-1 的唯一跨端契约变更）", () => {
  it("新行：机器码 + headline ⇒ 取 headline 的 prose", () => {
    expect(
      describeTradeReason({
        reason: "agent_close",
        explain: { headline: "1H 中轨失守且量能连续两根放大，持仓理由已失效" },
      }),
    ).toBe("1H 中轨失守且量能连续两根放大，持仓理由已失效");
  });

  it("新行但没有 headline ⇒ 机器码经标签表中文化，绝不显示裸英文码", () => {
    expect(describeTradeReason({ reason: "agent_close", explain: null })).toBe("AI 决策平仓");
    expect(describeTradeReason({ reason: "open_long", explain: null })).toBe("AI 决策开多");
    expect(describeTradeReason({ reason: "open_short", explain: null })).toBe("AI 决策开空");
  });

  it("老行：64 字截断的中文 prose、无 headline ⇒ 原样渲染（它本身就是可读的）", () => {
    // 这正是生产里那 221 笔——长度恰好 64、半句话、没有 explain.headline 的历史行。
    const truncated = "多头趋势延续，MA 通道上轨附近回踩确认，量能同步放大，且资金费率维持中";
    expect(describeTradeReason({ reason: truncated, explain: null })).toBe(truncated);
  });

  it("既有五个短码之一（保护层平仓）仍按前缀命中中文标签", () => {
    expect(describeTradeReason({ reason: "profit_guard_trailing", explain: null })).toContain(
      "利润保护",
    );
  });

  it("三者皆空 ⇒ null（调用方渲染破折号，绝不编一句话）", () => {
    expect(describeTradeReason({ reason: "", explain: null })).toBeNull();
    expect(describeTradeReason({})).toBeNull();
  });
});

// ============================ E-7 / E-8：漏斗末格 ============================
function funnel(over: Partial<AiTradeOpenFunnel> = {}): AiTradeOpenFunnel {
  return {
    intents: 20,
    blocked: { consensus: 8 },
    topBlocker: { key: "consensus", label: "同向共识门槛", count: 8 },
    llmDegraded: 0,
    sizingZeroed: 0,
    submitted: 12,
    filled: 5,
    conversion: 0.25,
    layers: ["consensus"],
    scope: "本会话全程累计",
    ...over,
  };
}

describe("开仓漏斗的两个新末格", () => {
  it("后端下发 expired ⇒ 多出一格，且与「真的成交」互斥并列", () => {
    const stages = funnelStages(funnel({ expired: 7 }));
    const keys = stages.map((s) => s.key);
    expect(keys).toEqual(["intents", "blocked", "sizingZeroed", "submitted", "filled", "expired"]);
    expect(stages.at(-1)!.count).toBe(7);
  });

  it("老后端缺该键 ⇒ 整格不渲染（逐字回退），**绝不画一个 0**", () => {
    const stages = funnelStages(funnel());
    expect(stages.map((s) => s.key)).not.toContain("expired");
  });

  it("闭合不变式不受影响：expired 排在「委托」之后，不进 blocked", () => {
    // 合入前的不变式是 `Σblocked + submitted + sizingZeroed ≤ intents`；新格不参与它。
    expect(isFunnelClosed(funnel({ expired: 999 }))).toBe(true);
  });
});

// ============================ 轴 B / C：文案与相干性提示 ============================
describe("轴 B / C 的前端读点", () => {
  it("新豁免码有中文标签，且**写明「不解最短持仓」**（只说「限制已解除」会误导）", () => {
    const text = describeHorizonRelease("loss_release_covered");
    expect(text).toContain("交易所止损");
    expect(text).toContain("不解最短持仓");
  });

  it("未知码原样回退（与 describeDegradeReason 同款极性，绝不抛）", () => {
    expect(describeHorizonRelease("brand_new_code")).toBe("brand_new_code");
    expect(describeHorizonRelease(null)).toBe("");
  });

  const profile = {
    id: "intraday_swing",
    title: "日内波段",
    bar: "1H",
    minHoldSeconds: 7200,
    targetHoldMinSeconds: 14400,
    leverageMaxHint: null,
  } as unknown as AiTradeHorizonProfile;

  it("divisor > 0 且唤醒短于下限 ⇒ 提示会被抬到多少（数值按下发值现算）", () => {
    const hints = horizonCoherenceHints({
      profile,
      bar: "1H",
      wakeMinutes: 3,
      leverage: null,
      wakeFloorDivisor: 24,
    });
    expect(hints.join("|")).toContain("10.0 分钟");
    expect(hints.join("|")).toContain("目标最短持仓 / 24");
  });

  it("divisor = 0（本轮默认）⇒ **不说那句话**——服务端根本没开这条抬升", () => {
    const hints = horizonCoherenceHints({
      profile,
      bar: "1H",
      wakeMinutes: 3,
      leverage: null,
      wakeFloorDivisor: 0,
    });
    expect(hints.join("|")).not.toContain("自动抬到");
  });
});

// ============================ 轴 D：暂停原因四镜像的前端那一份 ============================
describe("暂停原因文案表", () => {
  it("补齐了 liveness_blocked 与此前漏掉的 resume_rollback", () => {
    expect(AI_TRADE_PAUSE_REASON_TEXT.liveness_blocked).toContain("自动停手");
    expect(AI_TRADE_PAUSE_REASON_TEXT.liveness_blocked).toContain("仓位保留");
    expect(AI_TRADE_PAUSE_REASON_TEXT.resume_rollback).toBeTruthy();
  });
});

// ============================ A-2：利润保护偏好非粘性（两处剥离） ============================
describe("A-2：利润保护的开关不进持久化偏好", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it("写侧剥离：setAiTradeConfig 存进去的 profitGuard 不带 enabled", () => {
    useAppStore.getState().setAiTradeConfig({
      contextRefreshRounds: 10,
      profitGuard: { enabled: false, armReturnPct: 0.05 },
    } as never);
    const stored = useAppStore.getState().aiTradeConfig!;
    expect(stored.profitGuard).toEqual({ armReturnPct: 0.05 });
    expect("enabled" in (stored.profitGuard as object)).toBe(false);
  });

  it("v31 迁移：把**已经落盘**的 enabled:false 剥掉（只做写侧救不了那 193 份偏好）", async () => {
    localStorage.setItem(
      "aragon-ui",
      JSON.stringify({
        state: {
          aiTradeConfig: {
            contextRefreshRounds: 10,
            profitGuard: { enabled: false, armReturnPct: 0.05, minHoldSeconds: 30 },
          },
        },
        version: 30,
      }),
    );
    await useAppStore.persist.rehydrate();
    const cfg = useAppStore.getState().aiTradeConfig!;
    expect(cfg.profitGuard).toEqual({ armReturnPct: 0.05, minHoldSeconds: 30 });
    expect(cfg.profitGuard?.enabled).toBeUndefined();
  });

  it("aiTradeConfig 为 null 时迁移**不得抛**（对 null 取属性赋值会直接崩掉水合）", async () => {
    localStorage.setItem(
      "aragon-ui",
      JSON.stringify({ state: { aiTradeConfig: null, bar: "4H" }, version: 30 }),
    );
    await expect(useAppStore.persist.rehydrate()).resolves.not.toThrow();
    expect(useAppStore.getState().aiTradeConfig).toBeNull();
    expect(useAppStore.getState().bar).toBe("4H");
  });

  it("setAiTradeConfig(null) 与无 profitGuard 的配置都原样通过", () => {
    useAppStore.getState().setAiTradeConfig(null);
    expect(useAppStore.getState().aiTradeConfig).toBeNull();
    useAppStore.getState().setAiTradeConfig({ contextRefreshRounds: 10 } as never);
    expect(useAppStore.getState().aiTradeConfig).toEqual({ contextRefreshRounds: 10 });
  });
});
