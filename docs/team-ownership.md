# Team ownership and integration map

| Area | Primary owner | Output |
| --- | --- | --- |
| RDK X5 perception adapter | Developer | `zone.clear`, `camera.healthy`, evidence |
| Dashboard and digital executor | Developer | Live state, incident timeline, demo actuator |
| Fact validation and policy | AI safety | Decision and reason codes |
| Action Lease | AI safety | Short-lived signed authorization |
| Gateway and identity | Information security | Enforced actuator boundary and audit |
| Honeypot extension | Information security | Optional deception events and evidence |
| CrashLab and bypass attempts | Red team | Reproducible attacks and regression cases |

## Stable direction

```text
RDK sensors
  -> perception adapters
  -> Fact Bus
  -> policy
  -> Action Lease
  -> Gateway
  -> digital or physical executor
```

Telemetry and decisions flow to the Console. CrashLab attacks the boundaries;
it does not bypass the Gateway in normal operation.

## Current state

- Real UVC camera capture, camera health, and BPU body detection are working.
- The Console receives live frames and Facts.
- Policy, Action Lease, and Gateway are represented by development mocks.
- The security members own replacing those mocks without changing the device
  adapter's observation-only responsibility.
