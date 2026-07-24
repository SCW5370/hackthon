param()

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$pidFile = Join-Path $repoRoot ".run\biolab-level.pid"

if (-not (Test-Path -LiteralPath $pidFile)) {
    Write-Host "BioLab_Guardian is not recorded as running."
    exit 0
}

$levelPid = [int](Get-Content -Raw -LiteralPath $pidFile)
$process = Get-Process -Id $levelPid -ErrorAction SilentlyContinue
if ($process) {
    Stop-Process -Id $levelPid
    Write-Host "Stopped BioLab_Guardian (PID $levelPid)."
}
Remove-Item -LiteralPath $pidFile -ErrorAction SilentlyContinue
