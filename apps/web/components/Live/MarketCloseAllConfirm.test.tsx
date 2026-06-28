import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { tradingApi } from "@/lib/tradingApi";
import type { LivePosition } from "@/lib/tradingTypes";
import MarketCloseAllConfirm from "./MarketCloseAllConfirm";

vi.mock("@/lib/tradingApi", () => ({
  tradingApi: {
    config: vi.fn(),
    closePosition: vi.fn(),
    closeAll: vi.fn(),
  },
}));

function mockPositions(): LivePosition[] {
  return [
    {
      instId: "BTC-USDT-SWAP",
      instType: "SWAP",
      posSide: "long",
      pos: 5,
      avgPx: 64000,
      last: 65000,
      upl: 50,
      uplRatio: 0.01,
      lever: 3,
      marginMode: "cross",
    },
  ];
}

function renderConfirm(onClosed = vi.fn()) {
  return render(
    <MarketCloseAllConfirm
      open
      positions={mockPositions()}
      accountId={1}
      onClose={vi.fn()}
      onClosed={onClosed}
    />,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(tradingApi.closeAll).mockResolvedValue({
    ok: true,
    closedCount: 1,
    failedCount: 0,
    results: [{ instId: "BTC-USDT-SWAP", posSide: "long", ok: true, clOrdId: "aragonc1" }],
  });
});
afterEach(cleanup);

describe("MarketCloseAllConfirm — two-step confirmation gate", () => {
  it("keeps confirm disabled until the acknowledgement is checked", () => {
    renderConfirm();
    const confirm = screen.getByRole("button", { name: "确认市价全平" }) as HTMLButtonElement;
    expect(confirm.disabled).toBe(true);
  });

  it("first click only arms the second confirmation (does not execute)", async () => {
    renderConfirm();
    fireEvent.click(screen.getByLabelText("我已知晓将以市价一键平掉以上全部持仓"));
    const confirm = screen.getByRole("button", { name: "确认市价全平" }) as HTMLButtonElement;
    await waitFor(() => expect(confirm.disabled).toBe(false));

    fireEvent.click(confirm);
    expect(tradingApi.closeAll).not.toHaveBeenCalled();
    // 进入二次确认态：按钮文案变为「再次确认 · 立即全平」。
    expect(screen.getByRole("button", { name: "再次确认 · 立即全平" })).toBeTruthy();
  });

  it("executes closeAll only on the second click", async () => {
    const onClosed = vi.fn();
    renderConfirm(onClosed);
    fireEvent.click(screen.getByLabelText("我已知晓将以市价一键平掉以上全部持仓"));
    const confirm = screen.getByRole("button", { name: "确认市价全平" }) as HTMLButtonElement;
    await waitFor(() => expect(confirm.disabled).toBe(false));

    fireEvent.click(confirm); // arm
    const armed = await screen.findByRole("button", { name: "再次确认 · 立即全平" });
    fireEvent.click(armed); // execute

    await waitFor(() => expect(tradingApi.closeAll).toHaveBeenCalledTimes(1));
    expect(tradingApi.closeAll).toHaveBeenCalledWith(1);
    await waitFor(() => expect(onClosed).toHaveBeenCalled());
  });
});
