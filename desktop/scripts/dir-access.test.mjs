/**
 * Regression tests for the trajectory-directory IPC guards (src/lib/dir-access.js).
 *
 * Guards the invariant behind `aragon:open-path`: the main process only ever hands an existing
 * absolute *directory* to `shell.openPath` (for a file that call means "launch it with the
 * associated program"). `aragon:pick-directory` must never forward a relative or non-string
 * default path to the native dialog.
 *
 * Run (Node 20 compatible — the package script's glob needs Node 21+):
 *   node --test scripts/dir-access.test.mjs
 */

import assert from "node:assert/strict";
import { createRequire } from "node:module";
import path from "node:path";
import { test } from "node:test";

const require = createRequire(import.meta.url);
const { pickDefaultPath, openPathRejection } = require("../src/lib/dir-access.js");

// 用 path.resolve 造「当前平台意义下的绝对路径」：写死 `C:\\` 或 `/` 会让用例只在一种系统上成立。
const ABS_DIR = path.resolve("aragon-datasets", "ai-trade");
const ABS_FILE = path.resolve("aragon-datasets", "ai-trade", "manifest.json");
const ABS_MISSING = path.resolve("aragon-datasets", "gone");

/**
 * 假 fs：按路径返回目录 / 文件 / 不存在三态，并记录调用次数（绝不触碰真实磁盘）。
 *
 * 只提供 `promises.stat`、不提供 `statSync`：主进程上的同步 stat 会在网络盘掉线时卡死整个窗口，
 * 实现一旦退回同步调用，这里就会以 TypeError 判红。
 */
function fakeFs(entries) {
  const calls = [];
  return {
    calls,
    promises: {
      async stat(target) {
        calls.push(target);
        if (!(target in entries)) {
          const err = new Error(`ENOENT: no such file or directory, stat '${target}'`);
          err.code = "ENOENT";
          throw err;
        }
        return { isDirectory: () => entries[target] === "dir" };
      },
    },
  };
}

const INVALID_INPUTS = [
  undefined,
  null,
  42,
  {},
  "",
  "   ",
  path.join("aragon-datasets", "ai-trade"), // 相对路径
  `${ABS_DIR}\0evil`,
];

test("pickDefaultPath：非对象 / 缺键 / 非字符串 / 相对路径 / 空串 / 含 NUL 一律 undefined", () => {
  for (const opts of [undefined, null, ABS_DIR, 7, {}, { defaultPath: 7 }]) {
    assert.equal(pickDefaultPath(opts), undefined, `opts=${String(opts)}`);
  }
  for (const bad of INVALID_INPUTS) {
    assert.equal(pickDefaultPath({ defaultPath: bad }), undefined, `defaultPath=${String(bad)}`);
  }
});

test("pickDefaultPath：合法绝对路径原样透传", () => {
  assert.equal(pickDefaultPath({ defaultPath: ABS_DIR }), ABS_DIR);
});

test("openPathRejection：非字符串 / 相对 / 空 / 含 NUL ⇒ invalid_path，且根本不去 stat", async () => {
  const fsImpl = fakeFs({ [ABS_DIR]: "dir" });
  for (const bad of INVALID_INPUTS) {
    assert.equal(await openPathRejection(bad, fsImpl), "invalid_path", `target=${String(bad)}`);
  }
  assert.equal(fsImpl.calls.length, 0);
});

test("openPathRejection：stat 抛错（不存在 / 无权限）⇒ not_found", async () => {
  assert.equal(await openPathRejection(ABS_MISSING, fakeFs({})), "not_found");
});

test("openPathRejection：存在但是文件 ⇒ not_directory（绝不交给 shell.openPath 去「运行」它）", async () => {
  assert.equal(await openPathRejection(ABS_FILE, fakeFs({ [ABS_FILE]: "file" })), "not_directory");
});

test("openPathRejection：存在的绝对目录 ⇒ null（放行）", async () => {
  const fsImpl = fakeFs({ [ABS_DIR]: "dir" });
  assert.equal(await openPathRejection(ABS_DIR, fsImpl), null);
  assert.deepEqual(fsImpl.calls, [ABS_DIR]);
});
