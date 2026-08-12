/**
 * Regression test for the `OKX_` env passthrough in backend subprocess env assembly
 * (src/lib/config.js) — okx-link-resilience §3.5.
 *
 * 白名单漏放行 `OKX_` 前缀时，打包版**所有** OKX_LINK_* 旋钮（心跳 / 退避 / 熔断 / 连接池 /
 * 补根上限）会静默失效：后端 `Settings` 是 `extra="ignore"`，未透传的 env 被无声丢弃，从不报错。
 * 本仓库已为这类漏改栽过两次，所以它必须有一条会红的用例。
 *
 * 独立成文件而不是并进 `config.test.mjs`：那个文件属于另一个在制特性（自动交易总闸默认值翻转），
 * 尚未提交；把本轮的断言塞进去会让这次提交捎带上别人的半成品。
 *
 * Run: node --test scripts/config.okx-link.test.mjs
 */

import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { test } from "node:test";

const require = createRequire(import.meta.url);
const cfg = require("../src/lib/config.js");

// 隔离 ambient env：`backendEnv` 以 `...process.env` 打底，这些键若恰好存在于运行机器上会让断言
// 依赖环境。测试内临时清除并复原，保证确定性（沿用 `config.test.mjs` 的既有做法）。
const PROBE_KEYS = ["OKX_LINK_WS_PING_INTERVAL_S", "OKX_LINK_BREAKER_FAILURES", "IGNORED_KEY"];

function withCleanEnv(run) {
  const saved = {};
  for (const key of PROBE_KEYS) {
    saved[key] = process.env[key];
    delete process.env[key];
  }
  try {
    run();
  } finally {
    for (const key of PROBE_KEYS) {
      if (saved[key] === undefined) delete process.env[key];
      else process.env[key] = saved[key];
    }
  }
}

test("prod backendEnv forwards OKX_LINK_* knobs from agent.env", () => {
  withCleanEnv(() => {
    const env = cfg.backendEnv({
      mode: "prod",
      dbPath: "C:/tmp/aragon.db",
      agentEnv: {
        OKX_LINK_WS_PING_INTERVAL_S: "5",
        OKX_LINK_BREAKER_FAILURES: "0",
        IGNORED_KEY: "x",
      },
    });

    assert.equal(env.OKX_LINK_WS_PING_INTERVAL_S, "5");
    assert.equal(env.OKX_LINK_BREAKER_FAILURES, "0");
    assert.equal(env.IGNORED_KEY, undefined, "白名单之外的键一律不进后端 env");
  });
});

test("dev backendEnv does not inject agent.env at all", () => {
  // 透传只在 prod 生效（dev 直接读 services/quant/.env），这条守住那个边界不被顺手放宽。
  withCleanEnv(() => {
    const env = cfg.backendEnv({ mode: "dev", agentEnv: { OKX_LINK_WS_PING_INTERVAL_S: "5" } });

    assert.equal(env.OKX_LINK_WS_PING_INTERVAL_S, undefined);
  });
});
