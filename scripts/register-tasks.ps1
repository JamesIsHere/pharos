# register-tasks.ps1 -- registers the nightly refresh in Task Scheduler and puts
# the Pharos icon on the desktop. Safe to re-run: both are replaced in place.
#
# Task "Pharos Nightly Refresh": daily 07:30 UTC (02:30 EST / 03:30 EDT), run
# as soon as possible after a missed start, only while you are logged on (no
# stored password). Pinned to UTC, not local time, so it stays after SEC's
# nightly submissions.zip rebuild (06:21 UTC on 2026-11-04) across DST (D63).
# Keeps the publish inside C01's 26h window and clear of the Wednesday 06:00
# local workshop mirror (D49).

$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot
$pwsh = (Get-Command pwsh).Source
$taskName = 'Pharos Nightly Refresh'

$action = New-ScheduledTaskAction -Execute $pwsh -WorkingDirectory $root `
    -Argument "-NoProfile -WindowStyle Hidden -File `"$(Join-Path $PSScriptRoot 'nightly-refresh.ps1')`""
$trigger = New-ScheduledTaskTrigger -Daily -At '02:30'
$trigger.StartBoundary = (Get-Date).Date.ToString('yyyy-MM-dd') + 'T07:30:00Z'   # UTC pin (D63)
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Hours 5) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive

Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings `
    -Principal $principal -Description 'uv run pharos refresh; failures show on the dashboard. Registered by pharos\scripts\register-tasks.ps1' `
    -Force | Out-Null
"registered task '$taskName'"

$shortcut = Join-Path ([Environment]::GetFolderPath('Desktop')) 'Pharos.lnk'
$lnk = (New-Object -ComObject WScript.Shell).CreateShortcut($shortcut)
$lnk.TargetPath = $pwsh
$lnk.Arguments = "-NoProfile -WindowStyle Hidden -File `"$(Join-Path $PSScriptRoot 'open-dashboard.ps1')`""
$lnk.WorkingDirectory = $root
$lnk.IconLocation = Join-Path $PSScriptRoot 'pharos.ico'
$lnk.WindowStyle = 7   # minimized, so the console doesn't flash up
$lnk.Description = 'Open the Pharos dashboard'
$lnk.Save()
"wrote $shortcut"
