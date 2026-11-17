# Readiness critical-path slice (NF-T017a)

Branch: `feature/f10-ops-portability-readiness-206b`. Depends on the F10 fake-port in
[implementation-operations-portability.md](implementation-operations-portability.md).

## Bounded implementation scope

Critical-path suites under `nexusflow/tests/readiness/`:

1. Tenant/environment isolation smoke (`test_tenant_isolation.py`)
2. Drain/restart + pinned recovery smoke (`test_drain_recovery.py`)
3. Measured-results note with **unresolved** SC-06 sizing/recovery targets
   (`measured-results.md`) — no invented SLOs

## Verification

```sh
uv sync --locked
uv run pytest -q nexusflow/tests/readiness/
```

## Explicitly not claimed

- Full NF-T017 accessibility certification
- Capacity load campaign or agreed numeric throughput/availability/RTO/RPO targets
- Disposable Temporal restart integration beyond the fake-port pin/restore model
- Live Azure providers

Parent **NF-T017** remains open. Nested **NF-T017a** is checked for this slice only.
