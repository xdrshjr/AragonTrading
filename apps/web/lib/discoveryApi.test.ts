import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiRequestError } from "./api";
import { buildQuery, discoveryApi } from "./discoveryApi";

const originalFetch = global.fetch;

function mockFetch(response: {
  ok?: boolean;
  status?: number;
  statusText?: string;
  body?: unknown;
}) {
  const fn = vi.fn().mockResolvedValue({
    ok: response.ok ?? true,
    status: response.status ?? 200,
    statusText: response.statusText ?? "OK",
    json: async () => response.body ?? {},
  });
  global.fetch = fn as unknown as typeof fetch;
  return fn;
}

beforeEach(() => {
  vi.restoreAllMocks();
});

afterEach(() => {
  global.fetch = originalFetch;
});

describe("buildQuery", () => {
  it("skips empty / null / undefined values", () => {
    expect(buildQuery({ a: 1, b: "", c: undefined, d: null, e: "x" })).toBe("?a=1&e=x");
  });

  it("returns an empty string when nothing survives", () => {
    expect(buildQuery({ a: "", b: undefined })).toBe("");
  });

  it("url-encodes keys and values", () => {
    expect(buildQuery({ reason: "llm_review:量纲" })).toContain("llm_review%3A");
  });
});

describe("discoveryApi — 错误体解包", () => {
  it("把统一错误体解成 ApiRequestError（code + message + status）", async () => {
    mockFetch({
      ok: false,
      status: 403,
      body: { error: { code: "DISCOVERY_DISABLED", message: "已被管理员关闭" } },
    });
    await expect(discoveryApi.defaults()).rejects.toMatchObject({
      code: "DISCOVERY_DISABLED",
      message: "已被管理员关闭",
      status: 403,
    });
  });

  it("响应体不是统一错误体时回落状态码与状态文本", async () => {
    mockFetch({ ok: false, status: 500, statusText: "Internal Server Error", body: {} });
    await expect(discoveryApi.defaults()).rejects.toBeInstanceOf(ApiRequestError);
  });

  it("204 无内容返回 undefined 而不是解析崩溃", async () => {
    mockFetch({ ok: true, status: 204 });
    await expect(discoveryApi.remove(3)).resolves.toBeUndefined();
  });
});

describe("discoveryApi — 请求装配", () => {
  it("start 走 POST 并带 JSON 体", async () => {
    const fn = mockFetch({ body: { runId: 7, status: "running", config: {}, startedTs: 1 } });
    const res = await discoveryApi.start({
      instId: "BTC-USDT-SWAP",
      bar: "1H",
      windowBars: 4000,
      axes: ["factor"],
      horizon: 6,
      candidatesPerIteration: 40,
      iterationIntervalS: 300,
      dataRefreshS: 21600,
      windowMode: "rolling",
      maxIterations: 0,
      maxRuntimeHours: 0,
      maxKept: 200,
      autoResume: true,
      autoPromote: false,
      llm: { enabled: true, reviewSample: 5, maxCallsPerHour: 24, timeoutS: 60 },
      thresholds: {},
      evolution: {},
    });
    expect(res.runId).toBe(7);
    const [url, init] = fn.mock.calls[0];
    expect(url).toBe("/api/py/discovery/runs");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body).instId).toBe("BTC-USDT-SWAP");
  });

  it("候选查询把筛选拼进查询串", async () => {
    const fn = mockFetch({ body: { items: [], total: 0 } });
    await discoveryApi.listCandidates(5, { verdict: "rejected", reason: "F10", limit: 25 });
    expect(fn.mock.calls[0][0]).toBe(
      "/api/py/discovery/runs/5/candidates?verdict=rejected&reason=F10&limit=25",
    );
  });

  it("生命周期端点走 POST 且路径正确", async () => {
    const fn = mockFetch({ body: { runId: 1, status: "paused" } });
    await discoveryApi.pause(1);
    await discoveryApi.resume(1);
    await discoveryApi.stop(1);
    expect(fn.mock.calls.map((call) => call[0])).toEqual([
      "/api/py/discovery/runs/1/pause",
      "/api/py/discovery/runs/1/resume",
      "/api/py/discovery/runs/1/stop",
    ]);
    expect(fn.mock.calls.every((call) => call[1].method === "POST")).toBe(true);
  });

  it("入库与一键回测路径正确", async () => {
    const fn = mockFetch({ body: {} });
    await discoveryApi.promote(11);
    await discoveryApi.backtest(11, 100, 200);
    expect(fn.mock.calls[0][0]).toBe("/api/py/discovery/candidates/11/promote");
    expect(fn.mock.calls[1][0]).toBe("/api/py/discovery/candidates/11/backtest");
    expect(JSON.parse(fn.mock.calls[1][1].body)).toEqual({ startTs: 100, endTs: 200 });
  });
});
