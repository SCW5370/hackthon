param(
    [string]$RepoRoot = (Split-Path -Parent $PSScriptRoot),
    [string]$PythonExe = "D:\steam\steamapps\common\JOY OF PROGRAMMING\JoyOfProgramming\Content\000_MyContent\External\python-3.10.4-embed-amd64\python.exe",
    [string]$TaskName = "SafeExec Guard",
    [int]$Port = 8788,
    [int]$JoyPort = 18189
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path $RepoRoot).Path
$Runner = Join-Path $RepoRoot "scripts\run_windows_guard.py"
$PublicKey = Join-Path $RepoRoot ".run\public_key.txt"

foreach ($Required in @($PythonExe, $Runner, $PublicKey)) {
    if (-not (Test-Path $Required)) {
        throw "Missing required file: $Required"
    }
}

$Arguments = @(
    "`"$Runner`""
    "--key", "`"$PublicKey`""
    "--executor", "joy"
    "--host", "0.0.0.0"
    "--port", "$Port"
    "--joy-host", "127.0.0.1"
    "--joy-port", "$JoyPort"
) -join " "

$Action = New-ScheduledTaskAction `
    -Execute $PythonExe `
    -Argument $Arguments `
    -WorkingDirectory $RepoRoot
$Trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$Principal = New-ScheduledTaskPrincipal `
    -UserId $env:USERNAME `
    -LogonType Interactive `
    -RunLevel Highest
$Settings = New-ScheduledTaskSettingsSet `
    -RestartCount 999 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $Action `
    -Trigger $Trigger `
    -Principal $Principal `
    -Settings $Settings `
    -Description "SafeExec device-boundary Guard; reconnects to JOY locally." `
    -Force | Out-Null

Start-ScheduledTask -TaskName $TaskName
Write-Host "Installed and started scheduled task: $TaskName"
