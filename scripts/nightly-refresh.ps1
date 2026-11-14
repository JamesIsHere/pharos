# nightly-refresh.ps1 -- what the scheduled task runs (design.md section 6, rule 7).
# Runs `uv run pharos refresh` and keeps its output in data/logs/. The exit
# code is the refresh's, so Task Scheduler's "Last Run Result" tells the same
# story. There is no notification: a failure shows in `pharos health` (D50).
#
# Workshop mirror guard (open issue #9, D49): the weekly robocopy mirror
# (task "Workshop Mirror", Wednesday 06:00, 8 copy threads) must not copy
# serving/ while a publish is swapping CURRENT, or the backup can hold a
# pointer to a version folder it did not copy. Before starting, this waits
# while the mirror is running, and on a Wednesday from 05:00 until the mirror
# has run that day. The wait is capped; past the cap the refresh runs anyway.

# Watching a run (2026-11-11): -Watch opens a Windows Terminal window that
# shows this log as it grows. The refresh prints a line as each step starts
# and ends (src/pharos/progress.py), and every 30 seconds this script adds a
# heartbeat line: whether Python is alive, its memory, and its CPU time. CPU
# that stops climbing while no step line arrives means the run is stuck.
# -Command replaces the refresh (for testing this script on something short).

param([int]$MaxWaitMinutes = 180,
      [switch]$Watch,
      [string]$Command = 'uv run pharos refresh',
      [int]$HeartbeatSeconds = 30)

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

# Native abort hunt (2026-11-11): the fault handler prints every thread's
# Python stack when native code calls abort(), and unbuffered output streams
# progress lines here as they happen instead of only at a clean exit.
$env:PYTHONFAULTHANDLER = '1'
$env:PYTHONUNBUFFERED = '1'

if ($Watch) {
    Write-Log "watch window opened on $log"
    # one quoted string: Start-Process joins an array with bare spaces, and wt
    # then splits a spaced title into its command line
    Start-Process wt.exe -ArgumentList ("-w new --title `"Pharos run`" pwsh -NoProfile -NoExit " +
        "-Command `"Get-Content -LiteralPath '$log' -Wait`"")
}

# Python's stdout and stderr (the fault handler's stack) go to one side file;
# this script alone writes the log, copying new lines in every 2 seconds.
$out = "$log.out"
$env:PYTHONIOENCODING = 'utf-8'
Write-Log $Command
$proc = Start-Process cmd.exe -ArgumentList '/c', "$Command 1>`"$out`" 2>&1" -WorkingDirectory $root `
    -NoNewWindow -PassThru
$null = $proc.Handle   # keeps ExitCode readable after exit

function Get-RunPython {
    # the biggest python process under the run (uv starts it as a child)
    $all = Get-CimInstance Win32_Process -Property ProcessId, ParentProcessId, Name, WorkingSetSize
    $ids = @($proc.Id); $found = @()
    do {
        $kids = @($all | Where-Object { $ids -contains $_.ParentProcessId })
        $found += $kids; $ids = $kids.ProcessId
    } while ($kids.Count -gt 0)
    $found | Where-Object Name -like 'python*' | Sort-Object WorkingSetSize -Descending | Select-Object -First 1
}

$started = Get-Date
$nextBeat = $started.AddSeconds($HeartbeatSeconds)
$lastCpu = $null
$pending = ''
$reader = $null
while ($true) {
    $done = $proc.HasExited
    if (-not $reader -and (Test-Path $out)) {
        $fs = [IO.File]::Open($out, 'Open', 'Read', 'ReadWrite')
        $reader = [IO.StreamReader]::new($fs, [Text.Encoding]::UTF8)
    }
    if ($reader) {
        $pending += $reader.ReadToEnd()
        $cut = $pending.LastIndexOf("`n")
        if ($done) { $cut = $pending.Length - 1 }
        if ($cut -ge 0) {
            $lines = $pending.Substring(0, $cut + 1) -split "`r?`n" | Where-Object { $_ -ne '' }
            if ($lines) { $lines | Add-Content $log }
            $pending = $pending.Substring($cut + 1)
        }
    }
    if ($done) { break }
    if ((Get-Date) -ge $nextBeat) {
        $elapsed = (Get-Date) - $started
        $py = Get-RunPython
        if ($py) {
            $cpu = (Get-Process -Id $py.ProcessId -ErrorAction SilentlyContinue).TotalProcessorTime.TotalSeconds
            $delta = if ($null -ne $lastCpu) { ' (+{0:N0}s)' -f ($cpu - $lastCpu) } else { '' }
            $lastCpu = $cpu
            Write-Log ('heartbeat +{0:h\:mm\:ss}  python alive  {1:N1} GB  cpu {2:N0}s{3}' -f `
                $elapsed, ($py.WorkingSetSize / 1GB), $cpu, $delta)
        } else {
            Write-Log ('heartbeat +{0:h\:mm\:ss}  no python process' -f $elapsed)
        }
        $nextBeat = $nextBeat.AddSeconds($HeartbeatSeconds)
    }
    Start-Sleep -Seconds 2
}
if ($reader) { $reader.Close() }
Remove-Item $out -ErrorAction SilentlyContinue
$code = $proc.ExitCode
Write-Log "exit $code"
exit $code
