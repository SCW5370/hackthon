# Team ownership and integration map

| Area | Primary owner | Output |
| --- | --- | --- |
| RDK X5 perception adapter | Developer | `zone.clear`, `camera.healthy`, evidence |
| Dashboard and digital executor | Developer | Live state, incident timeline, demo actuator |
| Fact validation and policy | AI safety | Decision and reason codes |
| Action Lease | AI safety | Short-lived signed authorization |
| Gateway and identity | Information security | Enforced actuator boundary and audit |
| Honeypot extension | Information security | Optional deception events and evidence |
| AttackLab and bypass attempts | Security testing | Reproducible attacks, evidence-backed verdicts and regression cases |

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

Telemetry and decisions flow to the Console. AttackLab attacks the boundaries;
it does not bypass the Gateway in normal operation.

## Current state

- Real UVC camera capture, camera health, and BPU body detection are working.
- The Console receives live frames and Facts.
- Policy, Action Lease and Guard are connected to the live JOY executor.
- AttackLab develops only in `attack_lab/` against the shared schemas in
  `contracts/`; it does not modify the execution path.
