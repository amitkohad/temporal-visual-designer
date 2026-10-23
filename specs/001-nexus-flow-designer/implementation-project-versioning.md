# Project versioning backend increment

Branch: `codex/006-project-versioning`, based on main `b37cc59`.

Implemented Feature 02 backend contracts, scoped durable revisions, optimistic saves, history/compare/restore, whole-project immutable releases, pinned transitive dependency manifests, deterministic digests, transactional publication retries and retained-release deletion protection. Independent agents owned repository, validator and acceptance test paths; the coordinator integrated the service and identity adapter.

Actual Python paths replace the earlier conceptual numbered paths:

- `nexusflow/modules/project_versioning/types.py`: feature contracts and replaceable ports.
- `repository.py`: local SQLite provider, transaction boundaries and publication tombstones.
- `service.py`, `canonical.py`, `authorization.py`: authoring/publication operations and fresh identity authorization.
- `validation.py`, `export.py`: baseline offline checks and portable project schema.
- `nexusflow/tests/project_versioning/`: independently runnable provider, service, validation, identity-boundary and failure tests.

Run `uv run pytest nexusflow/tests/project_versioning` and `uv run nexusflow-project-schema`. Schema output is ignored under `artifacts/schemas/0.1/`. See [ADR 0004](../../docs/decisions/0004-project-versioning.md) for digest semantics, trust boundaries and remaining work.

Acceptance coverage includes two workflows and a form in one release, stale saves, restore without rewriting history, response-loss publication retry, missing transitive dependencies with no release, retained deletion denial, cross-organization denial and grant revocation. Tests include separate SQLite connections racing on save/publication and injected failure after release insertion before commit.

This completes the local backend portion of F02-T001/T002/T003/T005. F02-T004 (React UI) and full F02-T006 integration remain open. Feature 03 compiler, Feature 04 live catalog and Feature 09 package/deployment adapters are not substituted with production claims. The whole feature remains incomplete.

Verification: 242 isolated tests passed on Python 3.12 and 3.13; 6 real Temporal integration tests passed. Ruff lint/format, strict mypy, locked dependency sync, both schema exports and package build passed. Wheel inspection confirmed feature modules are included and test helpers are excluded.
