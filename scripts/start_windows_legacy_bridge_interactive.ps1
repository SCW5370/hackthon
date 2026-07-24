param(
    [Parameter(Mandatory = $true)]
    [string]$Token,
    [int]$Port = 8791
)

$ErrorActionPreference = "Stop"
$taskName = "SafeExec-Legacy-Interactive"
$startScript = Join-Path $PSScriptRoot "start_windows_legacy_bridge.ps1"
$arguments = (
    "-NoProfile -ExecutionPolicy Bypass -File `"$startScript`" " +
    "-Token `"$Token`" -Port $Port"
)
$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $arguments
$principal = New-ScheduledTaskPrincipal `
    -UserId $env:USERNAME `
    -LogonType Interactive `
    -RunLevel Limited

Unregister-ScheduledTask -TaskName $taskName -Confirm:$false `
    -ErrorAction SilentlyContinue
Register-ScheduledTask `
    -TaskName $taskName `
    -Action $action `
    -Principal $principal | Out-Null
Start-ScheduledTask -TaskName $taskName
Start-Sleep -Seconds 4
Unregister-ScheduledTask -TaskName $taskName -Confirm:$false

Write-Host "Requested UNSAFE Legacy bridge startup in the desktop session."
