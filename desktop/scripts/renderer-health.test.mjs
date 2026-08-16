/**
 * Regression tests for renderer health pure logic (src/lib/renderer-health.js).
 *
 * Guards the three decisions that keep a crashed window from becoming either a white screen
 * (no recovery at all) or an infinite crash-reload loop (recovery without a budget).
 *
 * Run: node --test scripts/renderer-health.test.mjs
 */

import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { test } from "node:test";

const require = createRequire(import.meta.url);
const { describeRendererGone, createReloadBudget, describeLoadFailure } = require(
  "../src/lib/renderer-health.js",
);

test("crashed / oom / abnormal-exit 值得自动重载", () => {
  for (const reason of ["crashed", "oom", "abnormal-exit"]) {
    const info = describeRendererGone({ reason });
    assert.equal(info.reason, reason);
    assert.equal(info.recoverable, true);
    assert.ok(info.message.length > 0, `${reason} 必须有一句人话`);
  }
});

test("killed / launch-failed / integrity-failure 不自动重载", () => {
  for (const reason of ["killed", "launch-failed", "integrity-failure"]) {
    const info = describeRendererGone({ reason });
    assert.equal(info.recoverable, false, reason);
    assert.equal(info.expected, false, `${reason} 是故障，必须走到对话框那条路`);
  }
});

test("clean-exit 既不自愈也不弹窗（它根本不是崩溃）", () => {
  const info = describeRendererGone({ reason: "clean-exit", exitCode: 0 });
  assert.equal(info.recoverable, false, "不能把一个刚被正常关掉的渲染进程再拉回来");
  assert.equal(info.expected, true, "只记日志：弹「界面进程已退出」是在为一件正常的事吓用户");
});

test("未知 reason 按不可恢复处理，且原样记进 message", () => {
  const info = describeRendererGone({ reason: "some-future-reason", exitCode: 7 });
  assert.equal(info.recoverable, false);
  assert.equal(info.expected, false, "看不懂的状态一律当故障：宁可弹窗，也不要静默放过");
  assert.equal(info.reason, "some-future-reason");
  assert.ok(info.message.includes("some-future-reason"));
  assert.ok(info.message.includes("7"), "exitCode 是排障线索，不得丢");
});

test("details 缺席也不抛（Electron 回调形状不受我们控制）", () => {
  const info = describeRendererGone(undefined);
  assert.equal(info.reason, "unknown");
  assert.equal(info.recoverable, false);
});

test("重载配额：窗口内放行 limit 次，第 limit+1 次拒绝", () => {
  const budget = createReloadBudget({ limit: 3, windowMs: 60_000 });
  assert.equal(budget.allow(1_000), true);
  assert.equal(budget.allow(2_000), true);
  assert.equal(budget.allow(3_000), true);
  assert.equal(budget.allow(4_000), false, "第 4 次必须被拒——崩溃循环比白屏更糟");
});

test("重载配额：滑出窗口后自行恢复（滑动窗口，不是一次性熔断）", () => {
  const budget = createReloadBudget({ limit: 2, windowMs: 1_000 });
  assert.equal(budget.allow(0), true);
  assert.equal(budget.allow(100), true);
  assert.equal(budget.allow(200), false);
  assert.equal(budget.allow(1_200), true, "最早那次已滑出 1s 窗口");
});

test("did-fail-load：-3 ERR_ABORTED 一律忽略（正常导航打断，不是故障）", () => {
  assert.equal(describeLoadFailure(-3, "ERR_ABORTED", "http://127.0.0.1:18761/"), null);
});

test("did-fail-load：真故障给出带 URL / 描述 / 码的一行", () => {
  const line = describeLoadFailure(-102, "ERR_CONNECTION_REFUSED", "http://127.0.0.1:18761/");
  assert.ok(line.includes("http://127.0.0.1:18761/"));
  assert.ok(line.includes("ERR_CONNECTION_REFUSED"));
  assert.ok(line.includes("-102"));
});

test("did-fail-load：描述缺席也仍给出一行（绝不返回 undefined 让调用方误判为忽略）", () => {
  const line = describeLoadFailure(-105, "", "");
  assert.equal(typeof line, "string");
  assert.ok(line.includes("-105"));
});
