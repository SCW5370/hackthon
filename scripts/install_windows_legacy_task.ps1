param(
    [Parameter(Mandatory = $true)]
    [string]$Token,
    [string]$RepoRoot = "",
    [string]$TaskName = "Motion Gate Legacy Baseline",
    [int]$Port = 8791
)

$ErrorActionPreference = "Stop"
if ([string]::IsNullOrWhiteSpace($RepoRoot)) {
    $RepoRoot = Split-Path -Parent $PSScriptRoot
}
$RepoRoot = (Resolve-Path $RepoRoot).Path
$RunDir = Join-Path $RepoRoot ".run"
$TokenFile = Join-Path $RunDir "legacy_token.txt"
$Runner = Join-Path $RepoRoot "scripts\run_windows_legacy_task.ps1"

if ([string]::IsNullOrWhiteSpace($Token)) {
    throw "Token must not be empty."
}
if (-not (Test-Path -LiteralPath $Runner)) {
    throw "Missing required file: $Runner"
}

New-Item -ItemType Directory -Force -Path $RunDir | Out-Null
Set-Content `
    -LiteralPath $TokenFile `
    -Value $Token.Trim() `
    -Encoding ASCII `
    -NoNewline
& icacls.exe $TokenFile /inheritance:r `
    /grant:r "${env:USERNAME}:(R,W)" "SYSTEM:(F)" /Q | Out-Null

Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
Get-CimInstance Win32_Process |
    Where-Object {
        $_.Name -match "^pythonw?\.exe$" -and
        $_.CommandLine -like "*run_windows_legacy_bridge.py*"
    } |
    ForEach-Object {
        Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
    }

$PowerShellArgs = @(
    "-NoProfile"
    "-WindowStyle", "Hidden"
    "-ExecutionPolicy", "Bypass"
    "-File", "`"$Runner`""
    "-RepoRoot", "`"$RepoRoot`""
    "-Port", "$Port"
) -join " "
$Action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument $PowerShellArgs `
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
    -Description "Explicitly unsafe Motion Gate A/B baseline; exhibition use only." `
    -Force | Out-Null
Start-ScheduledTask -TaskName $TaskName
Write-Host "Installed and started scheduled task: $TaskName"
