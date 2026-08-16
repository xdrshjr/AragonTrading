import { describe, expect, it } from "vitest";
import {
  FAST_MS,
  LONG_MS,
  PROXY_TIMEOUT_MS,
  isTimeoutError,
  isWritePath,
  timeoutFor,
  timeoutMessage,
  withTimeout,
} from "./httpTimeout";
import { ApiRequestError, toApiError } from "./api";

describe("超时档位（D3）", () => {
  // ⚠️ 这张表里的每一条都必须是 `apps/web/lib/**` 里**真的会被 request() 拿到的路径**。
  // 写成后端路由的模板形状（或干脆写一条不存在的 `/ai-backtest/start`）会得到一条永远绿、
  // 却什么都没守住的用例——而真实路径继续以 20 秒被 abort，那是把一个能用的功能改坏。
  it.each([
    "/strategies/7/backtest",
    "/strategies/7/multi-backtest",
    "/strategies/7/walk-forward",
    "/strategies/compare",
    "/strategies/combo/preview",
    "/factors/mine",
    "/factors/evaluate",
    "/factors/compute",
    "/factor-sets/3/score",
    "/factor-sets/train",
    "/factor-sets/score-preview",
    "/weight-models/train",
    "/weight-models/4/runs/9/backtest",
    "/discovery/candidates/11/backtest",
    "/ai-trade/prior/rebuild",
  ])("重计算路径 %s 走 LONG_MS", (path) => {
    expect(timeoutFor(path)).toBe(LONG_MS);
  });

  // 异步作业只是「排进队列」并立刻回 jobId，进度走 WS——给它 315 秒等于在后端挂住时白冻 5 分钟。
  it("异步作业的启动/查询仍是 FAST_MS", () => {
    expect(timeoutFor("/ai-trade/backtest/start")).toBe(FAST_MS);
    expect(timeoutFor("/weight-models/batches")).toBe(FAST_MS);
  });

  it.each(["/health", "/candles?instId=BTC-USDT-SWAP&bar=1H", "/strategies", "/trading/positions"])(
    "普通路径 %s 走 FAST_MS",
    (path) => {
      expect(timeoutFor(path)).toBe(FAST_MS);
    },
  );

  it("query string 不影响档位判定", () => {
    expect(timeoutFor("/strategies/compare?foo=1")).toBe(LONG_MS);
  });

  // 前缀匹配不能宽到把「同前缀但不同尾段」的路径也算长跑：`/strategies/7` 是一次普通读，
  // 给它 315 秒等于在后端挂住时白白多冻结 5 分钟。
  it("同前缀但非长跑尾段的路径仍是 FAST_MS", () => {
    expect(timeoutFor("/strategies/7")).toBe(FAST_MS);
    expect(timeoutFor("/factor-sets/3")).toBe(FAST_MS);
  });
});

describe("客户端绝不能比代理先放弃（D3 / P1-8）", () => {
  it("LONG_MS 严格大于 PROXY_TIMEOUT_MS", () => {
    // 相等是错的：客户端计时从发起请求那一刻起算，代理计时从 Next 服务端收到请求才起算，
    // 取等值时客户端**必然**先触发，正好把「代理还在等」误报成「超时了」。
    expect(LONG_MS).toBeGreaterThan(PROXY_TIMEOUT_MS);
  });
});

describe("withTimeout 不覆盖调用方自带的 signal（D3）", () => {
  it("自带 signal 原样保留", () => {
    const own = AbortSignal.timeout(1234);
    expect(withTimeout({ signal: own }, 1000).signal).toBe(own);
  });

  it("没有 signal 时挂上一个", () => {
    const init = withTimeout({ cache: "no-store" }, 1000);
    expect(init.signal).toBeInstanceOf(AbortSignal);
    expect(init.cache).toBe("no-store");
  });

  it("init 为 undefined 也能用", () => {
    expect(withTimeout(undefined, 1000).signal).toBeInstanceOf(AbortSignal);
  });
});

