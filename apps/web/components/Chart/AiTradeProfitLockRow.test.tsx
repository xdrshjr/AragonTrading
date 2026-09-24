import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import type { AiTradeGroupMemberState, AiTradeProfitLockSnapshot } from "@/lib/aiTradeTypes";
import AiTradeProfitLockRow from "./AiTradeProfitLockRow";

/**
 * 持仓行收益锁（profit-lock C-1）：门控必须是「确实还持仓」且「快照存在」——`member.position`
 * 平仓后不会被清空，只判快照就会在空仓后继续显示一条已不存在的锁线。
 */
afterEach(cleanup);

const SNAP: AiTradeProfitLockSnapshot = {
  stage: 1,
  status: "breakeven",
  unavailableReason: null,
  side: "long",
  entryPx: 100,
  peakPx: 100.2,
  mfeBps: 20,
  costBps: 10,
  atrBps: 40,
  armBps: 15,
  trailArmBps: 32,
  lockPx: 100.12,
  lockNetBps: 2,
  breachSeconds: 0,
  confirmSeconds: 8,
};

function member(side: string, snap: AiTradeProfitLockSnapshot | null, exits?: number) {
  return {
    instId: "BTC-USDT-SWAP",
    session: { positionSide: side, ...(exits != null ? { profitLock: { exits, armedEpochs: 1 } } : {}) },
    position: snap ? { profitLock: snap } : null,
  } as unknown as AiTradeGroupMemberState;
}

describe("AiTradeProfitLockRow", () => {
  it("flat 时不渲染，即使 position.profitLock 残留", () => {
    render(<AiTradeProfitLockRow member={member("flat", SNAP)} />);
    expect(screen.queryByTestId("ai-trade-profit-lock")).toBeNull();
  });

  it("无快照不渲染", () => {
    render(<AiTradeProfitLockRow member={member("long", null)} />);
    expect(screen.queryByTestId("ai-trade-profit-lock")).toBeNull();
  });

  it.each([
    [{ stage: 0, status: "watching", lockPx: null, lockNetBps: null }, "muted"],
    [{}, "accent"],
    [{ stage: 2, status: "trailing" }, "success"],
    [{ status: "unavailable", unavailableReason: "cost_unknown" }, "warn"],
  ] as const)("四态色调 %#", (patch, tone) => {
    render(
      <AiTradeProfitLockRow
        member={member("long", { ...SNAP, ...patch } as AiTradeProfitLockSnapshot)}
      />,
    );
    const row = screen.getByTestId("ai-trade-profit-lock");
    expect(row).toHaveAttribute("data-tone", tone);
    expect(row.className).toContain("whitespace-nowrap");
  });

  it("status 顶层的锁平次数进悬停说明", () => {
    render(<AiTradeProfitLockRow member={member("long", SNAP, 2)} />);
    expect(screen.getByTestId("ai-trade-profit-lock").getAttribute("title")).toContain(
      "本会话已锁平 2 次",
    );
  });
});
