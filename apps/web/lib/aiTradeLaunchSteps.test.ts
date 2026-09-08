import { describe, expect, it } from "vitest";
import {
  AI_TRADE_LAUNCH_ERROR_KEYS,
  AI_TRADE_LAUNCH_STEPS,
  BANNER_INPUTS,
  READY_INPUTS,
  STEP_ERROR_OWNERS,
  firstErroredStep,
  inputStepsClean,
  stepError,
  stepSummary,
  stepsFor,
  type AiTradeLaunchStepId,
  type AiTradeLaunchSummaryInput,
} from "./aiTradeLaunchSteps";

/**
 * 步骤模型的纯函数用例（ai-trade-guided-launch §8.1 U-1 / U-2 / U-7）。
 *
 * 这里守的是分步化真正的危险面：**某个校验没有归属**——它照旧拦住启动按钮，但没有任何一步的
 * 「下一步」会被它挡住，用户走到第 6 步才被告知第 3 步填错了。
 */

const SUMMARY: AiTradeLaunchSummaryInput = {
  mode: "live",
  memberCount: 3,
  instId: "BTC-USDT-SWAP",
  strategyCount: 4,
  bar: "1H",
  horizonLabel: "日内波段",
  wakeText: "唤醒 30 分",
  decisionEveryText: "每 4 根",
  budgetText: "800.00 USDT",
  leverageText: "3×",
  positionStepText: "25%",
  reasoningDepthLabel: "标准",
  experienceEnabled: true,
  intelEnabled: false,
  disciplineLabel: "均衡",
  baseRuleEnabled: true,
  profitGuardEnabled: true,
  riskCount: 2,
};

describe("U-1 步骤序列冻结", () => {
  it("两种模式的 id 序列都恰好是那六步", () => {
    const expected: AiTradeLaunchStepId[] = [
      "scope",
      "rhythm",
      "capital",
      "cognition",
      "guards",
      "review",
    ];
    expect(stepsFor("live").map((s) => s.id)).toEqual(expected);
    expect(stepsFor("backtest").map((s) => s.id)).toEqual(expected);
    expect(AI_TRADE_LAUNCH_STEPS).toHaveLength(6);
  });

  it("每一步都有标题与「这一步在回答什么问题」，且没有重复标题", () => {
    const titles = AI_TRADE_LAUNCH_STEPS.map((s) => s.title);
    expect(new Set(titles).size).toBe(titles.length);
    for (const step of AI_TRADE_LAUNCH_STEPS) {
      expect(step.title.trim().length).toBeGreaterThan(0);
      expect(step.question.trim().length).toBeGreaterThan(0);
    }
  });
});

