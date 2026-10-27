# Desktop packaging EBUSY exe lock

## Problem description

`desktop/pack.ps1` can fail at the final `electron-builder` step after the
backend PyInstaller build and the Next standalone build have both succeeded.
The observed failure is:

```text
packaging platform=win32 arch=x64 electron=31.7.7 appOutDir=dist\win-unpacked
copying Electron source=...\desktop\node_modules\electron\dist
  destination=...\desktop\dist\win-unpacked
EBUSY: resource busy or locked, open
  'M:\takoAI\AragonTrading\desktop\dist\win-unpacked\Aragon Trading.exe'
at writeFile (...\node_modules\app-builder-lib\src\electron\electronWin.ts:39:3)
at addWinAsarIntegrity (...)
at beforeCopyExtraFiles (...)
at WinPackager.doPack (...)
```

This is not a backend build failure and not the previous `winCodeSign`
symlink/EPERM issue. The failure happens when `electron-builder` tries to
write the packaged Windows executable in `desktop/dist/win-unpacked`.

At diagnosis time the exact packaged exe was not locked:

```powershell
$exe = 'desktop/dist/win-unpacked/Aragon Trading.exe'
[System.IO.File]::Open((Resolve-Path $exe), 'Open', 'ReadWrite', 'None')
# result: not_locked
```

That means the lock is timing-dependent, but the reproduced Windows behavior
below matches the observed `EBUSY` failure mode.

## Reproduction steps

### Direct project reproduction

1. Build or keep an existing unpacked desktop artifact:

```powershell
cd M:/takoAI/AragonTrading/desktop
npm run smoke
```

2. Start the unpacked app and leave it running:

```powershell
& "M:/takoAI/AragonTrading/desktop/dist/win-unpacked/Aragon Trading.exe"
```

3. In a second PowerShell window, bypass `pack.ps1`'s preflight process
reclaimer and run the packaging step directly:

```powershell
cd M:/takoAI/AragonTrading/desktop
npx --no-install electron-builder --dir --config.electronDist=node_modules/electron/dist
```

Expected result: Windows keeps the running image file locked, so
`electron-builder` can fail with `EBUSY` when it tries to rewrite
`dist/win-unpacked/Aragon Trading.exe`.

### Minimal repro case

This smaller repro does not use Electron. It shows the same Windows file-lock
mechanism: a running `.exe` cannot be overwritten.

```powershell
cd M:/takoAI/AragonTrading
$dir = 'tmps/desktop-packaging-exe-lock-repro'
New-Item -ItemType Directory -Force -Path $dir | Out-Null
$src = (Get-Command node).Source
$exe = Join-Path $dir 'Aragon Trading.exe'
Copy-Item -Force $src $exe
$proc = Start-Process -FilePath (Resolve-Path $exe) `
  -ArgumentList '-e "setTimeout(() => {}, 20000)"' `
  -PassThru -WindowStyle Hidden
Start-Sleep -Seconds 2
node -e "const fs=require('fs'); fs.promises.writeFile(process.argv[1], Buffer.from('x')).catch(e=>{console.error(e.code + ': ' + e.message); process.exit(1);})" $exe
Stop-Process -Id $proc.Id -Force
```

Observed output:

```text
EBUSY: EBUSY: resource busy or locked, open
'M:\takoAI\AragonTrading\tmps\desktop-packaging-exe-lock-repro\Aragon Trading.exe'
```

## Root cause analysis

Primary reviewed root cause: the final packaging step is racing with a Windows
write lock on the product executable at the `addWinAsarIntegrity` write. The
evidence proves the target exe is locked at that late write point. It does not,
by itself, prove that a stale app instance was continuously running for the
entire build, because the local `electronDist` path also clears and recopies
`appOutDir` before the rename/write sequence.

The two explanations that fit the full trace are:

1. A `dist/win-unpacked` process was opened or re-opened after the early
   `pack.ps1` process check and before/during `electron-builder`.
2. Endpoint protection, indexing, or another short-lived Windows scanner held
   the newly created `Aragon Trading.exe` during the integrity-resource write.

Both cases produce the observed `EBUSY` at the same executable path, and both
are boundary cases not covered by the single early process check.

