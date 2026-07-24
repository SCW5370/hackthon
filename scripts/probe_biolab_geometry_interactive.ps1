param()

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$resultFile = Join-Path $repoRoot ".run\geometry.result.json"
$runner = Join-Path $PSScriptRoot "probe_biolab_geometry.py"
$taskName = "SafeExec-BioLab-Geometry-Probe"

$joy = Get-Process -Name "JoyOfProgramming-Win64-Shipping" |
    Select-Object -First 1
if (-not $joy -or -not $joy.Path) {
    throw "JOY is not running."
}
$joyProject = Split-Path (Split-Path (Split-Path $joy.Path))
$python = Join-Path $joyProject `
    "Content\000_MyContent\External\python-3.10.4-embed-amd64\python.exe"

Remove-Item $resultFile -ErrorAction SilentlyContinue
$action = New-ScheduledTaskAction -Execute $python -Argument "`"$runner`""
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

Write-Host "Started read-only BioLab geometry probe in the desktop session."
