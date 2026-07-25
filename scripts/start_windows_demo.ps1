param(
    [switch]$EnableUnsafeBaseline,
    [string]$UnsafeToken = ""
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path

if (-not (Get-Process -Name "JoyOfProgramming-Win64-Shipping" -ErrorAction SilentlyContinue)) {
    throw "Start JOY OF PROGRAMMING and open Level Editor first."
}

& (Join-Path $PSScriptRoot "start_biolab.ps1")
Start-Sleep -Seconds 2
& (Join-Path $PSScriptRoot "start_windows_guard.ps1")

$ready = $false
for ($attempt = 0; $attempt -lt 30; $attempt++) {
    try {
        $status = Invoke-RestMethod -Uri "http://127.0.0.1:8788/readyz" -TimeoutSec 2
        if ($status.ready -eq $true) {
            $ready = $true
            break
        }
    } catch {
        Start-Sleep -Milliseconds 800
    }
}
if (-not $ready) {
    throw "Guard started but JOY is not ready. Check the biolab and Guard logs under .run."
}

if ($EnableUnsafeBaseline) {
    if ([string]::IsNullOrWhiteSpace($UnsafeToken)) {
        throw "-UnsafeToken is required when enabling the unsafe A/B baseline."
    }
    & (Join-Path $PSScriptRoot "start_windows_legacy_bridge.ps1") -Token $UnsafeToken
}

Write-Host ""
Write-Host "Windows device side is ready:" -ForegroundColor Green
Write-Host "  Guard + JOY: http://127.0.0.1:8788/readyz"
Write-Host "  Unsafe Bridge: $(if ($EnableUnsafeBaseline) { 'explicitly enabled' } else { 'off by default' })"
Write-Host "X5 discovers Guard readiness but never auto-executes an action."
