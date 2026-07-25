param()

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$pidFile = Join-Path $repoRoot ".run\safeexec-guard.pid"

$guardPids = @()
if (Test-Path -LiteralPath $pidFile) {
    $guardPids += [int](Get-Content -Raw -LiteralPath $pidFile)
}
$guardPids += Get-CimInstance Win32_Process |
    Where-Object {
        $_.Name -like "python*" -and
        $_.CommandLine -like "*run_windows_guard.py*"
    } |
    ForEach-Object { $_.ProcessId }
$guardPids = @($guardPids | Sort-Object -Unique)

foreach ($guardPid in $guardPids) {
    $process = Get-Process -Id $guardPid -ErrorAction SilentlyContinue
    if ($process) {
        Stop-Process -Id $guardPid
        Write-Host "Stopped SafeExec Guard (PID $guardPid)."
    }
}
if ($guardPids.Count -eq 0) {
    Write-Host "SafeExec Guard is not running."
}
Remove-Item -LiteralPath $pidFile -ErrorAction SilentlyContinue
