# SafeExec collaboration guide

## Branches

- `main`: only demo-ready, reviewed integration points.
- `dev/<name>/<topic>`: product and application development.
- `ai-safety/<topic>`: policy, temporal checks, and Action Lease.
- `infosec/<topic>`: Gateway, identity, interface security, and optional honeypot.
- `redteam/<topic>`: CrashLab injectors, bypasses, and validation cases.

Open small pull requests and keep changes within one component where possible.
Never commit passwords, private keys, Wi-Fi credentials, captures containing
private data, model binaries, system images, runtime logs, or PID files.

## Required checks

Run before opening a pull request:

```bash
PYTHONPATH=. python3 -m unittest discover -s tests -v
bash -n scripts/*.sh
```

Changes to `contracts/` require review from the affected component owners.
Changes to the policy, Lease, or Gateway boundary require at least one security
member's review.

## Integration boundary

Adapters publish observations. They must not issue an Action Lease or directly
decide actuator authorization. The development mock may simulate reactions for
the dashboard, but it is not the security kernel.