Evidence:

- `desktop/pack.ps1:225` runs `Stop-RunningDistProcesses` only once, before
  `build:backend` and `build:web`.
- `desktop/pack.ps1:241` then starts the PyInstaller backend build.
- `desktop/pack.ps1:245` then starts the Next standalone build.
- `desktop/pack.ps1:261` finally invokes `electron-builder`.

The user log shows these middle steps take long enough that the real
`electron-builder` write happens minutes after the only process-lock check.
There is no second check immediately before `desktop/pack.ps1:262`.

The current reclaimer is path-based:

- `desktop/pack.ps1:99` computes the `dist/win-unpacked` root.
- `desktop/pack.ps1:106` scans `Win32_Process`.
- `desktop/pack.ps1:111` checks `ExecutablePath`.
- `desktop/pack.ps1:115` checks `CommandLine`.
- `desktop/pack.ps1:147` force-stops matched processes.

That design handles a stale app running before the script starts, but it does
not cover a process that appears after `desktop/pack.ps1:225` and before
`desktop/pack.ps1:262`.

The `electron-builder` code path requires a writable product exe after copying
the Electron runtime:

- `desktop/node_modules/app-builder-lib/out/platformPackager.js:175` prepares
  the Electron stage directory.
- `desktop/node_modules/app-builder-lib/out/platformPackager.js:227` calls the
  framework `beforeCopyExtraFiles` hook.
- `desktop/node_modules/app-builder-lib/out/electron/ElectronFramework.js:145`
  enters the local `electronDist` branch used by the `pack.ps1` log
  (`--config.electronDist=node_modules/electron/dist`).
- `desktop/node_modules/app-builder-lib/out/electron/ElectronFramework.js:153`
  calls `emptyDir(appOutDir)`.
- `desktop/node_modules/app-builder-lib/out/electron/ElectronFramework.js:154`
  copies the Electron runtime into `dist/win-unpacked`.
- `desktop/node_modules/app-builder-lib/out/electron/ElectronFramework.js:41`
  computes the final `Aragon Trading.exe` path.
- `desktop/node_modules/app-builder-lib/out/electron/ElectronFramework.js:42`
  renames Electron's executable to the product executable.
- `desktop/node_modules/app-builder-lib/out/electron/ElectronFramework.js:44`
  calls `addWinAsarIntegrity`.
- `desktop/node_modules/app-builder-lib/out/electron/electronWin.js:34`
  writes the modified executable back to the same path.

If Windows has that target executable open as an image file or locked for
scanning, `writeFile` fails with the same `EBUSY` reported in the packaging log.
The `emptyDir`/`copyDir` citations are important: a stale app that was already
running before `electron-builder` entered this branch would often be expected to
fail during cleanup or copy. The reported stack instead lands at the later
integrity write, so the analysis should not overstate the stale-process case as
the only cause. The observed symptom is better described as a late executable
write lock, with stale/reopened app processes and scanner locks as the two
supported lock sources.

Counter-examples and alternatives checked:

- An app already running before `desktop/pack.ps1:225` is covered by the current
  early `Stop-RunningDistProcesses` call; if it cannot be stopped, lines
  `desktop/pack.ps1:151-162` fail after polling. That does not cover a process
  started after line 225.
- `winCodeSign` is not the failing component in this log: the
  `ensure:wincodesign` step succeeds before `electron-builder` starts, and the
  stack is in `electronWin.addWinAsarIntegrity`, not cache extraction.
- Backend and web build failures are ruled out by the log: both steps complete
  before the failure, and the failing path is under `desktop/dist/win-unpacked`.
- Deleting `dist/win-unpacked` manually before packaging is not a complete
  explanation or fix, because `electron-builder` already calls
  `emptyDir(appOutDir)` in the local `electronDist` branch and still can hit a
  later lock at `writeFile`.

Recent-change context:

- Commit `3ec104b` already documents a prior instance of this exact class:
  `pack.ps1: electron-builder hit EBUSY overwriting dist/win-unpacked while a
  prior packaged app was still running`.
