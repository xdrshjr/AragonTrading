import { afterEach, expect, it, vi } from "vitest";
import { agentTradingApi } from "./agentTradingApi";

afterEach(() => vi.unstubAllGlobals());

it("执行仅发送版本和预览哈希，网络失败绝不自动重发", async () => {
  const fetcher = vi.fn().mockRejectedValue(new TypeError("network"));
  vi.stubGlobal("fetch", fetcher);
  await expect(agentTradingApi.execute("c", "i", {
    version: 2, previewHash: "hash",
  })).rejects.toThrow();
  expect(fetcher).toHaveBeenCalledTimes(1);
  expect(fetcher.mock.calls[0][1].body).toBe(
    JSON.stringify({ version: 2, previewHash: "hash" }),
  );
});

it("能力关闭时全局未结查询仍使用只读端点", async () => {
  const fetcher = vi.fn().mockResolvedValue(new Response(
    JSON.stringify({ items: [], nextCursor: null }),
  ));
  vi.stubGlobal("fetch", fetcher);
  await agentTradingApi.unsettled();
  expect(fetcher.mock.calls[0][0]).toContain(
    "/api/py/agent/trading/unsettled-intents",
  );
  expect(fetcher.mock.calls[0][1].method).toBeUndefined();
});
