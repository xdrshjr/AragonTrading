/**
 * Regression test for the `AI_TRADE_INTEL_*` env passthrough in backend subprocess env assembly
 * (src/lib/config.js) — ai-trade-intel-closed-loop §6.3 / G-11。
 *
 * 本轮的 17 项新配置**沿用 `AI_TRADE_INTEL_` 前缀**，而白名单是**按前缀**而不是按键名逐个列举
 * 的，所以桌面壳零改动即可拿到它们。但这条结论完全依赖「白名单是前缀式的」这一个事实——
 * 一旦有人把它改成逐键枚举（那是一次看起来更安全的重构），17 项配置会在打包后端里**同时静默
 * 消失**，症状是「桌面端上核查轮永远不跑，而开发机上一切正常」。后端 `Settings` 是
 * `extra="ignore"`，未透传的 env 被无声丢弃，从不报错。
 *
 * 独立成文件而不是并进 `config.test.mjs` / `config.okx-link.test.mjs`：那两个文件各属于别的
 * 特性（其中一个还是未提交的在制件），把本轮的断言塞进去会让这次提交捎带上别人的半成品。
 *
 * Run: node --test scripts/config-passthrough.test.mjs
 *
 * （`desktop/package.json` 的 `node --test "scripts/*.test.mjs"` 依赖 Node 21+ 的原生 glob
 * 展开，而 CI 钉的是 20——本仓库已记录过这条，故这里给出可直跑的单文件命令。）
 */

import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { test } from "node:test";

const require = createRequire(import.meta.url);
const cfg = require("../src/lib/config.js");

const PROBE_KEYS = [
  "AI_TRADE_INTEL_VERIFY_ENABLED",
  "AI_TRADE_INTEL_CITE_ENABLED",
  "AI_TRADE_INTEL_ATTRIBUTION_ENABLED",
  "AI_TRADE_INTEL_VERIFY_MAX_TOKENS",
  // ai-trade-intel-actionability §6.3：本轮 18 项新配置**同样沿用 `AI_TRADE_INTEL_` 前缀**，
  // 故桌面壳零改动即可拿到它们。补进探针的是三把总闸——它们要是被静默丢掉，症状是
  // 「桌面端上高危情报永远不唤醒，而开发机上一切正常」。
  "AI_TRADE_INTEL_ALERT_ENABLED",
  "AI_TRADE_INTEL_EFFICACY_ENABLED",
  "AI_TRADE_INTEL_CONFLICT_ENABLED",
  // ai-trade-intel-arm-state §6.3：本轮唯一的新配置项同样沿用 `AI_TRADE_INTEL_` 前缀，故桌面壳
  // **产品代码零改动**。补这一行不是为了让它通过，而是为了在将来有人把前缀规则改窄时立刻判红
  // ——那正是这张表存在的全部理由。
  "AI_TRADE_INTEL_REARM_DEADLINE_S",
  "INTEL_VERIFY_ENABLED",
];

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

test("prod backendEnv forwards the closed-loop AI_TRADE_INTEL_* knobs from agent.env", () => {
  withCleanEnv(() => {
    const env = cfg.backendEnv({
      mode: "prod",
      dbPath: "C:/tmp/aragon.db",
      agentEnv: {
        AI_TRADE_INTEL_VERIFY_ENABLED: "false",
        AI_TRADE_INTEL_CITE_ENABLED: "false",
        AI_TRADE_INTEL_ATTRIBUTION_ENABLED: "false",
        AI_TRADE_INTEL_VERIFY_MAX_TOKENS: "4096",
        // ai-trade-intel-actionability 的三把总闸（同一前缀，桌面壳零改动即可穿到后端）。
        AI_TRADE_INTEL_ALERT_ENABLED: "false",
        AI_TRADE_INTEL_EFFICACY_ENABLED: "false",
        AI_TRADE_INTEL_CONFLICT_ENABLED: "false",
        // 前缀不沾 `AI_TRADE_` 的裸名：它**必须**被挡在外面，否则「按前缀」这条结论就是假的。
        INTEL_VERIFY_ENABLED: "true",
      },
    });

    assert.equal(env.AI_TRADE_INTEL_VERIFY_ENABLED, "false",
      "三把安全单键之一没穿过白名单：桌面端上核查轮会永远按默认值跑，且不报任何错");
    assert.equal(env.AI_TRADE_INTEL_CITE_ENABLED, "false");
    assert.equal(env.AI_TRADE_INTEL_ATTRIBUTION_ENABLED, "false");
    assert.equal(env.AI_TRADE_INTEL_VERIFY_MAX_TOKENS, "4096");
    assert.equal(env.AI_TRADE_INTEL_ALERT_ENABLED, "false",
      "告警总闸没穿过白名单：桌面端上高危情报会照默认值唤醒，而用户以为自己关掉了");
    assert.equal(env.AI_TRADE_INTEL_EFFICACY_ENABLED, "false");
    assert.equal(env.AI_TRADE_INTEL_CONFLICT_ENABLED, "false");
    assert.equal(env.INTEL_VERIFY_ENABLED, undefined, "白名单之外的键一律不进后端 env");
  });
});

test("dev backendEnv does not inject agent.env at all", () => {
  // 透传只在 prod 生效（dev 直接读 services/quant/.env），这条守住那个边界不被顺手放宽。
  withCleanEnv(() => {
    const env = cfg.backendEnv({
      mode: "dev",
      agentEnv: { AI_TRADE_INTEL_VERIFY_ENABLED: "false" },
    });

    assert.equal(env.AI_TRADE_INTEL_VERIFY_ENABLED, undefined);
  });
});
