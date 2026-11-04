# nightly-refresh.ps1 -- what the scheduled task runs (design.md section 6, rule 7).
# Runs `uv run pharos refresh` and keeps its output in data/logs/. The exit
# code is the refresh's, so Task Scheduler's "Last Run Result" tells the same
# story. There is no notification: a failure shows on the dashboard (D50).
#
# Workshop mirror guard (open issue #9, D49): the weekly robocopy mirror
# (task "Workshop Mirror", Wednesday 06:00, 8 copy threads) must not copy
# serving/ while a publish is swapping CURRENT, or the backup can hold a
# pointer to a version folder it did not copy. Before starting, this waits
# while the mirror is running, and on a Wednesday from 05:00 until the mirror
# has run that day. The wait is capped; past the cap the refresh runs anyway.

param([int]$MaxWaitMinutes = 180)

$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot
$logs = Join-Path $root 'data' 'logs'
New-Item -ItemType Directory -Force $logs | Out-Null
$log = Join-Path $logs ("nightly-{0:yyyyMMdd-HHmmss}.log" -f (Get-Date))

function Write-Log([string]$line) {
    "{0:yyyy-MM-dd HH:mm:ss}  {1}" -f (Get-Date), $line | Add-Content $log
}

function Test-MirrorInTheWay {
    $task = Get-ScheduledTask -TaskName 'Workshop Mirror' -ErrorAction SilentlyContinue
    if (-not $task) { return $false }   # no mirror registered, nothing to collide with
    if ($task.State -eq 'Running') { return $true }
    $now = Get-Date
    if ($now.DayOfWeek -ne 'Wednesday' -or $now.Hour -lt 5) { return $false }
    $lastRun = (Get-ScheduledTaskInfo -TaskName 'Workshop Mirror').LastRunTime
    return $lastRun -lt $now.Date.AddHours(6)
}

trap {
    # anything that stops the script (uv missing, the mirror query, a log
    # write) leaves its message and a nonzero exit, never a silent stop (#72)
    Write-Log "error: $_"
    exit 1
}

$deadline = (Get-Date).AddMinutes($MaxWaitMinutes)
if (Test-MirrorInTheWay) {
    Write-Log 'waiting for the Workshop Mirror task to finish'
    while ((Test-MirrorInTheWay) -and (Get-Date) -lt $deadline) { Start-Sleep -Seconds 60 }
    if (Test-MirrorInTheWay) {
        Write-Log "mirror still pending after $MaxWaitMinutes min; refreshing anyway"
    } else {
        Write-Log 'mirror done'
    }
}

Write-Log 'uv run pharos refresh'
Push-Location $root
try {
    & uv run pharos refresh *>&1 | ForEach-Object { "$_" } | Add-Content $log
    $code = $LASTEXITCODE
} finally {
    Pop-Location
}
Write-Log "exit $code"
exit $code
