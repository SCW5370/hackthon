param()

$ErrorActionPreference = "Continue"
& (Join-Path $PSScriptRoot "stop_windows_legacy_bridge.ps1")
& (Join-Path $PSScriptRoot "stop_windows_guard.ps1")
& (Join-Path $PSScriptRoot "stop_biolab.ps1")
Write-Host "Windows SafeExec demo processes stopped."
