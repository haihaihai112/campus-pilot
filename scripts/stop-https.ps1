$ErrorActionPreference = 'Stop'
if ($PSVersionTable.PSVersion.Major -lt 7) { throw 'Use PowerShell 7 (pwsh).' }
$taskRoot = Split-Path -Parent $PSScriptRoot
$taskState = Join-Path $taskRoot 'work/https-current.json'
if (-not (Test-Path -LiteralPath $taskState)) { Write-Output 'No recorded HTTPS deployment.'; return }
$taskInfo = Get-Content -LiteralPath $taskState -Raw | ConvertFrom-Json
# Stop the tunnel first. Match creation time as well as PID to avoid PID reuse.
foreach ($taskKind in @('tunnel','app')) {
    if ($taskKind -eq 'app' -and $taskInfo.app_processes) {
        $taskRecorded = @($taskInfo.app_processes)
        for ($taskIndex=$taskRecorded.Count-1; $taskIndex -ge 0; $taskIndex--) {
            $taskRecord = $taskRecorded[$taskIndex]
            $taskProcess = Get-Process -Id $taskRecord.pid -ErrorAction SilentlyContinue
            if (-not $taskProcess) { continue }
            $taskCreated = ([datetime]$taskRecord.started).ToUniversalTime()
            if ([Math]::Abs(($taskProcess.StartTime.ToUniversalTime() - $taskCreated).TotalSeconds) -gt 1) {
                throw "PID $($taskRecord.pid) was reused; refusing to stop it."
            }
            Stop-Process -Id $taskRecord.pid -ErrorAction SilentlyContinue
        }
        continue
    }
    $taskId = $taskInfo."${taskKind}_pid"
    if (-not $taskId) { continue }
    $taskProcess = Get-Process -Id $taskId -ErrorAction SilentlyContinue
    if (-not $taskProcess) { continue }
    $taskCreated = ([datetime]$taskInfo."${taskKind}_started").ToUniversalTime()
    if ([Math]::Abs(($taskProcess.StartTime.ToUniversalTime() - $taskCreated).TotalSeconds) -gt 1) {
        throw "PID $taskId was reused; refusing to stop it."
    }
    # Snapshot descendants before stopping parents. No image-wide process termination.
    $taskAll = @(Get-CimInstance Win32_Process)
    $taskTree = [System.Collections.Generic.List[int]]::new()
    $taskTree.Add([int]$taskId)
    for ($taskIndex=0; $taskIndex -lt $taskTree.Count; $taskIndex++) {
        foreach ($taskChild in $taskAll | Where-Object { $_.ParentProcessId -eq $taskTree[$taskIndex] }) {
            if (-not $taskTree.Contains([int]$taskChild.ProcessId)) { $taskTree.Add([int]$taskChild.ProcessId) }
        }
    }
    for ($taskIndex=$taskTree.Count-1; $taskIndex -ge 0; $taskIndex--) {
        Stop-Process -Id $taskTree[$taskIndex] -ErrorAction SilentlyContinue
    }
}
# Do not discard the active record while any service port is still occupied.
foreach ($taskPort in @($taskInfo.base_port, ($taskInfo.base_port+10), ($taskInfo.base_port+20), ($taskInfo.base_port+30))) {
    $taskListener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, $taskPort)
    $taskListener.Server.ExclusiveAddressUse = $true
    try { $taskListener.Start() } catch { throw "Port $taskPort is still occupied; state retained for inspection." }
    finally { $taskListener.Stop() }
}
Move-Item -LiteralPath $taskState -Destination (Join-Path $taskInfo.log_dir 'stopped-state.json')
Write-Output 'HTTPS tunnel and its CampusPilot services stopped; logs and data retained.'
