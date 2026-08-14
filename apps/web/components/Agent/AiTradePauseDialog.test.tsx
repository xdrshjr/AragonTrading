import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { aiTradeApi } from "@/lib/aiTradeApi";
import type { AiTradeGroupState } from "@/lib/aiTradeTypes";
import AiTradePauseDialog from "./AiTradePauseDialog";

vi.mock("@/lib/aiTradeApi", () => ({
  aiTradeApi: { repairBackstop: vi.fn() },
}));

/**
 * 暂停确认框的三条 HCI 契约（§3.9.3）：
 * - **默认就是可逆的那一个**（保留持仓暂停）；
 * - 带仓时把「关机期间谁在保护这个仓位」写在**确认之前**，兜底未覆盖时给「立即补挂」直连；
 * - 无仓时不展示那一段（一条与你无关的安全告警只会稀释真正的告警）。
 */
function makeGroup(over: { position?: unknown; backstopArmed?: boolean } = {}): AiTradeGroupState {
  return {
    groupId: "grp-1",
    bar: "1H",
    strategyIds: [1],
    strategyNames: ["S1"],
    totalBudgetUsd: 100,
    leverage: 3,
    contextRefreshRounds: 10,
    state: "running",
    startedTs: 0,
    members: {
      "BTC-USDT-SWAP": {
        instId: "BTC-USDT-SWAP",
        sessionId: "sess-1",
        allocationPct: 100,
        session: {
          sessionId: "sess-1", runId: 1, state: "running", instId: "BTC-USDT-SWAP", bar: "1H",
          strategyIds: [1], strategyNames: ["S1"], budgetUsd: 100, leverage: 3,
          contextRefreshRounds: 10, roundsCompleted: 0, roundsSinceSummary: 0,
          positionSide: "long", sessionPnlUsd: 0, startedTs: 0,
          disciplines: {
            rows: [
              {
                key: "exchangeBackstop",
                label: "交易所侧兜底",
                armed: over.backstopArmed ?? false,
                threshold: "止损 58172.8",
                source: "runtime",
              },
            ],
            hint: null,
          },
        },
        trades: [],
        position: (over.position ?? null) as never,
        decisions: [],
        traces: [],
        summary: null,
      },
    },
  } as unknown as AiTradeGroupState;
}

function renderDialog(group: AiTradeGroupState) {
  const onConfirm = vi.fn();
  render(
    <AiTradePauseDialog
      open
      group={group}
      submitting={false}
      onClose={() => {}}
      onConfirm={onConfirm}
    />,
  );
  return onConfirm;
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("AiTradePauseDialog", () => {
  it("默认选中「保留持仓暂停」，提交按钮是 primary 而不是危险色", () => {
    const onConfirm = renderDialog(makeGroup());
    expect((screen.getAllByRole("radio")[0] as HTMLInputElement).checked).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "保留持仓并暂停" }));
    expect(onConfirm).toHaveBeenCalledWith({ holdPosition: true });
  });

  it("切到「平仓后暂停」后下发 holdPosition:false（那是会当场实现盈亏的那一个）", () => {
    const onConfirm = renderDialog(makeGroup());
    fireEvent.click(screen.getAllByRole("radio")[1]);
    fireEvent.click(screen.getByRole("button", { name: "平仓后暂停" }));
    expect(onConfirm).toHaveBeenCalledWith({ holdPosition: false });
  });

  it("带仓且兜底未覆盖 ⇒ 风险前置：红条 + 「立即补挂」直连既有端点", async () => {
    vi.mocked(aiTradeApi.repairBackstop).mockResolvedValue({} as never);
    renderDialog(makeGroup({ position: { side: "long", size: 0.6 } }));

    expect(screen.getByTestId("ai-trade-pause-positions")).toBeInTheDocument();
    expect(screen.getByText("无持久化兜底")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "立即补挂" }));
    await waitFor(() =>
      expect(aiTradeApi.repairBackstop).toHaveBeenCalledWith("grp-1", "BTC-USDT-SWAP"),
    );
    expect(await screen.findByText("补挂请求已发出，正在向交易所核实")).toBeInTheDocument();
  });

  it("兜底已覆盖 ⇒ 绿标且不出现补挂按钮（不制造一条与你无关的告警）", () => {
    renderDialog(makeGroup({ position: { side: "long", size: 0.6 }, backstopArmed: true }));
    expect(screen.getByText("交易所侧兜底已覆盖")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "立即补挂" })).not.toBeInTheDocument();
  });

  it("无持仓 ⇒ 整段持仓与兜底行不渲染", () => {
    renderDialog(makeGroup());
    expect(screen.queryByTestId("ai-trade-pause-positions")).not.toBeInTheDocument();
  });

  it("文案如实说明暂停期仍在跑的东西（把「暂停」读成「AI 坏了」是本轮点名的风险 12）", () => {
    renderDialog(makeGroup());
    expect(
      screen.getByText(/盯市、保护性平仓、会话止盈止损与交易所侧兜底核实照常运行/),
    ).toBeInTheDocument();
  });
});
