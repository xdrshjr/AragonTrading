<#
.SYNOPSIS
    Aragon Trading 桌面端一键打包脚本（Windows / PowerShell）。

.DESCRIPTION
    双击或 `./pack.ps1` 即可把整套 Web 应用（apps/web + services/quant）打成
    可独立分发的桌面安装包。脚本封装 README「二、生产打包模式」的全流程并做前置自检：

      1. 校验 node / npm / python 在 PATH 上；
      2. 缺 services/quant/.venv 时自动 `pnpm setup`（建后端 venv + 装依赖）；
      3. 缺 desktop/node_modules 时自动 `npm install`；
      4. 运行 build:backend(PyInstaller) -> build:web(Next standalone) -> electron-builder；
      5. 默认复用本地 node_modules/electron/dist，规避受限网络下 111MB 重复下载卡死。

    全程不需要管理员特权 / 开发者模式（build:web 走隔离扁平副本根除 Windows EPERM symlink）。

.PARAMETER Target
    dist           （默认）出 NSIS 安装包 + portable -> dist/
    smoke          只出免安装目录 dist/win-unpacked/（build:backend+build:web+--dir）
    release-check  smoke 之后立即跑 verify:dist 端到端发布验收（联网，要求 live 真实数据）
    verify         仅对已构建产物跑 verify:dist（不重新构建）

.PARAMETER Clean
    构建前清掉 dist/、resources/{web,backend}/ 并对 .web-build/ 做全量重装（build:web:clean）。

.PARAMETER SkipSetup
    跳过 venv 自检 / pnpm setup（确信后端依赖已就绪时用）。

.PARAMETER SkipInstall
    跳过 desktop/ 的 npm install 自检。

.PARAMETER ElectronMirror
    设置 ELECTRON_MIRROR（如 https://registry.npmmirror.com/-/binary/electron/）。

.PARAMETER DownloadElectron
    不复用本地 electron，强制让 electron-builder 自行下载对应版本二进制。

.PARAMETER NoStopRunningDist
    默认打包前会回收仍从 dist/win-unpacked 运行的旧产物，避免 Windows EBUSY 文件锁。
    指定此开关后，若发现旧产物仍在运行则直接失败并打印 PID。

.EXAMPLE
    ./pack.ps1                       # 出安装包
    ./pack.ps1 -Target smoke         # 快速冒烟（免安装目录）
    ./pack.ps1 -Target release-check # 打包并端到端验收
    ./pack.ps1 -Clean                # 全量干净打包
    ./pack.ps1 -NoStopRunningDist    # 检测到旧 win-unpacked 正在运行时直接失败
    ./pack.ps1 -Target verify -- --allow-okx-down   # 离线只接受诚实 5xx 的验收
#>
#Requires -Version 5.1
[CmdletBinding()]
param(
    [ValidateSet('dist', 'smoke', 'release-check', 'verify')]
    [string]$Target = 'dist',
    [switch]$Clean,
    [switch]$SkipSetup,
    [switch]$SkipInstall,
    [string]$ElectronMirror,
    [switch]$DownloadElectron,
    [switch]$NoStopRunningDist,
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$ExtraArgs
)

$ErrorActionPreference = 'Stop'

# --- 路径与控制台编码（中文日志需 UTF-8，否则 GBK 控制台会乱码/抛错）---
$DesktopDir = $PSScriptRoot
$RepoRoot = Split-Path -Parent $DesktopDir
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}
$env:PYTHONUTF8 = '1'

function Write-Section([string]$Title) {
    Write-Host ''
    Write-Host "==> $Title" -ForegroundColor Cyan
}

function Fail([string]$Message, [int]$Code = 1) {
    Write-Host "[pack.ps1] ✗ $Message" -ForegroundColor Red
    exit $Code
}

# 运行原生命令并在非零退出码时中止（$LASTEXITCODE 是 native exe 的唯一可靠信号）。
function Invoke-Native {
    param([string]$Title, [scriptblock]$Action)
    Write-Section $Title
    & $Action
    if ($LASTEXITCODE -ne 0) {
        Fail "步骤失败：$Title (exit $LASTEXITCODE)" $LASTEXITCODE
    }
}

function Test-Tool([string]$Name) {
    return [bool](Get-Command $Name -ErrorAction SilentlyContinue)
}

