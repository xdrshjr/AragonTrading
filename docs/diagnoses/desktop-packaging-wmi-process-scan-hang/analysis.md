# Desktop packaging hangs forever on the WMI process scan

## Problem description

`desktop/pack.ps1` stops making progress immediately after the dependency
self-checks and never recovers — no error, no timeout, no further output:

```text
==> 0) 工具链自检 (node / npm / python)
    node v22.18.0 · npm 10.9.3 · python Python 3.11.7
    pnpm: 10.29.1

1) 后端虚拟环境已就绪 ✓
2) desktop 依赖已就绪 ✓
<hangs indefinitely>
```

The next line the script would print is the `==> 4) 构建后端` section header
(or `==> 3) 回收正在运行的旧 win-unpacked 产物` when an old build is running).
Neither ever appears, so nothing in the actual build pipeline — PyInstaller,
Next standalone, electron-builder — has started yet. This is *not* the earlier
`winCodeSign` EPERM or `EBUSY` exe-lock failure; those both happen much later
and both fail loudly.

## Reproduction steps

### Direct project reproduction

On a Windows host whose WMI is wedged (see below):

```powershell
cd M:/takoAI/AragonTrading/desktop
./pack.ps1
```

Observed: output stops after `2) desktop 依赖已就绪 ✓` and the process must be
killed manually.

### Minimal repro case

The single statement the script blocks on, in isolation:

```powershell
Get-CimInstance Win32_Process
# never returns — killed after >10 minutes with zero output
```

Even the smallest possible WMI query is wedged the same way:

```powershell
$j = Start-Job { Get-CimInstance Win32_OperatingSystem }
Wait-Job $j -Timeout 25    # times out
```

While the service itself claims to be healthy:

```powershell
Get-Service Winmgmt | Select-Object Name, Status, StartType
# Name: Winmgmt   Status: Running   StartType: Automatic
```

## Root cause analysis

Between step 2 and step 4, `pack.ps1` runs a pre-flight that reclaims any
process still running out of `dist/win-unpacked`, so that `electron-builder`
does not hit a Windows file lock when it overwrites `Aragon Trading.exe`
(the `desktop-packaging-ebusy-exe-lock` diagnosis). That pre-flight enumerated
processes through WMI:

```powershell
function Get-RunningDistProcesses([string]$UnpackedDir) {
    ...
    Get-CimInstance Win32_Process |          # <-- blocks forever
        Where-Object { ... } |
        Sort-Object ProcessId
}
```

`Get-CimInstance` with no `-OperationTimeoutSec` waits on the WMI service with
no client-side deadline. On a host where the WMI repository/service has wedged
— a common, self-inflicted Windows state in which `Winmgmt` reports `Running`
but answers no query — the call simply never returns.

Two properties turned a degraded-but-optional convenience into a total build
stoppage:

1. **The dependency was mandatory, not best-effort.** The only thing the scan
   actually needs is "which processes are running out of this directory", and
   that is available from `Get-Process` (Win32 API, no WMI, 531 processes in
   69 ms on the same host). WMI was used only because it additionally exposes
   `CommandLine`, which is a secondary matching signal — the process holding
   the file lock is by definition the one whose *image path* is under
   `win-unpacked`.
2. **The failure was silent and unbounded.** No timeout, no progress line, and
   the pre-flight sits between two success messages, so the symptom reads as
   "packaging is stuck building" rather than "packaging never started".

## Impact surface

- `desktop/pack.ps1` — all four targets (`dist`, `smoke`, `release-check`,
  and `verify` when an old build is running) block before any build step.
- The scan is called up to three times per run (pre-flight, pre-package, and
  inside the `EBUSY` retry loop), so any per-call stall is paid repeatedly.
- Nothing else in the packaging pipeline touches WMI. `src/lib/process-manager.js`
  uses `tasklist`/`netstat`, which do not go through the WMI service, and are
  runtime concerns rather than build ones.

## Recommended fix (implemented)

Move the scan into `desktop/scripts/dist-process-scan.ps1`, a dot-sourceable
library that `pack.ps1` loads, and invert the dependency:

- **Primary path is `Get-Process`.** Image paths come from the Win32 API and
  are matched case-insensitively against the `win-unpacked` prefix. Reading
  `.Path` on a protected process throws; that is caught and treated as "not
  under dist", which is the correct semantics.
- **`CommandLine` becomes an optional enhancement.** It is still fetched from
  WMI, but with `-OperationTimeoutSec` (default 8s), which does bound a wedged
  provider — verified at 8.155 s against the same host that hangs forever
  without it. `Get-RunningDistProcesses -CommandLineTimeoutSeconds 0` skips WMI
  entirely.
- **Degradation is announced, not silent.** The first failure prints what broke
  and exactly what is now under-detected ("exe not under win-unpacked but
  command line points into it"), then caches the verdict so subsequent calls in
  the same run do not each re-pay the timeout.

Extracting the library also makes the behaviour testable, which it was not
while it lived inline in a script with a top-level body.

## Regression tests

`desktop/scripts/dist-process-scan.test.mjs` (`node --test`, part of
`npm test`) drives real `powershell.exe` processes:

- the scan returns inside a 60 s budget — on the wedged host it returns in
  ~8.6 s, where the previous implementation returned never;
- a real child process launched from a temporary `win-unpacked/Aragon Trading.exe`
  is found with `-CommandLineTimeoutSeconds 0`, proving the primary path needs
  no WMI at all;
- `Format-DistProcessList` falls back to the command line when the image path
  is unreadable, so the "close these processes" summary is never blank;
- a source gate fails if `pack.ps1` ever reintroduces a direct
  `Get-CimInstance` / `Get-WmiObject` / `Win32_Process` call.

## Note on the host's WMI

The fix makes packaging immune to it, but the wedged WMI itself is a real
machine-level fault and will affect other tools (Task Manager details columns,
`Get-CimInstance`-based scripts, some installers). Repairing it is separate
from this build fix; the usual first steps are `winmgmt /verifyrepository` and
`winmgmt /salvagerepository` from an elevated prompt.
