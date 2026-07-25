param(
    [string]$RepoRoot = (Split-Path -Parent $PSScriptRoot),
    [string]$GuardTaskName = "SafeExec Guard",
    [string]$BioLabTaskName = "SafeExec BioLab Twin",
    [int]$FailureThreshold = 5
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path $RepoRoot).Path
$RunDir = Join-Path $RepoRoot ".run"
$FailureFile = Join-Path $RunDir "guard-watchdog-failures.txt"
New-Item -ItemType Directory -Force -Path $RunDir | Out-Null

$Joy = Get-Process -Name "JoyOfProgramming-Win64-Shipping" `
    -ErrorAction SilentlyContinue |
    Select-Object -First 1
if ($null -eq $Joy) {
    # JOY is interactive and must be reopened by the operator.
    Remove-Item $FailureFile -ErrorAction SilentlyContinue
    exit 0
}

$BioLab = Get-CimInstance Win32_Process |
    Where-Object {
        $_.Name -match "^pythonw?\.exe$" -and
        $_.CommandLine -like "*BioLab_Guardian.py*"
    } |
    Select-Object -First 1
if ($null -eq $BioLab) {
    Stop-ScheduledTask -TaskName $BioLabTaskName -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 1
    Start-ScheduledTask -TaskName $BioLabTaskName
    Remove-Item $FailureFile -ErrorAction SilentlyContinue
    exit 0
}

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
