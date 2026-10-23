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

Python 3.12 or 3.13 and uv are required. No Azure, Entra, database or Temporal service is needed for these tests. The foundation provides strict contract validation, portable schemas and isolated test helpers. It does not yet run workflows or provide a web UI.

See [development commands](docs/development.md), the [Python/React architecture decision](docs/decisions/0001-foundation.md), and [implementation specifications](specs/001-nexus-flow-designer/README.md).