describe("写类路径的超时文案必须说「未确认」而不是「失败」（D3）", () => {
  // 这是本模块最重要的一条：一个超时的 POST /trading/order **可能已经成交了**。
  // 显示成「下单失败」会让用户重下一单——那是这个仓库最不能犯的错。
  // ⚠️ 路径必须逐条抄自 `*Api.ts` 的真实调用点。会话动作全都带 `{id}`
  //（`/ai-trade/{groupId}/stop`，不是 `/ai-trade/stop`），按后者写表则**一条都匹配不上**：
  // 「停止会话 / 暂停 / 继续」这些最需要「结果未知」口径的按钮反而会拿到读类文案，
  // 而用例照样是绿的——本轮 D3 反复点名的静默不完整，在自己的验收里再犯一次。
  it.each(["/trading/order", "/trading/close", "/trading/close-all", "/trading/leverage",
           "/ai-trade/start", "/ai-trade/g1/stop", "/ai-trade/g1/pause", "/ai-trade/g1/resume",
           "/ai-trade/g1/end", "/ai-trade/g1/member/BTC-USDT-SWAP/stop",
           "/ai-trade/g1/member/BTC-USDT-SWAP/backstop/repair",
           "/live-auto/start", "/live-auto/s1/stop",
           "/live-auto/group/start", "/live-auto/group/g1/stop",
           "/live-auto/group/g1/member/BTC-USDT-SWAP/stop",
           "/paper/start", "/paper/batch/start", "/paper/s1/stop",
           "/paper/batch/g1/stop", "/paper/stop-all"])(
    "%s 判为写类",
    (path) => {
      expect(isWritePath(path)).toBe(true);
      expect(timeoutMessage(path, FAST_MS)).toContain("结果未知");
    },
  );

  // 反向：同家族里的只读路径不许被「末段动作」误伤，否则等于在一次纯查询上制造恐慌。
  it.each(["/ai-trade/status", "/ai-trade/g1/resume-preview", "/ai-trade/history",
           "/live-auto/status", "/paper/sessions", "/ai-trade/backtest/j1/cancel"])(
    "%s 仍判为读类",
    (path) => {
      expect(isWritePath(path)).toBe(false);
    },
  );

  it("读类文案不说「结果未知」（不该在没改动任何东西的操作上制造恐慌）", () => {
    expect(isWritePath("/candles")).toBe(false);
    const text = timeoutMessage("/candles", FAST_MS);
    expect(text).toContain("请求超时");
    expect(text).not.toContain("结果未知");
  });

  // 判据来自显式路径表而不是 HTTP 方法：这条 POST 只是强制探活一次，什么都没改。
  it("/okx/connectivity/probe 是 POST 但判为读类", () => {
    expect(isWritePath("/okx/connectivity/probe")).toBe(false);
  });

  it("文案里的秒数取自实际施加的超时", () => {
    expect(timeoutMessage("/candles", 20_000)).toContain("20 秒");
    expect(timeoutMessage("/candles", LONG_MS)).toContain("315 秒");
  });
});

describe("toApiError 的翻译与不吞（D3）", () => {
  it.each(["TimeoutError", "AbortError"])("%s 翻译成 TIMEOUT 错误体", (name) => {
    const err = Object.assign(new Error("aborted"), { name });
    expect(isTimeoutError(err)).toBe(true);

    const translated = toApiError(err, FAST_MS, "/trading/order");
    expect(translated).toBeInstanceOf(ApiRequestError);
    expect((translated as ApiRequestError).code).toBe("TIMEOUT");
    expect((translated as ApiRequestError).status).toBe(0);
    expect((translated as ApiRequestError).message).toContain("结果未知");
  });

  it("非超时错误原样返回（绝不吞、也绝不改写成超时）", () => {
    const err = new TypeError("Failed to fetch");
    expect(isTimeoutError(err)).toBe(false);
    expect(toApiError(err, FAST_MS, "/candles")).toBe(err);
  });
});
