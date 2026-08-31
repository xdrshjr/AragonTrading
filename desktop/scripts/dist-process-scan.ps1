<#
.SYNOPSIS
    dist/win-unpacked 旧产物进程扫描（desktop/pack.ps1 的可点源函数库）。

.DESCRIPTION
    打包前必须先回收仍在运行的旧产物进程，否则 electron-builder 覆写
    "Aragon Trading.exe" 时会撞上 Windows 文件锁并以 EBUSY 失败。

    识别进程走 Get-Process（Win32 API），刻意不经 WMI。这是本文件存在的全部
    理由：WMI 在部分 Windows 主机上会进入「Winmgmt 服务显示 Running、但任何
    查询都永不返回」的卡死态，而旧实现直接 Get-CimInstance Win32_Process，
    于是整个打包在「2) desktop 依赖已就绪」之后无限挂起——不报错、不超时、
    也没有下一行日志，看上去像卡在了构建上。
    详见 docs/diagnoses/desktop-packaging-wmi-process-scan-hang/analysis.md。

    进程命令行只有 WMI 能提供，因此降级为「可选增强」：查询带
    -OperationTimeoutSec 硬超时，一旦超时或失败就整轮放弃、显式打印漏检范围，
    绝不阻塞打包。
#>

# WMI 一旦确认不可用就不再重试：一次打包里进程扫描会跑多轮（预检 + 打包前 +
# EBUSY 重试），逐轮干等同一个超时只是白白拖慢每一轮。
$script:DistScanCommandLineAvailable = $true
$script:DistScanDegradeReported = $false

function Write-DistScanDegradeNotice([string]$Reason) {
    if ($script:DistScanDegradeReported) { return }
    $script:DistScanDegradeReported = $true

    $trimmed = "$Reason".Trim()
    Write-Host "    WMI 进程命令行查询不可用（$trimmed）。" -ForegroundColor Yellow
    Write-Host '    已改用可执行文件路径识别旧产物进程。' -ForegroundColor Yellow
    Write-Host '    仅「exe 不在 win-unpacked 下、但命令行指向它」的进程会漏检；' -ForegroundColor Yellow
    Write-Host '    若打包仍报 EBUSY，请手动关闭旧产物后重试。' -ForegroundColor Yellow
}

function Get-DistProcessCommandLineMap {
    <#
    .SYNOPSIS
        取 PID -> CommandLine 映射。WMI 不可用时返回 $null，不抛异常。

    .PARAMETER TimeoutSeconds
        WMI 查询硬超时秒数。小于等于 0 表示完全跳过 WMI（只按 exe 路径识别）。
    #>
    param([int]$TimeoutSeconds = 8)

    if ($TimeoutSeconds -le 0) { return $null }
    if (-not $script:DistScanCommandLineAvailable) { return $null }

    try {
        $rows = @(Get-CimInstance -ClassName Win32_Process `
                -Property ProcessId, CommandLine `
                -OperationTimeoutSec $TimeoutSeconds `
                -ErrorAction Stop)
    }
    catch {
        $script:DistScanCommandLineAvailable = $false
        Write-DistScanDegradeNotice $_.Exception.Message
        return $null
    }

    $map = @{}
    foreach ($row in $rows) {
        $map[[int]$row.ProcessId] = [string]$row.CommandLine
    }
    return $map
}

function Get-RunningDistProcesses {
    <#
    .SYNOPSIS
        列出可执行文件（或命令行）位于 $UnpackedDir 之下的进程，按 PID 升序。

    .PARAMETER UnpackedDir
        免安装产物目录，通常是 desktop/dist/win-unpacked。目录不存在也安全。

    .PARAMETER CommandLineTimeoutSeconds
        命令行增强查询的 WMI 硬超时；传 0 表示只按可执行文件路径识别。
    #>
    param(
        [Parameter(Mandatory = $true)][string]$UnpackedDir,
        [int]$CommandLineTimeoutSeconds = 8
    )

    $root = [System.IO.Path]::GetFullPath($UnpackedDir).TrimEnd([char[]]"\/")
    $rootBackslash = "$root\"
    $rootForwardSlash = $root.Replace('\', '/') + '/'
    $comparison = [System.StringComparison]::OrdinalIgnoreCase

    $commandLines = Get-DistProcessCommandLineMap $CommandLineTimeoutSeconds
    $found = New-Object 'System.Collections.Generic.List[object]'

    foreach ($proc in @(Get-Process -ErrorAction SilentlyContinue)) {
        if ($proc.Id -eq $PID) { continue }

        # 受保护进程 / 位数不匹配时读 .Path 会抛，语义上等同「不在 dist 下」。
        $exe = $null
        try { $exe = [string]$proc.Path } catch { $exe = $null }

        $cmd = $null
        if ($commandLines) { $cmd = [string]$commandLines[[int]$proc.Id] }

        $isExeInDist = $exe -and $exe.StartsWith($rootBackslash, $comparison)
        $isCmdInDist = $cmd -and (
            $cmd.IndexOf($rootBackslash, $comparison) -ge 0 -or
            $cmd.IndexOf($rootForwardSlash, $comparison) -ge 0
        )
        if (-not ($isExeInDist -or $isCmdInDist)) { continue }

        [void]$found.Add([pscustomobject]@{
                ProcessId      = [int]$proc.Id
                Name           = [string]$proc.ProcessName
                ExecutablePath = $exe
                CommandLine    = $cmd
            })
    }

    return @($found | Sort-Object ProcessId)
}

function Format-DistProcessList($Processes) {
    <#
    .SYNOPSIS
        把进程列表排成人能直接照着关的多行摘要（PID / 映像名 / 路径）。
    #>
    return ($Processes |
        ForEach-Object {
            $path = if ($_.ExecutablePath) { $_.ExecutablePath } else { $_.CommandLine }
            "PID $($_.ProcessId) $($_.Name) $path"
        }) -join "`n    "
}
