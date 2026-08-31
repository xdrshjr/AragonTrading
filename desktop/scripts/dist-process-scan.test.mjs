/**
 * Regression tests for the packaging pre-flight process scan
 * (scripts/dist-process-scan.ps1, consumed by pack.ps1).
 *
 * The bug these freeze: pack.ps1 used to enumerate processes with
 * `Get-CimInstance Win32_Process`. On a host whose WMI is wedged — Winmgmt
 * reports Running but every query blocks forever — that call never returns, so
 * the whole packaging run hung silently right after "2) desktop 依赖已就绪 ✓"
 * with no error, no timeout and no next log line.
 *
 * So two properties matter, and neither is expressible as a pure unit test:
 * (1) the scan always returns within a bounded time, whatever WMI does;
 * (2) a process really running out of win-unpacked is found without WMI at all.
 * Both are therefore driven through a real powershell.exe against a real
 * child process.
 *
 * Run: node --test scripts/dist-process-scan.test.mjs
 */

import assert from "node:assert/strict";
import { spawn, spawnSync } from "node:child_process";
import {
  copyFileSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  realpathSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { test } from "node:test";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const LIB = path.join(HERE, "dist-process-scan.ps1");
const PACK_PS1 = path.join(HERE, "..", "pack.ps1");
const IS_WINDOWS = process.platform === "win32";
const SKIP = IS_WINDOWS ? false : "Windows-only packaging pre-flight";

/** 单引号是 PowerShell 字面串里唯一需要转义的字符（双写）。 */
function psLiteral(value) {
  return `'${String(value).replace(/'/g, "''")}'`;
}

/**
 * 把一段 PowerShell 落成临时 .ps1 再 -File 执行：-Command 的参数转义在 Windows
 * 上不可靠，而这里的探针脚本必然带引号与路径。
 *
 * @returns {{status: number, stdout: string, stderr: string, elapsedMs: number}}
 */
function runPowerShell(body, timeoutMs) {
  const dir = mkdtempSync(path.join(os.tmpdir(), "dps-run-"));
  const file = path.join(dir, "probe.ps1");
  // 探针只写 ASCII，因此无需 BOM；库自身带 BOM，负责它的中文。
  writeFileSync(file, `$ErrorActionPreference = 'Stop'\n${body}\n`, "ascii");
  const startedAt = Date.now();
  try {
    const result = spawnSync(
      "powershell.exe",
      [
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        file,
      ],
      { encoding: "utf8", timeout: timeoutMs, windowsHide: true },
    );
    return { ...result, elapsedMs: Date.now() - startedAt };
  } finally {
    rmSync(dir, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 });
  }
}

const DOT_SOURCE = `. ${psLiteral(LIB)}`;

test("扫描在有界时间内返回——WMI 卡死也绝不挂住打包", { skip: SKIP }, () => {
  const budgetMs = 60_000;
  const probeDir = path.join(os.tmpdir(), "dps-absent", "win-unpacked");
  const result = runPowerShell(
    [
      DOT_SOURCE,
      `$found = @(Get-RunningDistProcesses -UnpackedDir ${psLiteral(probeDir)})`,
      'Write-Output "COUNT=$($found.Count)"',
    ].join("\n"),
    budgetMs * 2,
  );

  assert.equal(result.status, 0, `扫描必须正常退出；stderr=${result.stderr}`);
  assert.match(
    result.stdout,
    /COUNT=0/,
    `不存在的目录下不该有进程；stdout=${result.stdout}`,
  );
  assert.ok(
    result.elapsedMs < budgetMs,
    `扫描耗时 ${result.elapsedMs}ms 超出 ${budgetMs}ms 预算——` +
      "这正是旧实现在 WMI 卡死主机上无限挂起的形状",
  );
});

