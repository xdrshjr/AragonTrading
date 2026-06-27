import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiRequestError, api } from "./api";

describe("api range requests", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("backtest serializes the selected time range", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ runId: 1, metrics: {} }), { status: 200 }),
    );

    await api.backtest(7, {
      instId: "BTC-USDT-SWAP",
      bar: "1H",
      limit: 500,
      startTs: 1_700_000_000,
      endTs: 1_700_360_000,
    });

    const init = fetchMock.mock.calls[0][1] as RequestInit;
    expect(JSON.parse(String(init.body))).toMatchObject({
      startTs: 1_700_000_000,
      endTs: 1_700_360_000,
    });
  });

  it("compare serializes the selected time range", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ instId: "BTC-USDT-SWAP", bar: "1H", items: [] }), {
        status: 200,
      }),
    );

    await api.compare({
      strategyIds: [1, 2],
      instId: "BTC-USDT-SWAP",
      bar: "1H",
      limit: 500,
      startTs: 1_700_000_000,
      endTs: 1_700_360_000,
    });

    const init = fetchMock.mock.calls[0][1] as RequestInit;
    expect(JSON.parse(String(init.body))).toMatchObject({
      startTs: 1_700_000_000,
      endTs: 1_700_360_000,
    });
  });

  it("normalizes FastAPI validation detail into a readable error", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({
          detail: [
            {
              loc: ["body", "strategyIds"],
              msg: "List should have at most 12 items after validation, not 13",
            },
          ],
        }),
        { status: 422, statusText: "Unprocessable Entity" },
      ),
    );

    try {
      await api.compare({
        strategyIds: Array.from({ length: 13 }, (_, index) => index + 1),
        instId: "BTC-USDT-SWAP",
        bar: "1H",
        startTs: 1_700_000_000,
        endTs: 1_700_360_000,
      });
      throw new Error("expected request to fail");
    } catch (error) {
      expect(error).toBeInstanceOf(ApiRequestError);
      expect(error).toMatchObject({
        code: "VALIDATION_ERROR",
        status: 422,
        message: "strategyIds: List should have at most 12 items after validation, not 13",
      });
    }
  });
});
