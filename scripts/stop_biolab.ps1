param()

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$pidFile = Join-Path $repoRoot ".run\biolab-level.pid"

$levelPids = @()
if (Test-Path -LiteralPath $pidFile) {
    $levelPids += [int](Get-Content -Raw -LiteralPath $pidFile)
}
$levelPids += Get-CimInstance Win32_Process |
    Where-Object {
        $_.Name -like "python*" -and
        $_.CommandLine -like "*BioLab_Guardian.py*"
    } |
    ForEach-Object { $_.ProcessId }
$levelPids = @($levelPids | Sort-Object -Unique)

foreach ($levelPid in $levelPids) {
    $process = Get-Process -Id $levelPid -ErrorAction SilentlyContinue
    if ($process) {
        Stop-Process -Id $levelPid
        Write-Host "Stopped BioLab_Guardian (PID $levelPid)."
    }
}
if ($levelPids.Count -eq 0) {
    Write-Host "BioLab_Guardian is not running."
}
Remove-Item -LiteralPath $pidFile -ErrorAction SilentlyContinue
