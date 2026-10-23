# Development and verification

Use Python 3.12 or 3.13 and uv. The browser UI will use React; Node is not required for this Python foundation.

```sh
uv sync --locked
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest
uv run pytest nexusflow/tests/test_contracts.py
uv run nexusflow-export-schemas
uv build
```

The lockfile pins resolved versions. Schema export writes deterministic, versioned JSON schemas under ignored artifacts/schemas/0.1. Python/Pydantic performs additional graph checks that portable JSON Schema cannot express; React schema validation alone cannot establish deployability.

`uv run nexusflow-check workflow <file.json>` returns JSON issues and exits 0 for valid contracts, 1 for invalid content, and 2 for file/usage errors. It never runs a workflow. Error reports contain field locations and codes, not input payloads. Workflow schemas, expression handling and graph semantics are a foundation subset; complete graph/dataflow/parallel-join checks arrive in the Designer/runtime features.

Default tests use local synthetic data and require no Azure credentials, Entra, SMTP, Temporal process or network. Keep fakes under nexusflow/tests, use fresh state per test and synchronization instead of sleeps. Frozen Pydantic fields prevent reassignment, but nested dictionaries are not deeply immutable; boundary adapters must copy snapshots. Test security failures and recovery relevant to each change. Do not infer production identity/isolation guarantees from fake-store tests.

Before implementation of a feature adapter, resolve its specific decisions in research.md. The runtime feasibility proof is implemented; unresolved scale/retention/identity deployment inputs block their affected production features rather than every local contract test.


## Real Temporal integration

Default uv run pytest excludes the integration marker. Run uv run pytest -m integration nexusflow/tests/runtime for sandbox/restart/replay tests. They start a disposable loopback server using NEXUSFLOW_TEMPORAL_CLI or temporal on PATH. Without either, the SDK downloads a dev server and needs network access. Local CLI 1.9.1 is verified and pinned in CI. Startup errors fail the suite. No cloud credentials or deployed cluster are used. See [ADR 0002](decisions/0002-runtime-proof.md).

Identity acceptance: `uv run pytest nexusflow/tests/identity`. Synthetic RSA/JWKS/directory fixtures and temporary SQLite files require no Microsoft credentials or network. See ADR 0003 for trusted bootstrap and provider boundaries.

Project versioning acceptance: `uv run pytest nexusflow/tests/project_versioning`. Export the feature schema with `uv run nexusflow-project-schema`. See [ADR 0004](decisions/0004-project-versioning.md) for local persistence, digest semantics and integration boundaries.
