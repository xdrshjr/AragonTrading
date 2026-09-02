/**
 * 日志轮转器的门禁（ai-trade-silent-degradation-repair 轴 D-1 · G-D1..G-D4 + G-D7③）。
 *
 * 它守的是一个已经发生了的事实：用户本机的 `backend.log` 是 **254.7 MB 单文件、216 万行、
 * 无轮转**，而其中 61.0% 的行是同一个后台轮询。用户被要求「看看桌面端的日志」——这份日志
 * 在物理上就不可能被人眼读。
 *
 * Run: node --test scripts/rotating-log.test.mjs
 *
 * （`desktop/package.json` 的 `node --test "scripts/*.test.mjs"` 依赖 Node 21+ 的原生 glob
 * 展开，而 CI 钉的是 20——本仓库已记录过这条，故这里给出可直跑的单文件命令。）
 */

import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { createRequire } from "node:module";
import { test } from "node:test";

const require = createRequire(import.meta.url);
const { openRotating, resolveLogOptions, DEFAULT_MAX_BYTES, DEFAULT_KEEP } =
  require("../src/lib/rotating-log.js");

function tmpDir() {
  return fs.mkdtempSync(path.join(os.tmpdir(), "aragon-rotlog-"));
}

/** 同步等到流真的把字节落盘（`write` 是异步的，断言 size 之前必须收敛）。 */
function settle(writer) {
  writer.end();
}

test("G-D1: 写满 maxBytes 即轮转，且只保留 keep 份", () => {
  const dir = tmpDir();
  const w = openRotating(dir, "backend", { maxBytes: 64, keep: 3 });
  for (let i = 0; i < 12; i += 1) w.write("x".repeat(50) + "\n"); // 每次 51 字节 ⇒ 每次都越线
  settle(w);

  assert.ok(fs.existsSync(path.join(dir, "backend.log")), "当前文件必须仍在");
  assert.ok(fs.existsSync(path.join(dir, "backend.1.log")), "轮转后必须产生 backend.1.log");
  assert.ok(fs.existsSync(path.join(dir, "backend.2.log")), "轮转两次后必须产生 backend.2.log");
  assert.equal(
    fs.existsSync(path.join(dir, "backend.3.log")),
    false,
    "keep=3 时绝不允许出现第 4 份——那正是「无上限」的复发形态",
  );
});

test("G-D2: 打开时既有文件已超限 ⇒ 先轮转再写", () => {
  const dir = tmpDir();
  fs.writeFileSync(path.join(dir, "backend.log"), "y".repeat(500));

  const w = openRotating(dir, "backend", { maxBytes: 100, keep: 3 });
  settle(w);

  assert.equal(
    fs.readFileSync(path.join(dir, "backend.1.log"), "utf-8").length,
    500,
    "打开时的那份超限文件必须被整份归档（本机现状的 254.7MB 走的就是这条路）",
  );
  assert.ok(fs.statSync(path.join(dir, "backend.log")).size < 100, "新文件必须是空的");
});

test("G-D3: rename 失败（模拟 Windows 占用）⇒ 不抛、不丢数据", () => {
  const dir = tmpDir();
  const realRename = fs.renameSync;
  fs.renameSync = () => {
    const err = new Error("EBUSY: resource busy or locked");
    err.code = "EBUSY";
    throw err;
  };
  try {
    const w = openRotating(dir, "backend", { maxBytes: 32, keep: 3 });
    assert.doesNotThrow(() => {
      w.write("a".repeat(40) + "\n");
      w.write("b".repeat(40) + "\n");
    }, "轮转器绝不能成为进程崩溃的原因——它跑在 bootstrap() 的第一行上");
    settle(w);
  } finally {
    fs.renameSync = realRename;
  }

  const body = fs.readFileSync(path.join(dir, "backend.log"), "utf-8");
  assert.ok(body.includes("a".repeat(40)), "轮转失败时第一行仍必须落盘");
  assert.ok(body.includes("b".repeat(40)), "轮转失败时后续行仍必须落盘（不丢数据）");
  assert.equal(fs.existsSync(path.join(dir, "backend.1.log")), false);
});

test("G-D4: maxBytes=0 ⇒ 不轮转，行为与纯追加流等价", () => {
  const dir = tmpDir();
  const w = openRotating(dir, "backend", { maxBytes: 0, keep: 3 });
  for (let i = 0; i < 20; i += 1) w.write("z".repeat(100) + "\n");
  settle(w);

  assert.equal(fs.existsSync(path.join(dir, "backend.1.log")), false, "0 = 逐字节回到今天");
  assert.equal(fs.statSync(path.join(dir, "backend.log")).size, 20 * 101);
});

test("G-D7③: 轮转参数来自 agent.env，而不是只来自 process.env", () => {
  // 这条是评审 P0-5 的构造性门禁：v1 只读 `process.env`，而 `openLogStreams()` 跑在
  // `bootstrap()` 的第一行、早于 agentEnv 加载，且一个从图标启动的打包进程 `process.env`
  // 里不会有任何用户设的键 —— 那把「一键回到今天」的闸在唯一的产品形态里按不下去。
  const fromAgentEnv = resolveLogOptions(() => ({ ARAGON_LOG_MAX_BYTES: "0", ARAGON_LOG_KEEP: "5" }), {});
  assert.equal(fromAgentEnv.maxBytes, 0, "agent.env 里的 0 必须真的把轮转关掉");
  assert.equal(fromAgentEnv.keep, 5);

  const fromProcess = resolveLogOptions(() => ({}), { ARAGON_LOG_MAX_BYTES: "123" });
  assert.equal(fromProcess.maxBytes, 123, "第二级 process.env 仍须生效（开发 / CI）");

  // agent.env 覆盖 process.env（用户显式写下的那份优先）。
  const both = resolveLogOptions(() => ({ ARAGON_LOG_MAX_BYTES: "7" }), { ARAGON_LOG_MAX_BYTES: "9" });
  assert.equal(both.maxBytes, 7);

  // loadAgentEnv 抛（文件不存在 / 编码坏 / 权限）⇒ 静默回落内建默认，绝不让日志器把启动带崩。
  const broken = resolveLogOptions(() => {
    throw new Error("EACCES");
  }, {});
  assert.equal(broken.maxBytes, DEFAULT_MAX_BYTES);
  assert.equal(broken.keep, DEFAULT_KEEP);
});
