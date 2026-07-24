# BioLab Agent

The Agent converts an operator task and an untrusted laboratory record into the
strict `safeexec.action.v1` contract. It never emits JOY coordinates, leases,
or robot joint commands.

Deterministic development run:

```bash
PYTHONPATH=. python3 -m lab_agent run \
  --mode replay \
  --scenario prompt-injection \
  --transport dry-run
```

OpenAI-compatible live mode uses `LLM_BASE_URL`, `LLM_API_KEY`, and
`LLM_MODEL`. SafeExec mode uses `SAFEEXEC_RUNTIME_URL`. Legacy mode is
simulation-only and additionally requires `LAB_LEGACY_URL`,
`LAB_LEGACY_TOKEN`, and `--confirm-unsafe-demo`.

The printed `plan_fingerprint` hashes only the stable business action. Request
IDs and timestamps remain fresh for every run while the demonstration can
prove that Legacy and SafeExec received the same semantic intent.
