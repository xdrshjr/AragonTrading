import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { AiTradeResumableGroup } from "@/lib/aiTradeTypes";
import { useAppStore } from "@/lib/store";
import AiTradePanel from "./AiTradePanel";

vi.mock("@/lib/aiTradeApi", () => ({
  aiTradeApi: { repairBackstop: vi.fn() },
}));

/**
 * 冷续跑卡（ai-trade-pause-resume §1.3 / [P2-9]）。
 *
 * 本文件守的是**措辞的可信度**：进程刚起、还没向交易所发过任何请求，所以这张卡**绝不能**声称
 * 「交易所侧止损仍生效」。关机前那次核实只能以过去时呈现，缺失时要明说不知道 —— 一句让用户
 * 安心的假话，代价是他真的去睡觉了。
 */
const CARD: AiTradeResumableGroup = {
  groupId: "grp-1",
  bar: "15m",
  accountId: 1,
  totalBudgetUsd: 300,
  leverage: 3,
  strategyNames: ["趋势带波段"],
  createdTs: 0,
  pausedTs: 1_700_000_000,
  pauseReason: "process_restart",
  pauseReasonText: "上次未正常退出，已自动暂停",
  resumedCount: 0,
  offlineSeconds: 31_200,
  offlineText: "8 小时 40 分",
  hasPosition: true,
  members: [
    {
      instId: "BTC-USDT-SWAP",
      position: { side: "long", size: 0.6 },
      backstopAtPause: { covered: true, stopPx: 58172.8 },
    },
  ],
};

function renderPanel(card: AiTradeResumableGroup | null) {
  useAppStore.setState({ aiTradeGroup: null, aiTradeResumable: card });
  render(<AiTradePanel rows={[]} active="BTC-USDT-SWAP" onSelect={() => {}} />);
}

beforeEach(() => useAppStore.setState({ aiTradeGroup: null, aiTradeResumable: null }));
afterEach(() => cleanup());

describe("冷续跑卡", () => {
  it("写明标的 / 周期 / 预算 / 离线时长 / 是否带仓（§1.3 的交付形态）", () => {
    renderPanel(CARD);
    const card = screen.getByTestId("ai-trade-resumable-card");
    expect(card).toHaveTextContent("BTC-USDT-SWAP");
    expect(card).toHaveTextContent("15m");
    expect(card).toHaveTextContent("已暂停 8 小时 40 分");
    expect(card).toHaveTextContent("上次未正常退出，已自动暂停");
    expect(card).toHaveTextContent("仍持有多头 0.60 张");   // fmtNum 的既有两位小数口径
  });

  it("兜底核实一律用**过去时**，绝不声称当下仍生效（[P2-9]）", () => {
    renderPanel(CARD);
    const card = screen.getByTestId("ai-trade-resumable-card");
    expect(card).toHaveTextContent("关机前最后一次核实（8 小时 40 分前）");
    expect(card).toHaveTextContent("当前状态需点「继续」后向交易所核实");
    expect(card).not.toHaveTextContent("仍生效");
  });

  it("关机前那次核实缺失 ⇒ 明说不知道，而不是省略或编一句安心话", () => {
    renderPanel({
      ...CARD,
      members: [{ instId: "BTC-USDT-SWAP", position: { side: "long", size: 0.6 } }],
    });
    expect(screen.getByTestId("ai-trade-resumable-card")).toHaveTextContent(
      "关机前未能确认是否有交易所侧兜底",
    );
  });

  it("关机前确认过没有兜底 ⇒ 如实说没有（与「不知道」是两个结论）", () => {
    renderPanel({
      ...CARD,
      members: [
        {
          instId: "BTC-USDT-SWAP",
          position: { side: "long", size: 0.6 },
          backstopAtPause: { covered: false },
        },
      ],
    });
    expect(screen.getByTestId("ai-trade-resumable-card")).toHaveTextContent(
      "关机前核实的结果是：没有任何持久化兜底",
    );
  });

  it("暂停时读不到持仓 ⇒ 说读不到，并说明继续前会重新核实（读不到 ≠ 没有）", () => {
    renderPanel({
      ...CARD,
      members: [{ instId: "BTC-USDT-SWAP", position: null, positionUnknown: true }],
    });
    expect(screen.getByTestId("ai-trade-resumable-card")).toHaveTextContent(
      "关机前未能读到持仓，继续前会重新向交易所核实",
    );
  });

  it("既无热态也无冷占位 ⇒ 整个面板不渲染（既有行为逐字不变）", () => {
    renderPanel(null);
    expect(screen.queryByTestId("ai-trade-resumable-card")).not.toBeInTheDocument();
  });
});