function Get-RunningDistProcesses([string]$UnpackedDir) {
    $root = [System.IO.Path]::GetFullPath($UnpackedDir).TrimEnd([char[]]"\/")
    $rootBackslash = "$root\"
    $rootForward = $root.Replace('\', '/')
    $rootForwardSlash = "$rootForward/"
    $comparison = [System.StringComparison]::OrdinalIgnoreCase

    Get-CimInstance Win32_Process |
        Where-Object {
            $pidValue = [int]$_.ProcessId
            if ($pidValue -eq $PID) { return $false }

            $exe = [string]$_.ExecutablePath
            $cmd = [string]$_.CommandLine
            $exeInDist =
                $exe -and $exe.StartsWith($rootBackslash, $comparison)
            $cmdInDist =
                $cmd -and (
                    $cmd.IndexOf($rootBackslash, $comparison) -ge 0 -or
                    $cmd.IndexOf($rootForwardSlash, $comparison) -ge 0
                )

            return $exeInDist -or $cmdInDist
        } |
        Sort-Object ProcessId
}

function Format-DistProcessList($Processes) {
    return ($Processes |
        ForEach-Object {
            $path = if ($_.ExecutablePath) { $_.ExecutablePath } else { $_.CommandLine }
            "PID $($_.ProcessId) $($_.Name) $path"
        }) -join "`n    "
}

function Stop-RunningDistProcesses([string]$UnpackedDir, [bool]$StopProcesses) {
    $running = @(Get-RunningDistProcesses $UnpackedDir)
    if (-not $running) { return }

    $summary = Format-DistProcessList $running
    if (-not $StopProcesses) {
        Fail "dist/win-unpacked 仍有旧产物进程在运行：`n    $summary`n请先关闭后重试，或去掉 -NoStopRunningDist 让脚本自动回收。" 6
    }

    Write-Section '3) 回收正在运行的旧 win-unpacked 产物'
    Write-Host "    发现旧产物进程，将先停止以释放 Windows 文件锁：" -ForegroundColor DarkGray
    Write-Host "    $summary" -ForegroundColor DarkGray

    foreach ($proc in $running) {
        Stop-Process -Id $proc.ProcessId -Force -ErrorAction SilentlyContinue
    }

    $deadline = (Get-Date).AddSeconds(12)
    do {
        Start-Sleep -Milliseconds 250
        $remaining = @(Get-RunningDistProcesses $UnpackedDir)
        if (-not $remaining) {
            Write-Host '    旧产物进程已释放 ✓' -ForegroundColor DarkGray
            return
        }
    } while ((Get-Date) -lt $deadline)

    $remainingSummary = Format-DistProcessList $remaining
    Fail "旧 win-unpacked 进程未能退出：`n    $remainingSummary`n请手动关闭后重试。" 6
}

Write-Host '────────────────────────────────────────────' -ForegroundColor DarkGray
Write-Host ' Aragon Trading 桌面端打包  (desktop/pack.ps1)' -ForegroundColor Green
Write-Host "  Target = $Target   Repo = $RepoRoot" -ForegroundColor DarkGray
Write-Host '────────────────────────────────────────────' -ForegroundColor DarkGray

# --- 0) 工具链自检 ---
Write-Section '0) 工具链自检 (node / npm / python)'
foreach ($tool in 'node', 'npm') {
    if (-not (Test-Tool $tool)) { Fail "未找到 $tool，请先安装 Node.js (≥18.18) 并加入 PATH。" 2 }
}
if (-not (Test-Tool 'python')) { Fail '未找到 python，请先安装 Python 3.11 并加入 PATH。' 2 }
$hasPnpm = Test-Tool 'pnpm'
Write-Host "    node $(node -v) · npm $(npm -v) · python $(python --version 2>&1)" -ForegroundColor DarkGray
Write-Host "    pnpm: $(if ($hasPnpm) { (pnpm -v) } else { '未安装（首装将回退 python scripts/dev.py setup）' })" -ForegroundColor DarkGray

if ($ElectronMirror) {
    $env:ELECTRON_MIRROR = $ElectronMirror
    Write-Host "    ELECTRON_MIRROR = $ElectronMirror" -ForegroundColor DarkGray
}

Push-Location $DesktopDir
try {
    # --- 1) 后端 venv 自检（build:backend 依赖 services/quant/.venv） ---
    if (-not $SkipSetup) {
        $venvPy = Join-Path $RepoRoot 'services/quant/.venv/Scripts/python.exe'
        if (-not (Test-Path $venvPy)) {
            Write-Section '1) 后端虚拟环境缺失 -> 运行一次性 setup（建 venv + 装依赖）'
            Push-Location $RepoRoot
            try {
                if ($hasPnpm) { Invoke-Native 'pnpm setup' { pnpm setup } }
                else { Invoke-Native 'python scripts/dev.py setup' { python scripts/dev.py setup } }
            }
            finally { Pop-Location }
        }
        else {
            Write-Host ''
            Write-Host '1) 后端虚拟环境已就绪 ✓' -ForegroundColor DarkGray
        }
    }

    # --- 2) desktop 依赖自检 ---
    if (-not $SkipInstall) {
        if (-not (Test-Path (Join-Path $DesktopDir 'node_modules'))) {
            Invoke-Native '2) 安装 desktop 依赖 (npm install)' { npm install }
        }
        else {
            Write-Host '2) desktop 依赖已就绪 ✓' -ForegroundColor DarkGray
        }
    }

    # --- 3) verify 目标：仅验收已构建产物，直接执行并退出 ---
    if ($Target -eq 'verify') {
        $verifyArgs = @('run', 'verify:dist')
        if ($ExtraArgs) { $verifyArgs += '--'; $verifyArgs += $ExtraArgs }
        Invoke-Native '3) 发布验收 (verify:dist)' { npm @verifyArgs }
        Write-Host ''
        Write-Host '[pack.ps1] ✓ 验收完成。' -ForegroundColor Green
        exit 0
    }

    Stop-RunningDistProcesses `
        (Join-Path $DesktopDir 'dist/win-unpacked') `
        (-not $NoStopRunningDist)

    # --- 3) 可选清理 ---
    if ($Clean) {
        Write-Section '3) 清理旧产物 (-Clean)'
        foreach ($rel in 'dist', 'resources/web', 'resources/backend') {
            $p = Join-Path $DesktopDir $rel
            if (Test-Path $p) {
                Write-Host "    rm $rel" -ForegroundColor DarkGray
                Remove-Item -Recurse -Force $p
            }
        }
    }

    # --- 4) 构建后端 (PyInstaller onedir + /api/health 自检冒烟) ---
    Invoke-Native '4) 构建后端 (build:backend / PyInstaller)' { npm run build:backend }

    # --- 5) 构建前端 (隔离扁平副本 + Next standalone) ---
    $webScript = if ($Clean) { 'build:web:clean' } else { 'build:web' }
    Invoke-Native "5) 构建前端 (npm run $webScript / Next standalone)" { npm run $webScript }

    # --- 6) 打包：默认复用本地 electron 二进制，规避 111MB 重复下载卡死 ---
    $builderArgs = @()
    if ($Target -eq 'smoke') { $builderArgs += '--dir' }   # 免安装目录
    $localElectron = Join-Path $DesktopDir 'node_modules/electron/dist'
    if ((-not $DownloadElectron) -and (Test-Path $localElectron)) {
        $builderArgs += '--config.electronDist=node_modules/electron/dist'
        Write-Host '    复用本地 electron 二进制（--config.electronDist），跳过下载。' -ForegroundColor DarkGray
    }
    $stepTitle = if ($Target -eq 'smoke') { '6) 打包免安装目录 (electron-builder --dir)' } else { '6) 打包安装包 (electron-builder)' }
    Invoke-Native $stepTitle { npx --no-install electron-builder @builderArgs }

    # --- 7) release-check：构建后立刻端到端验收 ---
    if ($Target -eq 'release-check') {
        $verifyArgs = @('run', 'verify:dist')
        if ($ExtraArgs) { $verifyArgs += '--'; $verifyArgs += $ExtraArgs }
        Invoke-Native '7) 发布验收 (verify:dist)' { npm @verifyArgs }
    }

    # --- 完成 ---
    Write-Host ''
    Write-Host '────────────────────────────────────────────' -ForegroundColor Green
    Write-Host '[pack.ps1] ✓ 打包完成' -ForegroundColor Green
    $distDir = Join-Path $DesktopDir 'dist'
    if (Test-Path $distDir) {
        Write-Host '  产物 (desktop/dist/):' -ForegroundColor DarkGray
        Get-ChildItem $distDir -File -ErrorAction SilentlyContinue |
            Where-Object { $_.Extension -in '.exe', '.msi', '.zip', '.blockmap' } |
            ForEach-Object { Write-Host ("    {0,10:N1} MB  {1}" -f ($_.Length / 1MB), $_.Name) -ForegroundColor DarkGray }
        $unpacked = Join-Path $distDir 'win-unpacked'
        if (Test-Path $unpacked) { Write-Host "    [dir]       win-unpacked\  （免安装产物，可 verify:dist 复验）" -ForegroundColor DarkGray }
    }
    Write-Host '────────────────────────────────────────────' -ForegroundColor Green
}
catch {
    Fail $_.Exception.Message 1
}
finally {
    Pop-Location
}
