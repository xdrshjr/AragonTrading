import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiRequestError } from "./api";
import { workspaceApi } from "./workspaceApi";
import type { WorkspaceWatchlistIn } from "./workspaceTypes";

const PREFS: WorkspaceWatchlistIn = {
  watchlist: ["BTC-USDT-SWAP", "ETH-USDT-SWAP"],
  autoTradeMembers: [{ instId: "BTC-USDT-SWAP", allocationPct: 50 }],
  autoTradeAllocationMode: "even",
  symbolGroups: [],
};

describe("workspaceApi", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("get hits the proxied workspace endpoint", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ present: false, ...PREFS }), { status: 200 }),
    );

    await workspaceApi.get();

    expect(fetchMock.mock.calls[0][0]).toBe("/api/py/workspace/watchlist");
    const init = fetchMock.mock.calls[0][1] as RequestInit;
    expect(init.method ?? "GET").toBe("GET");
  });

  it("update PUTs the full prefs document as JSON", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ present: true, ...PREFS }), { status: 200 }),
    );

    await workspaceApi.update(PREFS);

    const init = fetchMock.mock.calls[0][1] as RequestInit;
    expect(init.method).toBe("PUT");
    expect(JSON.parse(String(init.body))).toEqual(PREFS);
  });

  it("update forwards keepalive for the unload flush", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ present: true, ...PREFS }), { status: 200 }),
    );

    await workspaceApi.update(PREFS, { keepalive: true });

    const init = fetchMock.mock.calls[0][1] as RequestInit;
    expect(init.keepalive).toBe(true);
  });

  it("parses the structured error body into ApiRequestError", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ error: { code: "BAD", message: "类型错误" } }), {
        status: 422,
        statusText: "Unprocessable Entity",
      }),
    );

    try {
      await workspaceApi.get();
      throw new Error("expected request to fail");
    } catch (err) {
      expect(err).toBeInstanceOf(ApiRequestError);
      const apiErr = err as ApiRequestError;
      expect(apiErr.code).toBe("BAD");
      expect(apiErr.message).toBe("类型错误");
      expect(apiErr.status).toBe(422);
    }
  });

  it("falls back to status text when the error body is not JSON", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response("<html>boom</html>", { status: 500, statusText: "Internal Server Error" }),
    );

    try {
      await workspaceApi.update(PREFS);
      throw new Error("expected request to fail");
    } catch (err) {
      expect(err).toBeInstanceOf(ApiRequestError);
      expect((err as ApiRequestError).status).toBe(500);
    }
  });
});