describe("U-2 校验归属表：全覆盖 + 无重叠", () => {
  it("归属表的并集 === READY_INPUTS ∪ BANNER_INPUTS", () => {
    // 【v2 · P0-3】参照集**不是** `firstError` 单独一份：`directionError` / `trendError` 只进
    // `ready`，照那个字面施工会让它们没有任何步骤归属。
    const reference = new Set<string>([...READY_INPUTS, ...BANNER_INPUTS]);
    const owned = new Set<string>(Object.keys(STEP_ERROR_OWNERS));
    expect([...owned].sort()).toEqual([...reference].sort());
    expect([...owned].sort()).toEqual([...AI_TRADE_LAUNCH_ERROR_KEYS].sort());
  });

  it("没有任何一项归属两步（每个 key 恰好一个 owner）", () => {
    const seen = new Map<string, string>();
    for (const key of AI_TRADE_LAUNCH_ERROR_KEYS) {
      const owner = STEP_ERROR_OWNERS[key];
      expect(owner, `${key} 没有归属`).toBeTruthy();
      expect(seen.has(key)).toBe(false);
      seen.set(key, owner.step);
    }
    expect(seen.size).toBe(AI_TRADE_LAUNCH_ERROR_KEYS.length);
  });

  it("`directionError` / `trendError` 归属 `guards`（v1 参照集漏掉的那两项）", () => {
    expect(STEP_ERROR_OWNERS.directionError.step).toBe("guards");
    expect(STEP_ERROR_OWNERS.trendError.step).toBe("guards");
    // 它们只在 READY_INPUTS 里，不在 BANNER_INPUTS 里——这正是它们容易被漏掉的原因。
    expect(READY_INPUTS).toContain("directionError");
    expect(BANNER_INPUTS).not.toContain("directionError");
    expect(READY_INPUTS).toContain("trendError");
    expect(BANNER_INPUTS).not.toContain("trendError");
  });

  it("`stepError` 只看当前模式下有效的那些", () => {
    const live = { mode: "live" as const, errors: { everyError: "回测才有的错" } };
    expect(stepError(live, "rhythm")).toBeNull();
    const backtest = { mode: "backtest" as const, errors: { everyError: "回测才有的错" } };
    expect(stepError(backtest, "rhythm")).toBe("回测才有的错");
    // live 段不看 instSupported（那是回测单标的的判据）
    const liveInst = { mode: "live" as const, errors: { instSupported: "标的不支持" } };
    expect(stepError(liveInst, "scope")).toBeNull();
  });

  it("`cognition` 永不产生阻断错误（设计结论，不是遗漏）", () => {
    const owners = Object.values(STEP_ERROR_OWNERS).map((o) => o.step);
    expect(owners).not.toContain("cognition");
  });

  it("`inputStepsClean` 只看第 1–5 步，`review` 步的错误不影响它（N-10 的判据）", () => {
    const model = {
      mode: "live" as const,
      errors: { acknowledged: "请先勾选「我已知晓这是真实资金 AI 自动交易」" },
    };
    expect(inputStepsClean(model)).toBe(true);
    expect(stepError(model, "review")).toBeTruthy();
    const dirty = { mode: "live" as const, errors: { strategyError: "请至少选择一个交易策略" } };
    expect(inputStepsClean(dirty)).toBe(false);
  });

  it("`firstErroredStep` 按步骤序返回第一个有错的步（review 兜底行的跳转目标）", () => {
    const model = {
      mode: "live" as const,
      errors: { directionError: "逆势置信度越界", inputError: "杠杆非法" },
    };
    expect(firstErroredStep(model)?.id).toBe("capital");
    const onlyDirection = { mode: "live" as const, errors: { directionError: "逆势置信度越界" } };
    expect(firstErroredStep(onlyDirection)?.id).toBe("guards");
    expect(firstErroredStep({ mode: "live", errors: {} })).toBeNull();
  });
});

describe("U-7 步骤摘要", () => {
  it("六步各产出非空且不含 undefined / NaN 字面量", () => {
    for (const mode of ["live", "backtest"] as const) {
      for (const step of stepsFor(mode)) {
        const text = stepSummary({ ...SUMMARY, mode }, step.id);
        expect(text.trim().length, `${mode}/${step.id} 摘要为空`).toBeGreaterThan(0);
        expect(text).not.toContain("undefined");
        expect(text).not.toContain("NaN");
      }
    }
  });

  it("空/非法输入回落成短横，绝不把 undefined 印到轨上", () => {
    const broken = {
      ...SUMMARY,
      horizonLabel: "",
      bar: String(undefined),
      budgetText: String(Number.NaN),
    };
    const rhythm = stepSummary(broken, "rhythm");
    expect(rhythm).not.toContain("undefined");
    expect(rhythm).toContain("—");
    expect(stepSummary(broken, "capital")).not.toContain("NaN");
  });

  it("实盘摘要说标的数、回测摘要说标的本身", () => {
    expect(stepSummary(SUMMARY, "scope")).toContain("3 个标的");
    expect(stepSummary({ ...SUMMARY, mode: "backtest" }, "scope")).toContain("BTC-USDT-SWAP");
  });

  it("`review` 步摘要如实报风险条数（0 条时说「无风险提示」而不是空串）", () => {
    expect(stepSummary(SUMMARY, "review")).toContain("2 条");
    expect(stepSummary({ ...SUMMARY, riskCount: 0 }, "review")).toBe("无风险提示");
  });
});