test(
  "win-unpacked 下运行的进程按 exe 路径被识别，完全不经 WMI",
  { skip: SKIP },
  async () => {
    // realpath 展开 8.3 短名：Get-Process 给的是长路径，短名会让前缀比较落空。
    const root = realpathSync.native(mkdtempSync(path.join(os.tmpdir(), "dps-")));
    const unpacked = path.join(root, "win-unpacked");
    mkdirSync(unpacked, { recursive: true });
    // node.exe 自包含，复制出来即可当「仍在运行的旧产物」。
    const fakeApp = path.join(unpacked, "Aragon Trading.exe");
    copyFileSync(process.execPath, fakeApp);

    const child = spawn(fakeApp, ["-e", "setInterval(() => {}, 60000)"], {
      stdio: "ignore",
    });
    await new Promise((resolve, reject) => {
      child.once("spawn", resolve);
      child.once("error", reject);
    });

    try {
      let stdout = "";
      let isFound = false;
      // 新进程出现在 Get-Process 里有极短延迟，轮询而不是睡死一个魔数。
      for (let attempt = 0; attempt < 10 && !isFound; attempt += 1) {
        const result = runPowerShell(
          [
            DOT_SOURCE,
            "$found = @(Get-RunningDistProcesses " +
              `-UnpackedDir ${psLiteral(unpacked)} ` +
              "-CommandLineTimeoutSeconds 0)",
            "foreach ($p in $found) {",
            '  Write-Output "HIT=$($p.ProcessId)|$($p.Name)|$($p.ExecutablePath)"',
            "}",
          ].join("\n"),
          60_000,
        );
        assert.equal(result.status, 0, `扫描必须正常退出；stderr=${result.stderr}`);
        stdout = result.stdout;
        isFound = stdout.includes(`HIT=${child.pid}|`);
        if (!isFound) await new Promise((resolve) => setTimeout(resolve, 500));
      }

      assert.ok(
        isFound,
        `未找到从 win-unpacked 运行的 PID ${child.pid}；stdout=${stdout}`,
      );
      const hit = stdout
        .split(/\r?\n/)
        .find((line) => line.startsWith(`HIT=${child.pid}|`));
      const [, name, exe] = hit.slice("HIT=".length).split("|");
      assert.equal(name, "Aragon Trading", "映像名要能让用户认出该关哪个进程");
      assert.equal(
        exe.toLowerCase(),
        fakeApp.toLowerCase(),
        "摘要里必须给出完整 exe 路径",
      );
    } finally {
      child.kill();
      await new Promise((resolve) => child.once("exit", resolve));
      rmSync(root, {
        recursive: true,
        force: true,
        maxRetries: 20,
        retryDelay: 200,
      });
    }
  },
);

test("命令行增强超时传 0 时直接跳过 WMI，不返回映射", { skip: SKIP }, () => {
  const result = runPowerShell(
    [
      DOT_SOURCE,
      "$map = Get-DistProcessCommandLineMap 0",
      'Write-Output "NULL=$($null -eq $map)"',
    ].join("\n"),
    30_000,
  );

  assert.equal(result.status, 0, `stderr=${result.stderr}`);
  assert.match(result.stdout, /NULL=True/, `stdout=${result.stdout}`);
  assert.ok(
    result.elapsedMs < 20_000,
    `跳过 WMI 的分支不该等任何超时，实际 ${result.elapsedMs}ms`,
  );
});

test("Format-DistProcessList 在拿不到 exe 路径时回落到命令行", { skip: SKIP }, () => {
  const result = runPowerShell(
    [
      DOT_SOURCE,
      "$rows = @(",
      "  [pscustomobject]@{ ProcessId = 11; Name = 'alpha'; " +
        "ExecutablePath = 'C:/x/alpha.exe'; CommandLine = $null },",
      "  [pscustomobject]@{ ProcessId = 22; Name = 'beta'; " +
        "ExecutablePath = $null; CommandLine = 'cmd /c beta.exe' }",
      ")",
      "Write-Output (Format-DistProcessList $rows)",
    ].join("\n"),
    30_000,
  );

  assert.equal(result.status, 0, `stderr=${result.stderr}`);
  assert.ok(
    result.stdout.includes("PID 11 alpha C:/x/alpha.exe"),
    `stdout=${result.stdout}`,
  );
  assert.ok(
    result.stdout.includes("PID 22 beta cmd /c beta.exe"),
    "exe 路径不可读时若连命令行都不显示，用户就不知道该关什么",
  );
});

test("pack.ps1 不再直连 WMI 枚举进程", () => {
  const source = readFileSync(PACK_PS1, "utf8");
  assert.ok(
    !/Get-CimInstance|Get-WmiObject|Win32_Process/.test(source),
    "打包主流程一旦重新直连 WMI，就会在 WMI 卡死的主机上重演无限挂起",
  );
  assert.ok(
    source.includes("dist-process-scan.ps1"),
    "pack.ps1 必须点源进程扫描库，否则 Stop-RunningDistProcesses 无函数可用",
  );
});
