param(
    [string]$RepoRoot = (Split-Path -Parent $PSScriptRoot),
    [string]$GuardTaskName = "SafeExec Guard",
    [int]$FailureThreshold = 2
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path $RepoRoot).Path
$RunDir = Join-Path $RepoRoot ".run"
$FailureFile = Join-Path $RunDir "guard-watchdog-failures.txt"
New-Item -ItemType Directory -Force -Path $RunDir | Out-Null

try {
    $Status = Invoke-RestMethod `
        -Uri "http://127.0.0.1:8788/readyz" `
        -TimeoutSec 4
    if ($Status.ready -eq $true) {
        Remove-Item $FailureFile -ErrorAction SilentlyContinue
        exit 0
    }
} catch {
    # Count the failure below.
}

$BioLab = Get-CimInstance Win32_Process |
    Where-Object {
        $_.Name -like "python*" -and
        $_.CommandLine -like "*BioLab_Guardian.py*"
    } |
    Select-Object -First 1
if ($null -eq $BioLab) {
    # JOY scene is absent; restarting Guard cannot repair that condition.
    Remove-Item $FailureFile -ErrorAction SilentlyContinue
    exit 0
}

$Failures = 0
if (Test-Path $FailureFile) {
    $Failures = [int](Get-Content -Raw $FailureFile)
}
$Failures += 1
$Failures | Set-Content -Encoding ASCII $FailureFile
if ($Failures -lt $FailureThreshold) {
    exit 0
}

Stop-ScheduledTask -TaskName $GuardTaskName -ErrorAction SilentlyContinue
Start-Sleep -Seconds 1
Start-ScheduledTask -TaskName $GuardTaskName
Remove-Item $FailureFile -ErrorAction SilentlyContinue
