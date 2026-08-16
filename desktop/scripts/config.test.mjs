/**
 * Regression tests for backend subprocess env assembly (src/lib/config.js).
 *
 * Guards the prod default-injection of the two "自动交易" master gates so a packaged
 * desktop ships with them ON by default. Explicit agent.env values may still turn them off.
 *
 * Run: node --test scripts/config.test.mjs
 */

import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import path from "node:path";
import { test } from "node:test";

const require = createRequire(import.meta.url);
const cfg = require("../src/lib/config.js");

// 隔离 ambient env：这些键若已存在于 process.env，会经 `...process.env` 抢先落位而使 `??=` 不再注入，
// 令断言依赖运行机器的环境。测试内临时清除并在末尾恢复，保证确定性。
const GATE_KEYS = ["LIVE_AUTO_TRADING_ENABLED", "AI_TRADE_ENABLED"];
function withCleanGateEnv(run) {
  const saved = {};
  for (const k of GATE_KEYS) {
    saved[k] = process.env[k];
    delete process.env[k];
  }
  try {
    run();
  } finally {
    for (const k of GATE_KEYS) {
      if (saved[k] === undefined) delete process.env[k];
      else process.env[k] = saved[k];
    }
  }
}

test("prod backendEnv defaults AI 自动交易 gate to enabled", () => {
  withCleanGateEnv(() => {
    const env = cfg.backendEnv({ mode: "prod", dbPath: "C:/tmp/aragon.db" });
    assert.equal(env.AI_TRADE_ENABLED, "true");
    assert.equal(env.LIVE_AUTO_TRADING_ENABLED, "true");
  });
});

test("prod backendEnv lets an explicit agent.env value override the AI 自动交易 default", () => {
  withCleanGateEnv(() => {
    const env = cfg.backendEnv({
      mode: "prod",
      dbPath: "C:/tmp/aragon.db",
      agentEnv: { AI_TRADE_ENABLED: "false" },
    });
    assert.equal(env.AI_TRADE_ENABLED, "false");
  });
});

test("dev backendEnv leaves AI 自动交易 default to backend Settings", () => {
  withCleanGateEnv(() => {
    const env = cfg.backendEnv({ mode: "dev" });
    assert.equal(env.AI_TRADE_ENABLED, undefined);
    assert.equal(env.LIVE_AUTO_TRADING_ENABLED, undefined);
  });
});

test("prod backendEnv passes through DISCOVERY_* agent.env keys", () => {
  // strategy-factor-discovery §六：DISCOVERY_ 前缀必须在 prod 白名单内，否则打包后所有
  // DISCOVERY_* env（总闸 / 并发 / 配额）静默失效——查不出来的一类失败。
  withCleanGateEnv(() => {
    const env = cfg.backendEnv({
      mode: "prod",
      dbPath: "C:/tmp/aragon.db",
      agentEnv: { DISCOVERY_ENABLED: "false", DISCOVERY_MAX_RUNS: "2", IGNORED_KEY: "x" },
    });
    assert.equal(env.DISCOVERY_ENABLED, "false");
    assert.equal(env.DISCOVERY_MAX_RUNS, "2");
    assert.equal(env.IGNORED_KEY, undefined);
  });
});

test("prod backendEnv passes through AI_TRADE_* agent.env keys", () => {
  withCleanGateEnv(() => {
    const env = cfg.backendEnv({
      mode: "prod",
      dbPath: "C:/tmp/aragon.db",
      agentEnv: { AI_TRADE_MIN_CONSENSUS: "2", IGNORED_KEY: "x" },
    });
    assert.equal(env.AI_TRADE_MIN_CONSENSUS, "2");
    assert.equal(env.IGNORED_KEY, undefined);
  });
});

// ai-trade-holding-horizon-modes [P2-8]：交易周期层的 env 也走 AI_TRADE_ 前缀。规格说「前缀已覆盖，
// 但实施时须**实测**而非假设」——运维总闸如果在打包版里读不到，G-1 的一键回退就只是文档上的承诺。
test("prod backendEnv passes through AI_TRADE_HORIZON_* agent.env keys", () => {
  withCleanGateEnv(() => {
    const env = cfg.backendEnv({
      mode: "prod",
      dbPath: "C:/tmp/aragon.db",
      agentEnv: {
        AI_TRADE_HORIZON_ENABLED: "false",
        AI_TRADE_HORIZON_DEFAULT_PROFILE: "trend_hold",
        AI_TRADE_HORIZON_BACKTEST_ENABLED: "false",
      },
    });
    assert.equal(env.AI_TRADE_HORIZON_ENABLED, "false");
    assert.equal(env.AI_TRADE_HORIZON_DEFAULT_PROFILE, "trend_hold");
    assert.equal(env.AI_TRADE_HORIZON_BACKTEST_ENABLED, "false");
  });
});

test("prod backendEnv forwards OKX_LINK_* knobs from agent.env", () => {
  // okx-link-resilience §3.5：白名单漏放行 OKX_ 前缀时，打包版所有链路旋钮会**静默**失效
  // （Settings 为 extra="ignore"，从不报错）。这条把那个漏改变成一条会红的用例。
  withCleanGateEnv(() => {
    const env = cfg.backendEnv({
      mode: "prod",
      dbPath: "C:/tmp/aragon.db",
      agentEnv: {
        OKX_LINK_WS_PING_INTERVAL_S: "5",
        OKX_LINK_BREAKER_FAILURES: "0",
      },
    });
    assert.equal(env.OKX_LINK_WS_PING_INTERVAL_S, "5");
    assert.equal(env.OKX_LINK_BREAKER_FAILURES, "0");
  });
});

test("prod backendEnv forwards SECURITY_ALLOWED_ORIGINS from agent.env", () => {
  // trust-boundary-and-signal-integrity D1/P0-3：后端本轮起按 Origin 白名单拒绝跨源请求。
  // prod 的 PyInstaller 后端**不读 .env、只读进程 env**，所以白名单的唯一入口就是这份透传。
  // 漏放行 SECURITY_ 前缀 ⇒ 用户改了前端端口后「点什么都 403」且**没有任何一条路**能改回来。
  // 这是同一类漏改的第三次（前两次是 DISCOVERY_ 与 OKX_），故照同一范式钉住。
  withCleanGateEnv(() => {
    const env = cfg.backendEnv({
      mode: "prod",
      dbPath: "C:/tmp/aragon.db",
      agentEnv: { SECURITY_ALLOWED_ORIGINS: "http://127.0.0.1:19999" },
    });
    assert.equal(env.SECURITY_ALLOWED_ORIGINS, "http://127.0.0.1:19999");
  });
});

// 触发 unused-import lint 前的自证：确认 config.js 解析入口与本测试同源，避免误引其他副本。
test("config module resolves to src/lib/config.js", () => {
  const here = path.dirname(fileURLToPath(import.meta.url));
  const resolved = require.resolve("../src/lib/config.js");
  assert.equal(resolved, path.join(path.dirname(here), "src", "lib", "config.js"));
});
