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

Tests use local synthetic data and require no Azure credentials, Entra, SMTP, Temporal or network. Keep fakes under nexusflow/tests, use fresh state per test and synchronization instead of sleeps. Frozen Pydantic fields prevent reassignment, but nested dictionaries are not deeply immutable; boundary adapters must copy snapshots. Test security failures and recovery relevant to each change. Do not infer production identity/isolation guarantees from fake-store tests.

Before implementation of a feature adapter, resolve its specific decisions in research.md. Runtime feasibility is next; unresolved scale/retention/identity deployment inputs block their affected production features rather than every local contract test.
