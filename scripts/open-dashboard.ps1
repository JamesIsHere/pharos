# open-dashboard.ps1 -- what the desktop icon runs (design.md D9).
# Starts the Streamlit server if it isn't already answering, then opens the
# dashboard in an Edge app window. It never refreshes data: opening the app
# only reads serving/ and health/.
#
# The server keeps running after the window closes, so the next open is warm.
# Port 8517, not Streamlit's default 8501, so another project's app on 8501 is
# never mistaken for this one.

param([int]$Port = 8517, [int]$TimeoutSeconds = 30)

$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot
$url = "http://localhost:$Port"
$logs = Join-Path $root 'data' 'logs'

function Test-Server {
    try {
        (Invoke-WebRequest "$url/_stcore/health" -TimeoutSec 1).Content -eq 'ok'
    } catch {
        $false   # not answering yet is the expected state while it starts
    }
}

function Show-Error([string]$message) {
    Add-Type -AssemblyName System.Windows.Forms
    [System.Windows.Forms.MessageBox]::Show($message, 'Pharos', 'OK', 'Error') | Out-Null
}

if (-not (Test-Server)) {
    New-Item -ItemType Directory -Force $logs | Out-Null
    Start-Process uv -WorkingDirectory $root -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $logs 'streamlit.out.log') `
        -RedirectStandardError (Join-Path $logs 'streamlit.err.log') `
        -ArgumentList 'run', 'streamlit', 'run', 'app/Home.py',
                      '--server.headless', 'true', '--server.port', $Port,
                      '--browser.gatherUsageStats', 'false'
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while (-not (Test-Server)) {
        if ((Get-Date) -gt $deadline) {
            Show-Error "The dashboard server did not start within $TimeoutSeconds s. See $logs\streamlit.err.log"
            exit 1
        }
        Start-Sleep -Milliseconds 200
    }
}

$edge = Join-Path ${env:ProgramFiles(x86)} 'Microsoft' 'Edge' 'Application' 'msedge.exe'
Start-Process $edge "--app=$url"
