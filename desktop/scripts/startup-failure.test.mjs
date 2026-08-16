/**
 * Regression tests for the startup failure copy contract (src/lib/startup-failure.js).
 *
 * Freezes the two properties that make this a usable last-resort handler:
 * (1) it never throws, whatever it is handed — a fallback that throws is not a fallback;
 * (2) the copy always tells the user where the full log is and how to fix the common cause.
 *
 * Run: node --test scripts/startup-failure.test.mjs
 */

import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { test } from "node:test";

const require = createRequire(import.meta.url);
const { describeStartupFailure, DETAIL_MAX_CHARS } = require(
  "../src/lib/startup-failure.js",
);

const LOG_DIR = "C:/Users/tester/AppData/Roaming/aragon-desktop/logs";

test("标题固定，正文含日志目录与 pnpm setup 指引", () => {
  const { title, body } = describeStartupFailure(new Error("boom"), LOG_DIR);
  assert.equal(title, "Aragon Trading 启动失败");
  assert.ok(body.includes(LOG_DIR), "用户必须知道完整日志在哪，否则等于什么都没说");
  assert.ok(body.includes("pnpm setup"), "最常见的可自助修复原因要给出确切命令");
  assert.ok(body.includes("boom"), "错误详情必须出现在正文里");
});

test("非 Error 入参（字符串 / undefined / null）一律不抛且给出非空文案", () => {
  for (const bad of ["boom", undefined, null, 42, { nope: true }]) {
    const info = describeStartupFailure(bad, LOG_DIR);
    assert.ok(info.title.length > 0, `${String(bad)} 必须有标题`);
    assert.ok(info.body.length > 0, `${String(bad)} 必须有正文`);
  }
});

test("stack / logDir 自己会抛时仍然返回可读结果（兜底不得再抛）", () => {
  const hostile = {
    get stack() {
      throw new Error("stack getter exploded");
    },
  };
  const info = describeStartupFailure(hostile, undefined);
  assert.ok(info.body.includes("无法读取错误详情"));
  assert.ok(info.body.includes("日志目录不可用"));
});

test("超长 stack 被截断，弹窗不会被撑爆", () => {
  const err = new Error("x");
  err.stack = "y".repeat(5000);
  const { body } = describeStartupFailure(err, LOG_DIR);
  assert.ok(!body.includes("y".repeat(DETAIL_MAX_CHARS + 1)), "详情必须被截断到上限内");
  assert.ok(body.includes("y".repeat(DETAIL_MAX_CHARS)), "但截断前的那 800 字符要留住");
});
