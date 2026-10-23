# Foundation implementation record

Date: 2026-10-23. Branch: codex/003-foundation-contracts. Scope: first implementation increment; Python backend/contracts and React browser UI selected by the user.

## Delivered

- Importable Python package with uv lockfile, strict Pydantic contracts and portable JSON Schema 2020-12 export.
- Six versioned envelopes: request-context, error, activity-descriptor, workflow, project and step-event.
- Located, input-free errors; graph checks for duplicates, node/port references, bounded-loop declaration, reachability, terminal paths and unsupported cycles; project dependency declarations.
- Deterministic onboarding/purchasing skeleton fixtures, scoped fake-provider protocol, negative authorization/scope cases and copy isolation.
- Contract validation/export CLI, module/application ownership boundaries and AST tests preventing prohibited imports.
- Lint, format, static typing, isolated tests and source/wheel build commands; Windows/Linux Python 3.12/3.13 CI matrix.

## Verification evidence

50 tests passed on Windows with Python 3.13.3 and Python 3.12.2. Ruff lint and format checks passed; mypy passed all six production source files. Schema export and source/wheel build passed. Wheel contents were inspected: production contracts included and test helpers excluded. GitHub-hosted matrix execution is pending push; no CI success is claimed. No live Entra, Azure, database, Temporal or React integration was exercised.

## Task accounting and boundaries

NF-T001/NF-T002 have a documented foundation subset in ADR 0001; broader identity topology, expression execution, runtime SDK, activity sandbox, retention and sizing decisions remain open at their affected feature gates. NF-T003's local package/harness/build boundary is implemented, while full cross-feature API/code generation and UI design-system scaffolding remain later work. Do not close the broader setup gates or claim all C01–C10 are executable.

- [x] NF-T003a Python package, reproducible dependencies and development checks.
- [x] NF-T003b Foundation contract subset, JSON export and located validation errors.
- [x] NF-T003c Independent fixtures, scoped fake conformance and import-boundary tests.
- [x] NF-T003d Build/package checks and CI definition.
- [ ] GitHub-hosted CI evidence after push.
- [ ] Runtime feasibility proof (NF-T004): compatible Python Temporal SDK, replay/restart, durable human response and debug gate, concurrent pinned releases.

This increment does not implement production authorization, complete compiler validation, forms/packages/deployments, API server, workflow execution, database persistence or React UI. JSON schemas are structural; validate_contract adds the documented semantic subset. The approved Figma canvas remains the UI reference for later work.
