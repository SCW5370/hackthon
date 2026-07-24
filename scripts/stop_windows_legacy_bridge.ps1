param()

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$pidFile = Join-Path $repoRoot ".run\legacy-bridge.pid"

if (-not (Test-Path -LiteralPath $pidFile)) {
    Write-Host "Legacy bridge is not recorded as running."
    exit 0
}

$bridgePid = [int](Get-Content -Raw -LiteralPath $pidFile)
$process = Get-Process -Id $bridgePid -ErrorAction SilentlyContinue
if ($process) {
    Stop-Process -Id $bridgePid
    Write-Host "Stopped Legacy bridge (PID $bridgePid)."
}
Remove-Item -LiteralPath $pidFile -ErrorAction SilentlyContinue
