import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import AiTradeLivenessBanner from "./AiTradeLivenessBanner";
import ExitSourceCard from "./aiTradeStats/ExitSourceCard";
import type { AiTradeExitMix, AiTradeGroupMemberState, AiTradeLiveness } from "@/lib/aiTradeTypes";

/**
 * 活性横幅 + 退出裁量权结论行（ai-trade-loss-and-liveness-hardening 轴 D / 轴 F）。
 *
 * 两条呈现纪律由本文件钉死：
 * ① **判定性文案一个字都不在前端拼**——`message` / `advice` / `note` 全部原样来自后端；
 * ② **逐标的呈现，绝不整组与门**：三个标的里只有一个卡住时，另外两个不该被说成卡住。
 */
afterEach(cleanup);

function member(instId: string, liveness: AiTradeLiveness | null): AiTradeGroupMemberState {
  return {
    instId,
    sessionId: `s-${instId}`,
    allocationPct: 50,
    session: liveness ? ({ instId, liveness } as never) : ({ instId } as never),
    trades: [],
    position: null,
    profitGuardCooldown: null,
    decisions: [],
    traces: [],
    summary: null,
  } as unknown as AiTradeGroupMemberState;
}

const DEGRADED: AiTradeLiveness = {
  level: "degraded",
  code: "ma_band_unavailable",
  streak: 23,
  sinceWall: 1_788_012_345,
  message: "已连续 23 轮因「均线通道数据不足，无法验证开单位置」无法开仓；期间无任何成交。",
  advice: "检查行情连接是否恢复；或在启动弹窗里关闭【基础策略】MA 通道约束后重开会话。",
};

const HALT: AiTradeLiveness = {
  ...DEGRADED,
  level: "halt",
  streak: 61,
  message: "已连续 61 轮无法开仓；期间无任何成交。将暂停整个分组（含其余标的），仓位一律保留。",
};

describe("AiTradeLivenessBanner", () => {
  it("总闸关 / 老后端 ⇒ 成员行整键缺席 ⇒ 整段不渲染", () => {
    const { container } = render(<AiTradeLivenessBanner members={[member("BTC-USDT-SWAP", null)]} />);
    expect(container.firstChild).toBeNull();
  });

  it("`ok` 档不渲染横幅（「现在一切正常」不需要一条告警）", () => {
    const ok: AiTradeLiveness = { ...DEGRADED, level: "ok", code: null, streak: 0, message: "" };
    const { container } = render(<AiTradeLivenessBanner members={[member("BTC-USDT-SWAP", ok)]} />);
    expect(container.firstChild).toBeNull();
  });

  it("degraded ⇒ 逐字渲染后端下发的 message 与 advice（前端不拼任何判定性文案）", () => {
    render(<AiTradeLivenessBanner members={[member("BTC-USDT-SWAP", DEGRADED)]} />);
    expect(screen.getByTestId("ai-liveness-row-BTC-USDT-SWAP")).toHaveAttribute(
      "data-level",
      "degraded",
    );
    expect(screen.getByText(DEGRADED.message)).toBeInTheDocument();
    expect(screen.getByText(DEGRADED.advice)).toBeInTheDocument();
  });

  it("halt ⇒ 文案里**逐字**说明将暂停整组（§九 R11 ②：不让用户事后才发现）", () => {
    render(<AiTradeLivenessBanner members={[member("BTC-USDT-SWAP", HALT)]} />);
    expect(screen.getByTestId("ai-liveness-row-BTC-USDT-SWAP")).toHaveAttribute(
      "data-level",
      "halt",
    );
    expect(screen.getByText(HALT.message)).toBeInTheDocument();
    // 后端文案里的 `**` 在 JSX 文本节点里**没有任何渲染器**，用户看到的就是两侧的星号。
    // 本仓库已为这条栽过三次（`hold_horizon_text`、情报层、本轮的 `_HALT_SCOPE_TEXT`），
    // 故在横幅这一侧也留一道门禁：强调只能靠措辞，不能靠标记。
    expect(screen.getByTestId("ai-liveness-row-BTC-USDT-SWAP").textContent).not.toContain("**");
  });

  it("**逐标的**呈现：只有一个卡住时，另外那个不出现在横幅里", () => {
    render(
      <AiTradeLivenessBanner
        members={[member("BTC-USDT-SWAP", DEGRADED), member("ETH-USDT-SWAP", null)]}
      />,
    );
    expect(screen.getByTestId("ai-liveness-row-BTC-USDT-SWAP")).toBeInTheDocument();
    expect(screen.queryByTestId("ai-liveness-row-ETH-USDT-SWAP")).toBeNull();
  });
});