- The current worktree adds `ensure:wincodesign` to avoid a different
  `winCodeSign` symlink failure. That step appears in the user log and succeeds;
  it does not write `dist/win-unpacked/Aragon Trading.exe`.

## Impact surface

- Affects Windows desktop packaging through `desktop/pack.ps1`,
  `npm run dist`, `npm run smoke`, and direct `electron-builder` runs.
- Blocks installer and portable artifact generation after expensive backend and
  web builds have already completed.
- Most likely to hit developers who keep the previous `win-unpacked` app open
  while repackaging, or machines where endpoint protection briefly locks new
  executable files.
- Does not affect the web app, FastAPI backend runtime, strategy logic, or OKX
  data correctness.

## Candidate fixes

| Fix | Summary | Invasiveness | Risk | Effort |
| --- | --- | --- | --- | --- |
| A | Move/re-run `Stop-RunningDistProcesses` immediately before `electron-builder`; keep the existing early check for `-Clean`. | Low | Low: reuses existing code and preserves current behavior. | Low |
| B | Wrap the `electron-builder` invocation in a small retry loop that, on `EBUSY` for `Aragon Trading.exe`, re-runs the process reclaimer, waits briefly, and retries once or twice. | Medium | Medium: must avoid hiding real packaging failures; output parsing must be conservative. | Medium |
| C | Always remove `desktop/dist/win-unpacked` immediately before packaging. | Medium | Medium-high: fails if locked, removes previous smoke artifact, and does not handle transient scanner locks after Electron copies the new exe. | Low |

## Recommended fix

Use Candidate A plus a narrow part of Candidate B.

The minimum reliable fix is a single `desktop/pack.ps1` change with two guards:

1. Call the existing `Stop-RunningDistProcesses` again immediately before the
   `electron-builder` invocation at `desktop/pack.ps1:262`. This closes the
   multi-minute race between the current early lock check and the actual
   executable write.
2. Replace the one-shot builder call with a bounded wrapper that captures
   builder output and retries only when the failed output contains both `EBUSY`
   and the product exe path/name (`Aragon Trading.exe`, preferably also
   `win-unpacked`). Before each retry, re-run `Stop-RunningDistProcesses`; if
   `-NoStopRunningDist` is set and a matching process exists, fail fast with the
   PID list instead of killing it. If no matching process exists, wait briefly
   and retry to cover scanner/indexer locks.

All non-matching `electron-builder` failures must keep the current fail-fast
behavior. The retry should be small and bounded (one or two retries) so a real
packaging/configuration error is not hidden behind repeated rebuild attempts.
This keeps the change scoped to `pack.ps1` and does not change backend, web, or
Electron runtime behavior.

Candidate C is not recommended as the primary fix. Deleting `win-unpacked`
before packaging is less targeted, still fails when the executable is already
locked, and does not address a post-copy transient lock at the exact
`addWinAsarIntegrity` write.

## 评审结论

Final recommended fix: implement the pre-builder reclaimer plus narrow
`EBUSY` retry as one scoped change in `desktop/pack.ps1`.

The evidence chain is strong for a late Windows write lock on
`desktop/dist/win-unpacked/Aragon Trading.exe`: the user log reaches
`electron-builder`, the stack lands in `ElectronFramework.beforeCopyExtraFiles`
and `electronWin.addWinAsarIntegrity`, and `electronWin.js:34` writes the
modified executable back to the same path. The existing process reclaimer is
real but runs only once at `desktop/pack.ps1:225`, before backend and web builds;
there is no guard immediately before `desktop/pack.ps1:262`, where the builder
is launched.

Candidate A alone is necessary but not sufficient because the observed stack can
also be explained by a transient scanner lock after Electron has copied and
renamed the exe. Candidate B alone is too broad unless it is restricted to the
exact `EBUSY` + product-exe signature. Candidate C is extra work and duplicates
`electron-builder`'s own `emptyDir(appOutDir)` while still missing post-copy
locks. Therefore the smallest robust fix is: re-run
`Stop-RunningDistProcesses` immediately before builder, then retry only the
specific `Aragon Trading.exe` `EBUSY` failure with a short bounded retry path
that preserves `-NoStopRunningDist` fail-fast semantics.
