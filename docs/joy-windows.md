# BioLab_Guardian on Windows

This directory is the JOY actuator side only. It does not implement an Agent,
JobManifest policy, Action Lease, signatures, replay protection, an HTTP
bridge, or prompt-injection handling.

## Prerequisites

1. Start Steam and **JOY OF PROGRAMMING**.
2. Open **Level Editor**. It is safe if an existing Workshop level is visible;
   the generator clears the editor scene.
3. Run commands from the repository root in PowerShell.

The checked environment used JOY's bundled Python 3.10.9 and `pyjop 1.0.3`.
The read-only probe verified `SimEnv.connect("127.0.0.1", 18189)`,
`LevelEditor`, `RobotArm`, `DataExchange`, `RangeFinder`, the required
spawnable types, and external-client support.

## Optional external Python environment

When the Windows `py` launcher is installed:

```powershell
py -m venv .venv
.venv\Scripts\python -m pip install -U pip
.venv\Scripts\python -m pip install git+https://github.com/maschere/pyjop.git
```

The helper scripts fall back to JOY's bundled Python when `.venv` does not
exist, so a separate Python install is not required for the demo.

## Read-only probe

With an external Python environment:

```powershell
.venv\Scripts\python joy\probe_env.py
```

With JOY's bundled interpreter, locate the executable under the JOY install
and run the same file. The probe performs no spawn, movement, save, or editor
mutation.

## Generate and start the level

```powershell
powershell -ExecutionPolicy Bypass -File scripts\start_biolab.ps1
```

This starts `joy\BioLab_Guardian.py` in the background. It selects
`MinimalisticIndoor`, creates `sample-A` through `sample-F` in a 2×3 waiting
grid, assigns unique colors, RFID tags, and destination slots, registers the
six RPCs, and starts the non-blocking arm state machine. Completed samples do
not overlap, and no manual scene placement is required.

Stop only the background level runtime with:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\stop_biolab.ps1
```

## Reproduce the scenarios

The requested Python module commands are:

```powershell
py -m joy.dev_control normal
py -m joy.dev_control legacy-attack --confirm-unsafe-demo
```

On a machine without the `py` launcher, use the equivalent one-command
wrappers:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\run_biolab_cli.ps1 normal
powershell -ExecutionPolicy Bypass -File scripts\run_biolab_cli.ps1 legacy-attack -ConfirmUnsafeDemo
```

The Legacy command is intentionally simulation-only, opens no network port,
and refuses to run without explicit confirmation. It verifies both:

- `sample-A` is at `waste-bin`;
- `unsafe_outcome` is `true`.

## RPC and SafeExec handoff

The level exposes these methods through `safeexec_exchange`:

- `health()`
- `inventory()`
- `transfer(command)`
- `pause()`
- `resume()`
- `reset()`

The command object handed to the future security layer is:

```json
{
  "command_id": "cmd-001",
  "action": "TRANSFER",
  "sample_id": "sample-A",
  "source": "cold-storage",
  "destination": "analyzer-01"
}
```

`transfer()` validates only structure, known entity names, and current source
location. It intentionally does not decide whether a business route is safe.
After the security member validates a command, it should call
`JoyDriver.transfer()`. `reset()` restores all six samples and is accepted only
while the line is not running.

## Tests

Offline tests:

```powershell
py -m unittest discover -s tests -v
```

Live JOY smoke test:

```powershell
py -m joy.smoke_test
```

The smoke test checks the 18189 connection, unique A–F entities and RFID
values, normal transfer, Legacy transfer, Reset, Pause, and Resume.
