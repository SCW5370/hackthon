$ErrorActionPreference = "Stop"

Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;

public static class SafeExecPower {
    [StructLayout(LayoutKind.Sequential)]
    public struct SYSTEM_POWER_STATUS {
        public byte ACLineStatus;
        public byte BatteryFlag;
        public byte BatteryLifePercent;
        public byte SystemStatusFlag;
        public uint BatteryLifeTime;
        public uint BatteryFullLifeTime;
    }

    [DllImport("kernel32.dll")]
    public static extern uint SetThreadExecutionState(uint flags);

    [DllImport("kernel32.dll")]
    public static extern bool GetSystemPowerStatus(
        out SYSTEM_POWER_STATUS status
    );
}
"@

$EsContinuous = [Convert]::ToUInt32("80000000", 16)
$EsSystemRequired = [uint32]0x00000001

try {
    while ($true) {
        $Status = New-Object SafeExecPower+SYSTEM_POWER_STATUS
        $OnAcPower = [SafeExecPower]::GetSystemPowerStatus([ref]$Status) -and
            $Status.ACLineStatus -eq 1
        $Flags = $EsContinuous
        if ($OnAcPower) {
            $Flags = $Flags -bor $EsSystemRequired
        }
        [void][SafeExecPower]::SetThreadExecutionState($Flags)
        Start-Sleep -Seconds 20
    }
} finally {
    [void][SafeExecPower]::SetThreadExecutionState($EsContinuous)
}
