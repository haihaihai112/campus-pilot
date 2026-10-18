param([string]$BootstrapScript, [int]$BasePort = 8010)
$ErrorActionPreference = 'Stop'
if ($PSVersionTable.PSVersion.Major -lt 7) { throw 'Use PowerShell 7 (pwsh).' }
$taskRoot = Split-Path -Parent $PSScriptRoot
Set-Location $taskRoot
$taskPython = Join-Path $taskRoot '.venv/Scripts/python.exe'
$taskSsh = (Get-Command ssh -ErrorAction Stop).Source
$taskLogs = Join-Path $taskRoot ('work/https-' + (Get-Date -Format 'yyyyMMdd-HHmmss-fff'))
New-Item -ItemType Directory -Path $taskLogs | Out-Null
$taskState = Join-Path $taskRoot 'work/https-current.json'
if (Test-Path -LiteralPath $taskState) {
    throw 'Existing HTTPS state found. Run scripts/stop-https.ps1 before starting again.'
}
foreach ($taskPort in @($BasePort, ($BasePort+10), ($BasePort+20), ($BasePort+30))) {
    $taskListener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, $taskPort)
    $taskListener.Server.ExclusiveAddressUse = $true
    try { $taskListener.Start() } catch { throw "Port $taskPort is in use. Stop the existing CampusPilot launcher first." }
    finally { $taskListener.Stop() }
}
$env:PYTHONUTF8 = '1'
if (-not $BootstrapScript) { $env:CAMPUS_ENV_FILE = Join-Path $taskRoot '.env.pilot' }
$taskArgs = @(('"' + (Join-Path $PSScriptRoot 'serve_https.py') + '"'), '--base-port', "$BasePort")
if ($BootstrapScript) {
    $taskBootstrap = (Resolve-Path -LiteralPath $BootstrapScript).Path
    $taskArgs += @('--bootstrap', ('"' + $taskBootstrap + '"'))
}
$taskInfo = @{ log_dir=$taskLogs; base_port=$BasePort }
try {
    $taskKnown = Join-Path $taskRoot 'work/https-known-hosts'
    $taskSshArgs = @('-F','NUL','-T','-o','BatchMode=yes','-o','StrictHostKeyChecking=accept-new','-o',('UserKnownHostsFile="' + $taskKnown + '"'),'-o','IdentityFile=none','-o','IdentitiesOnly=yes','-o','ServerAliveInterval=30','-o','ServerAliveCountMax=3','-o','ConnectTimeout=15','-o','ExitOnForwardFailure=yes','-R',"80:127.0.0.1:$BasePort",'nokey@localhost.run')
    $taskTunnel = Start-Process -FilePath $taskSsh -ArgumentList $taskSshArgs -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $taskLogs 'tunnel.stdout.log') -RedirectStandardError (Join-Path $taskLogs 'tunnel.stderr.log')
    $taskInfo.tunnel_pid=$taskTunnel.Id
    $taskInfo.tunnel_started=$taskTunnel.StartTime.ToUniversalTime().ToString('o')
    $taskInfo | ConvertTo-Json | Set-Content -LiteralPath $taskState -Encoding utf8
    for ($taskAttempt=0; $taskAttempt -lt 40; $taskAttempt++) {
        if ($taskTunnel.HasExited) { throw 'Tunnel exited; inspect tunnel.stderr.log.' }
        $taskText = Get-Content -LiteralPath (Join-Path $taskLogs 'tunnel.stdout.log') -Raw -ErrorAction SilentlyContinue
        if ($taskText -match 'https://[a-zA-Z0-9-]+\.lhr\.life') {
            $taskInfo.url=$Matches[0]
            $taskInfo | ConvertTo-Json | Set-Content -LiteralPath $taskState -Encoding utf8
            break
        }
        Start-Sleep -Seconds 1
    }
    if (-not $taskInfo.url) { throw 'No HTTPS address received; inspect tunnel logs.' }
    $taskArgs += @('--public-origin', $taskInfo.url)
    $taskApp = Start-Process -FilePath $taskPython -ArgumentList $taskArgs -WorkingDirectory $taskRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $taskLogs 'app.stdout.log') -RedirectStandardError (Join-Path $taskLogs 'app.stderr.log')
    $taskInfo.app_pid=$taskApp.Id
    $taskInfo.app_started=$taskApp.StartTime.ToUniversalTime().ToString('o')
    $taskInfo | ConvertTo-Json | Set-Content -LiteralPath $taskState -Encoding utf8
    $taskReady = $false
    for ($taskAttempt=0; $taskAttempt -lt 60; $taskAttempt++) {
        $taskApp.Refresh()
        if ($taskApp.HasExited) { throw 'App exited; inspect app.stderr.log.' }
        try {
            $taskHealth = Invoke-RestMethod "http://127.0.0.1:$BasePort/health" -NoProxy -TimeoutSec 3
            if ($taskHealth.status -eq 'ok' -and $taskHealth.auth_mode -eq 'pilot') { $taskReady=$true; break }
        } catch { }
        Start-Sleep -Seconds 1
    }
    if (-not $taskReady) { throw 'App did not become healthy; inspect the logs.' }
    # Keep identities of every service even if the parent launcher later exits.
    $taskAll = @(Get-CimInstance Win32_Process)
    $taskTree = [System.Collections.Generic.List[int]]::new()
    $taskTree.Add($taskApp.Id)
    for ($taskIndex=0; $taskIndex -lt $taskTree.Count; $taskIndex++) {
        foreach ($taskChild in $taskAll | Where-Object { $_.ParentProcessId -eq $taskTree[$taskIndex] }) {
            if (-not $taskTree.Contains([int]$taskChild.ProcessId)) { $taskTree.Add([int]$taskChild.ProcessId) }
        }
    }
    $taskInfo.app_processes = @($taskTree | ForEach-Object {
        $taskProcess = Get-Process -Id $_ -ErrorAction Stop
        @{ pid=$taskProcess.Id; started=$taskProcess.StartTime.ToUniversalTime().ToString('o') }
    })
    $taskInfo | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $taskState -Encoding utf8
    Write-Output ($taskInfo.url + '/login')
    Write-Output "Logs: $taskLogs"
} catch {
    Write-Warning 'Startup incomplete. Run scripts/stop-https.ps1 to stop the recorded processes.'
    throw
}
