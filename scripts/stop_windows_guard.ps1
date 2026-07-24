param()

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$pidFile = Join-Path $repoRoot ".run\safeexec-guard.pid"

if (-not (Test-Path -LiteralPath $pidFile)) {
    Write-Host "SafeExec Guard is not recorded as running."
    exit 0
}

$guardPid = [int](Get-Content -Raw -LiteralPath $pidFile)
$process = Get-Process -Id $guardPid -ErrorAction SilentlyContinue
if ($process) {
    Stop-Process -Id $guardPid
    Write-Host "Stopped SafeExec Guard (PID $guardPid)."
}
Remove-Item -LiteralPath $pidFile -ErrorAction SilentlyContinue
