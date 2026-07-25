param(
    [string]$RepoRoot = (Split-Path -Parent $PSScriptRoot),
    [string]$PythonExe = "D:\steam\steamapps\common\JOY OF PROGRAMMING\JoyOfProgramming\Content\000_MyContent\External\python-3.10.4-embed-amd64\python.exe",
    [string]$TaskName = "SafeExec BioLab Twin"
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path $RepoRoot).Path
$LevelScript = Join-Path $RepoRoot "joy\BioLab_Guardian.py"

foreach ($Required in @($PythonExe, $LevelScript)) {
    if (-not (Test-Path $Required)) {
        throw "Missing required file: $Required"
    }
}
$PythonwExe = Join-Path (Split-Path $PythonExe) "pythonw.exe"
$TaskPython = if (Test-Path $PythonwExe) { $PythonwExe } else { $PythonExe }

$Action = New-ScheduledTaskAction `
    -Execute $TaskPython `
    -Argument "`"$LevelScript`"" `
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
    -Description "SafeExec JOY BioLab digital-twin RPC process." `
    -Force | Out-Null

Start-ScheduledTask -TaskName $TaskName
Write-Host "Installed and started scheduled task: $TaskName"
