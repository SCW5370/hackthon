# SafeExec V0 integration scaffold

This directory is the integration workspace for the 48-hour SafeExec demo.
It intentionally avoids implementing security policy, Action Lease signing,
or deception behavior before the responsible team members freeze those
contracts.

SafeExec is a software runtime demonstrated with edge hardware. The RDK X5 and
camera provide observable facts; the product boundary is the policy, Action
Lease, enforcement Gateway, audit trail, attack validation, and operator
Console that sit between an Agent and an actuator.

## What is included

- `contracts/v0.md`: provisional envelopes for Facts and Events.
- `console/`: dependency-free dashboard that works with mock events.
- `dev/mock_server.py`: non-security mock backend for parallel UI work.
- `adapters/`: reusable vision geometry and camera-health primitives.
- `adapters/rdk_fact_bridge.py`: ROS2 bridge from X5 perception to Facts.
- `config/demo.json`: normalized danger-zone and camera thresholds.
- `scripts/`: local development start/stop commands.
- `tests/`: unit tests for the adapter primitives.
- `docs/team-ownership.md`: team boundaries and integration direction.
- `CONTRIBUTING.md`: branch, review, and secret-handling rules.

## Start locally

```bash
cd safeexec
./scripts/start_dev.sh
```

Open <http://127.0.0.1:8787>.

Stop it with:

```bash
./scripts/stop_dev.sh
```

## Connect the RDK X5

The Mac-side mock endpoint listens on the USB network address
`192.168.128.20`. Copy this directory to `/opt/safeexec` on the board, then:

```bash
cd /opt/safeexec
./scripts/rdk_perception.sh start
./scripts/rdk_perception.sh status
```

The tested RDK pipeline for the attached UVC camera is:

`/dev/video0 MJPEG → hobot_codec NV12 → BPU mono2d body model → Fact bridge`.

The body model must use `model_type=0` for the bundled
`multitask_body_head_face_hand_kps_960x544.hbm`; other values crash this
specific runtime/model combination.

## Important boundary

The mock server is only a UI and integration aid. It is not SafeExec's
security kernel and must never be used to claim that policy enforcement,
Lease validation, or actuator isolation is complete.
