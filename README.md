# Nexus Flow Designer

A visual business-process designer powered by Temporal. The backend and workflow workers use Python; the browser UI will use React.

## Foundation development

```sh
uv sync --locked
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest
uv run nexusflow-export-schemas
uv build
```

Python 3.12 or 3.13 and uv are required. No Azure, Entra, database or Temporal service is needed for these tests. The foundation provides strict contract validation, portable schemas and isolated test helpers. A limited Python Temporal proof now executes linear workflows with durable approvals, timers and debug pauses. The full runtime and React UI remain in development.

See [development commands](docs/development.md), the [Python/React architecture decision](docs/decisions/0001-foundation.md), and [implementation specifications](specs/001-nexus-flow-designer/README.md).

Run the real proof with `uv run pytest -m integration nexusflow/tests/runtime`. See [runtime proof](specs/001-nexus-flow-designer/implementation-runtime-proof.md).

The identity/environment backend core now validates signed Entra tokens through injected key/directory providers, applies scoped persisted grants and maintains logical Dev/UAT/Prod routing. See [implementation scope](specs/001-nexus-flow-designer/implementation-identity-environments.md); live Microsoft adapters and React sign-in remain planned.

The project versioning backend now supports scoped revision history, restore and immutable whole-project releases. See [implementation scope](specs/001-nexus-flow-designer/implementation-project-versioning.md); React project screens and production catalog/deployment adapters remain planned.

The offline authoring validator adds structured branch/join checks, reusable-workflow recursion checks and descriptor-backed input mapping validation. See [implementation scope](specs/001-nexus-flow-designer/implementation-authoring-validation.md). Variable bindings are validated for authoring and rejected by the proof runtime until execution support is implemented.
