import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { AiTradeFeedRecovery, AiTradeGroupMemberState } from "@/lib/aiTradeTypes";
import { AiTradeFeedRecoveryCard } from "./AiTradeFeedRecoveryCard";

/**
 * 行情自愈恢复卡（ai-trade-feed-self-healing 轴 D · §7.5）。
 *
 * 本文件守四条：
 *  ① 键缺席与 `null` 都不渲染，**连外框都不渲染**（常态多一行只会稀释真正要紧的信息）；
 *  ② 多标的逐行点名；
 *  ③ `nextAttemptInS === null` ⇒ 不对着一个不会到来的时刻读秒；
 *  ④ pending 状态机：点击后立即禁用，回执到达前不得二次点击。
 */
const relinkMember = vi.fn();
const stopMember = vi.fn();

vi.mock("@/lib/aiTradeApi", () => ({
  aiTradeApi: {
    relinkMember: (...args: unknown[]) => relinkMember(...args),
    stopMember: (...args: unknown[]) => stopMember(...args),
  },
}));

const WIRE: AiTradeFeedRecovery = {
  state: "recovering",
  stage: "relink",
  attempts: { backfill: 17, relink: 2 },
  lastAttemptTs: 1_756_270_000,
  nextAttemptInS: 38,
  lastOutcome: "no_data",
  lastOutcomeText: "REST 未取到更新的 K 线",
  source: "ws",
  recoveryText: "正在自动恢复行情连接 · 已重连 2 次 · 下一次尝试 38 秒后",
};

const STUCK: AiTradeFeedRecovery = {
  ...WIRE,
  state: "stuck",
  stage: "escalated",
  nextAttemptInS: null,
  attempts: { backfill: 40, relink: 3 },
  recoveryText:
    "自动恢复已尝试 3 次仍未拿到新 K 线（20 小时 59 分钟无数据）。" +
    "交易所侧止盈止损仍生效；该标的的 Agent 决策已停摆。",
};

function member(instId: string, feedRecovery?: AiTradeFeedRecovery | null) {
  return {
    instId,
    sessionId: `s-${instId}`,
    allocationPct: 50,
    session: feedRecovery === undefined ? {} : { lifecycle: { feedRecovery } },
    trades: [],
    position: null,
    decisions: [],
    traces: [],
    summary: null,
  } as unknown as AiTradeGroupMemberState;
}

function renderCard(members: AiTradeGroupMemberState[]) {
  return render(<AiTradeFeedRecoveryCard groupId="grp-1" members={members} />);
}

afterEach(() => {
  cleanup();
  relinkMember.mockReset();
  stopMember.mockReset();
});