// ============================ 轴 F：退出裁量权结论行 ============================
const MIX: AiTradeExitMix = {
  totalExits: 43,
  bySource: [
    { source: "agent", count: 36, share: 0.84, netUsd: -67.524, avgHoldSeconds: 2140, winRate: 0.27 },
    { source: "profitGuard", count: 7, share: 0.16, netUsd: 14.136, avgHoldSeconds: 9820, winRate: null },
  ],
};

describe("ExitSourceCard · exitDiscretion（轴 F）", () => {
  it("后端未下发结论（min_samples=0 或老后端）⇒ 结论行整段不渲染", () => {
    render(<ExitSourceCard exits={MIX} />);
    expect(screen.queryByTestId("ai-exit-discretion")).toBeNull();
  });

  it("adverse ⇒ 渲染后端下发的整句 + 一个能真的去改的入口", () => {
    const calls: string[] = [];
    render(
      <ExitSourceCard
        exits={{
          ...MIX,
          exitDiscretion: {
            level: "adverse",
            agentTrips: 36,
            agentNetUsd: -67.52,
            minSamples: 20,
            bestDeterministic: { source: "profitGuard", trips: 7, netUsd: 14.14 },
            note: "模型自主平仓 36 笔合计 -67.52 USDT；profitGuard 平仓 7 笔合计 +14.14 USDT。",
          },
        }}
        onOpenProfitGuard={() => calls.push("open")}
      />,
    );
    const row = screen.getByTestId("ai-exit-discretion");
    expect(row).toHaveAttribute("data-level", "adverse");
    expect(row).toHaveTextContent("36 笔合计 -67.52 USDT");
    screen.getByTestId("ai-exit-discretion-cta").click();
    expect(calls).toEqual(["open"]);
  });

  it("insufficient ⇒ 如实写「样本 M/N，结论未生效」且**不给按钮**（结论都没生效，别催用户去改）", () => {
    render(
      <ExitSourceCard
        exits={{
          ...MIX,
          exitDiscretion: {
            level: "insufficient",
            agentTrips: 12,
            agentNetUsd: -5,
            minSamples: 20,
            bestDeterministic: null,
            note: "样本 12/20，结论未生效。",
          },
        }}
        onOpenProfitGuard={() => undefined}
      />,
    );
    expect(screen.getByTestId("ai-exit-discretion")).toHaveTextContent("样本 12/20，结论未生效。");
    expect(screen.queryByTestId("ai-exit-discretion-cta")).toBeNull();
  });

  it("没有落点回调 ⇒ 只渲染结论、不渲染按钮（绝不给一个点了没反应的按钮）", () => {
    render(
      <ExitSourceCard
        exits={{
          ...MIX,
          exitDiscretion: {
            level: "adverse",
            agentTrips: 36,
            agentNetUsd: -67.52,
            minSamples: 20,
            bestDeterministic: { source: "profitGuard", trips: 7, netUsd: 14.14 },
            note: "n",
          },
        }}
      />,
    );
    expect(screen.getByTestId("ai-exit-discretion")).toBeInTheDocument();
    expect(screen.queryByTestId("ai-exit-discretion-cta")).toBeNull();
  });
});
