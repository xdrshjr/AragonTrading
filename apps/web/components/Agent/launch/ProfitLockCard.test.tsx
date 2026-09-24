import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { afterEach, describe, expect, it } from "vitest";
import { collectRisks, type AiTradeLaunchRiskInput } from "@/lib/aiTradeLaunchRisk";
import { firstErroredStep, stepSummary, type AiTradeLaunchSummaryInput } from "@/lib/aiTradeLaunchSteps";
import type { AiTradeTradabilityResponse } from "@/lib/aiTradeTypes";
import ProfitLockCard from "./ProfitLockCard";
import type { UseAiTradeLaunchState } from "./useAiTradeLaunchState";
import { useProfitLockFields } from "./useProfitLockFields";

/**
 * 向导「收益锁」卡片（profit-lock C-2 / §7.4）：默认开；关闭后第 6 步出现 `profitLockOff`；
 * 高级字段非法 ⇒ 字段级提示 + 校验量归属第 5 步；预检未就绪只显示公式。
 */
afterEach(cleanup);

const RISK_BASE: AiTradeLaunchRiskInput = {
  mode: "live", gearNote: null, tradabilityLevel: "ok", tradabilityHeadline: null,
  profitGuardEnabled: true, profitLockEnabled: true, horizonEnabled: false,
  effectiveMinHoldSeconds: 0, interlockArmed: false, inertHardTakeProfitText: null,
  allocationOversubscribedPct: null, budgetClampText: null, truncatedFrom: null, maxSymbols: 5,
  reasoningDepth: "standard", wakeMinutes: 30, experienceEnabled: true, wakeFloorDivisor: 0,
  targetHoldMinSeconds: 0,
};

const SUMMARY: AiTradeLaunchSummaryInput = {
  mode: "live", memberCount: 1, instId: "BTC-USDT-SWAP", strategyCount: 1, bar: "30m",
  horizonLabel: "日内波段", wakeText: "唤醒 3 分", decisionEveryText: "每 1 根",
  budgetText: "默认预算", leverageText: "5×", positionStepText: "50%", reasoningDepthLabel: "深度",
  experienceEnabled: true, intelEnabled: false, disciplineLabel: "均衡", baseRuleEnabled: true,
  profitGuardEnabled: true, profitLockEnabled: true, riskCount: 0,
};

const PROBE = {
  costBps: { market: 10, limit: 7 },
  atrBps: { p10: 20, p50: 40, p90: 80 },
} as unknown as AiTradeTradabilityResponse;

function Harness({ probe }: { probe: AiTradeTradabilityResponse | null }) {
  const [profitLockEnabled, setProfitLockEnabled] = useState(true);
  const [lockArmCostRaw, setLockArmCostRaw] = useState("");
  const [lockKeepRatioRaw, setLockKeepRatioRaw] = useState("");
  const [lockConfirmRaw, setLockConfirmRaw] = useState("");
  const state = {
    profitLockEnabled, setProfitLockEnabled, lockArmCostRaw, setLockArmCostRaw,
    lockKeepRatioRaw, setLockKeepRatioRaw, lockConfirmRaw, setLockConfirmRaw,
  } as unknown as UseAiTradeLaunchState;
  const view = useProfitLockFields(state);
  const risks = collectRisks({ ...RISK_BASE, profitLockEnabled });
  const blocking = firstErroredStep({ mode: "live", errors: { profitLockError: view.error } });
  return (
    <>
      <ProfitLockCard state={state} view={view} probe={probe} mode="live" disabled={false} />
      <span data-testid="payload">{JSON.stringify(view.payload)}</span>
      <span data-testid="risks">{risks.map((r) => r.id).join(",")}</span>
      <span data-testid="blocking">{blocking?.id ?? "none"}</span>
      <span data-testid="summary">
        {stepSummary({ ...SUMMARY, profitLockEnabled }, "guards")}
      </span>
    </>
  );
}

describe("ProfitLockCard", () => {
  it("默认开：提交体只带 enabled，不越权替服务端填默认值；摘要含「收益锁开」", () => {
    render(<Harness probe={PROBE} />);
    expect(screen.getByLabelText("收益锁（推荐）")).toBeChecked();
    expect(screen.getByTestId("payload").textContent).toBe('{"enabled":true}');
    expect(screen.getByTestId("risks").textContent).toBe("");
    expect(screen.getByTestId("summary").textContent).toContain("收益锁开");
  });

  it("关闭 ⇒ 提交 {enabled:false}，第 6 步风险清单出现 profitLockOff，摘要变「收益锁关」", () => {
    render(<Harness probe={PROBE} />);
    fireEvent.click(screen.getByLabelText("收益锁（推荐）"));
    expect(screen.getByTestId("payload").textContent).toBe('{"enabled":false}');
    expect(screen.getByTestId("risks").textContent).toBe("profitLockOff");
    expect(screen.getByTestId("summary").textContent).toContain("收益锁关");
  });

  it("keepRatio=1.2 ⇒ 字段级提示，且校验量把第 5 步（guards）挡住", () => {
    render(<Harness probe={PROBE} />);
    fireEvent.change(screen.getByTestId("ai-profit-lock-keepRatio"), { target: { value: "1.2" } });
    expect(screen.getByRole("alert")).toHaveTextContent("利润锁保留比例须在 (0, 0.95] 之间");
    expect(screen.getByTestId("blocking").textContent).toBe("guards");
  });

  it("保本锁倍数越过利润锁倍数 ⇒ 前端就地拦住（否则服务端 422）", () => {
    render(<Harness probe={PROBE} />);
    fireEvent.change(screen.getByTestId("ai-profit-lock-armCostMult"), { target: { value: "4" } });
    expect(screen.getByRole("alert")).toHaveTextContent("须 ≤ 利润锁武装倍数 3");
  });

  it("合法的高级字段只提交改过的那一个", () => {
    render(<Harness probe={PROBE} />);
    fireEvent.change(screen.getByTestId("ai-profit-lock-confirmSeconds"), { target: { value: "5" } });
    expect(screen.getByTestId("payload").textContent).toBe('{"enabled":true,"confirmSeconds":5}');
    expect(screen.getByTestId("blocking").textContent).toBe("none");
  });

  it("预检就绪 ⇒ 用真实成本与 ATR 预估；未就绪 ⇒ 只显示公式、不编数字", () => {
    render(<Harness probe={PROBE} />);
    expect(screen.getByTestId("ai-profit-lock-preview")).toHaveTextContent(
      "按当前成本约 10 bps、ATR 约 40 bps：浮盈到 15 bps 时保本，到 32 bps 时开始锁利。",
    );
    cleanup();
    render(<Harness probe={null} />);
    expect(screen.queryByTestId("ai-profit-lock-preview")).toBeNull();
    expect(screen.getByTestId("ai-profit-lock-formula")).toBeInTheDocument();
  });

  it("正文不含 Markdown 星号或块注释字面量", () => {
    const { container } = render(<Harness probe={PROBE} />);
    const text = container.querySelector("[data-testid=ai-profit-lock-card]")?.textContent ?? "";
    expect(text).not.toContain("**");
    expect(text).not.toContain("/*");
  });
});