describe("恢复卡", () => {
  it("键缺席或值为 null ⇒ 整块不渲染，不占位", () => {
    renderCard([member("BTC-USDT-SWAP"), member("ETH-USDT-SWAP", null)]);
    expect(screen.queryByTestId("ai-trade-feed-recovery-card")).toBeNull();
  });

  it("正在自愈 ⇒ 渲染后端下发的那句话，一个字都不重写", () => {
    renderCard([member("BTC-USDT-SWAP", WIRE)]);
    const card = screen.getByTestId("ai-trade-feed-recovery-card");
    expect(card).toHaveTextContent("正在自动恢复行情连接");
    expect(card).toHaveTextContent("已重连 2 次");
    expect(card).toHaveTextContent("下一次尝试 38 秒后");
    expect(card.textContent).not.toContain("**");
  });

  it("多标的逐行点名；单标的不带前缀", () => {
    renderCard([member("BTC-USDT-SWAP", null), member("ETH-USDT-SWAP", WIRE)]);
    expect(screen.getByTestId("ai-trade-feed-recovery-row")).toHaveTextContent(
      "ETH-USDT-SWAP：",
    );
    cleanup();
    renderCard([member("BTC-USDT-SWAP", WIRE)]);
    expect(screen.getByTestId("ai-trade-feed-recovery-row").textContent).not.toContain("：正在");
  });

  it("`nextAttemptInS === null` ⇒ 不渲染倒计时进度条", () => {
    renderCard([member("BTC-USDT-SWAP", { ...WIRE, nextAttemptInS: null })]);
    expect(screen.queryByTestId("ai-trade-feed-recovery-progress")).toBeNull();
    cleanup();
    renderCard([member("BTC-USDT-SWAP", WIRE)]);
    expect(screen.getByTestId("ai-trade-feed-recovery-progress")).toBeInTheDocument();
  });

  it("`stuck` ⇒ 出现【仅停止该标的】；`recovering` ⇒ 只有【立即重连】", () => {
    renderCard([member("BTC-USDT-SWAP", STUCK)]);
    expect(screen.getByTestId("ai-trade-feed-relink")).toBeInTheDocument();
    expect(screen.getByTestId("ai-trade-feed-stop-member")).toBeInTheDocument();
    cleanup();
    renderCard([member("BTC-USDT-SWAP", WIRE)]);
    expect(screen.queryByTestId("ai-trade-feed-stop-member")).toBeNull();
  });

  it("`stuck` 的文案说清「交易所侧止盈止损仍生效」与「Agent 决策已停摆」", () => {
    renderCard([member("BTC-USDT-SWAP", STUCK)]);
    const card = screen.getByTestId("ai-trade-feed-recovery-card");
    expect(card).toHaveTextContent("交易所侧止盈止损仍生效");
    expect(card).toHaveTextContent("Agent 决策已停摆");
  });

  it("REST 生命维持期间如实说出来（用户必须知道自己跑在什么上面）", () => {
    renderCard([
      member("BTC-USDT-SWAP", {
        ...WIRE,
        source: "rest",
        recoveryText: "正在自动恢复行情连接 · 已重连 2 次。当前 K 线由 REST 补取维持，实时通道尚未恢复",
      }),
    ]);
    expect(screen.getByTestId("ai-trade-feed-recovery-card")).toHaveTextContent(
      "当前 K 线由 REST 补取维持",
    );
  });

  it("点击【立即重连】后按钮禁用且文案变化，回执到达前不得二次点击", async () => {
    // `let x!: T` 而不是 `| null = null`：后者会被 TS 的控制流窄化成 `never`（它看不到执行器
    // 已经跑过），于是 `resolve?.(...)` 报 "Type 'never' has no call signatures"。
    let resolve!: (value: unknown) => void;
    relinkMember.mockReturnValue(
      new Promise<unknown>((r) => {
        resolve = r;
      }),
    );
    renderCard([member("BTC-USDT-SWAP", STUCK)]);
    const button = screen.getByTestId("ai-trade-feed-relink");

    fireEvent.click(button);
    expect(relinkMember).toHaveBeenCalledTimes(1);
    expect(relinkMember).toHaveBeenCalledWith("grp-1", "BTC-USDT-SWAP");
    expect(screen.getByTestId("ai-trade-feed-relink")).toBeDisabled();
    expect(screen.getByTestId("ai-trade-feed-relink")).toHaveTextContent("正在重连…");

    fireEvent.click(screen.getByTestId("ai-trade-feed-relink"));
    expect(relinkMember).toHaveBeenCalledTimes(1);   // 二次点击被 pending 挡住

    resolve({ ok: true, instId: "BTC-USDT-SWAP", message: "已重建行情连接并补齐 3 根 K 线" });
    await screen.findByText("已重建行情连接并补齐 3 根 K 线");
    expect(screen.getByTestId("ai-trade-feed-relink")).not.toBeDisabled();
  });

  it("失败回执如实回显（绝不静默）", async () => {
    relinkMember.mockRejectedValue(new Error("boom"));
    renderCard([member("BTC-USDT-SWAP", STUCK)]);
    fireEvent.click(screen.getByTestId("ai-trade-feed-relink"));
    await screen.findByText("重连请求失败，请检查网络 / 代理后重试");
  });

  it("恢复卡是 `status` 而不是 `alert`（同屏两个 alert 会被读两遍）", () => {
    renderCard([member("BTC-USDT-SWAP", STUCK)]);
    const card = screen.getByTestId("ai-trade-feed-recovery-card");
    expect(card).toHaveAttribute("role", "status");
    expect(card).toHaveAttribute("aria-live", "polite");
  });
});
