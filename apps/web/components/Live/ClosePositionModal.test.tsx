import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useAppStore } from "@/lib/store";
import { tradingApi } from "@/lib/tradingApi";
import type { LivePosition, TradingConfig } from "@/lib/tradingTypes";
import ClosePositionModal from "./ClosePositionModal";

vi.mock("@/lib/tradingApi", () => ({
  tradingApi: {
    config: vi.fn(),
    closePosition: vi.fn(),
    closeAll: vi.fn(),
  },
}));

function mockConfig(over: Partial<TradingConfig> = {}): TradingConfig {
  return {
    accountId: 1,
    instId: "BTC-USDT-SWAP",
    posMode: "long_short_mode",
    tdMode: "cross",
    availUsdt: 10000,
    instMeta: { ctVal: 0.01, ctValCcy: "BTC", lotSz: 1, minSz: 1, tickSz: 0.1, maxLever: 125 },
    lastPx: 65000,
    instrumentSupported: true,
    liveTradingEnabled: true,
    ...over,
  };
}

function mockPos(over: Partial<LivePosition> = {}): LivePosition {
  return {
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
    ...over,
  };
}

function renderModal(pos: LivePosition = mockPos()) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <ClosePositionModal
        open
        pos={pos}
        accountId={1}
        onClose={vi.fn()}
        onClosed={vi.fn()}
      />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  useAppStore.setState({ liveAcknowledged: false });
  vi.mocked(tradingApi.config).mockResolvedValue(mockConfig());
});
afterEach(cleanup);

describe("ClosePositionModal", () => {
  it("locks the quantity input in market (整仓) mode to the full position", async () => {
    renderModal();
    const qty = (await screen.findByLabelText("平仓数量（张）")) as HTMLInputElement;
    fireEvent.click(screen.getByRole("button", { name: "市价（整仓）" }));
    const locked = screen.getByLabelText("平仓数量（张）") as HTMLInputElement;
    expect(locked.disabled).toBe(true);
    expect(locked.value).toBe("5");
    void qty;
  });

  it("rejects a size exceeding the position and keeps submit disabled", async () => {
    renderModal();
    const qty = (await screen.findByLabelText("平仓数量（张）")) as HTMLInputElement;
    fireEvent.change(qty, { target: { value: "8" } }); // |pos| = 5
    fireEvent.click(screen.getByLabelText("我已知晓这是真实平仓"));
    const submit = screen.getByRole("button", { name: "确认挂单平仓" }) as HTMLButtonElement;
    expect(submit.disabled).toBe(true);
    expect(screen.getByText(/不可超过当前持仓/)).toBeTruthy();
    expect(tradingApi.closePosition).not.toHaveBeenCalled();
  });

  it("submits a limit close with mapped args after acknowledgement", async () => {
    vi.mocked(tradingApi.closePosition).mockResolvedValue({
      ok: true,
      mode: "limit",
      ordId: "1234",
      clOrdId: "aragonc7f3a",
      closedSz: 3,
    });
    renderModal();
    const qty = (await screen.findByLabelText("平仓数量（张）")) as HTMLInputElement;
    fireEvent.change(qty, { target: { value: "3" } });
    fireEvent.click(screen.getByLabelText("我已知晓这是真实平仓"));

    const submit = screen.getByRole("button", { name: "确认挂单平仓" }) as HTMLButtonElement;
    await waitFor(() => expect(submit.disabled).toBe(false));
    fireEvent.click(submit);

    await waitFor(() => expect(tradingApi.closePosition).toHaveBeenCalledTimes(1));
    const req = vi.mocked(tradingApi.closePosition).mock.calls[0][0];
    expect(req.instId).toBe("BTC-USDT-SWAP");
    expect(req.posSide).toBe("long");
    expect(req.mgnMode).toBe("cross");
    expect(req.ordType).toBe("limit");
    expect(req.sz).toBe(3);
    expect(req.px).toBe(65000); // 自标记价预填并按 tickSz 对齐
    expect(await screen.findByText(/已提交挂单平仓/)).toBeTruthy();
  });

  it("forces market mode and disables limit for a dust position below minSz", async () => {
    renderModal(mockPos({ pos: 0.5 })); // |pos| 0.5 < minSz 1
    // dust 提示仅在元数据载入（isDust 成立）后出现，先等它再断言限价禁用，避免读到 config 未就绪态。
    expect(await screen.findByText(/不足最小下单量/)).toBeTruthy();
    const limitBtn = screen.getByRole("button", { name: "限价（挂单平仓）" }) as HTMLButtonElement;
    expect(limitBtn.disabled).toBe(true);
  });
});
