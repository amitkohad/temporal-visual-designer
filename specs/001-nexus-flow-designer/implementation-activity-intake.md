# Activity bundle intake increment and task reconciliation

Branch: codex/009-activity-bundle-intake, based on main cb4c22f after merged PR #8.

The task audit checked code/tests against task boundaries and closed NF-T003, NF-T006, F01-T002 and F02-T001/T002/T003/T005. Existing NF-T004/F01-T001 remain checked. Partially implemented identity/designer tasks now contain checked backend subtasks and explicitly unchecked remaining acceptance. Hosted cumulative CI evidence was confirmed from successful Python foundation run 21 on 94e4cbd. AGENTS.md records the requirement to update verified checkboxes in the same change going forward.

Implemented F04-T001a/T002 and the intake portion F04-T005a under `nexusflow/modules/activity_catalog/`. Types/digests freeze the source manifest and intake limits; validation.py checks archives/metadata/sources without extraction or execution; repository.py stores a private validated candidate atomically; service.py/authorization.py enforce fresh activity.import rights; export.py emits the portable manifest schema.

Parallel ownership: one agent reconciled task lists, one implemented archive validation/security tests, and another independently tested intake acceptance. The coordinator built contracts, local repository/service, signed-identity integration, exports and documentation. Independent review found declared-vs-host grammar and impossible file/ancestor path collisions; both have regression coverage.

Run `uv run pytest nexusflow/tests/activity_catalog` independently without network, Temporal or Azure. The suite covers path/archive attacks, bounded expansion, manifest/digest/file/dependency validation, immutable retries/conflicts, authorization/scope/revocation, separate-connection races, durable reopen, injected transaction rollback and module non-execution. Export the format with `uv run nexusflow-bundle-schema`.

This does not implement catalog publication/search/lifecycle, retention/export closure, Admin UI or a Python worker. F04-T001b/T003/T004/T005b/T006/T007 and the whole feature remain open. See [ADR 0007](../../docs/decisions/0007-activity-bundle-intake.md) for the accepted source format, digest, trust boundaries and limits.

Verification: 428 isolated tests passed on both Python 3.12 and 3.13, including 63 intake tests. All six real Temporal integration tests passed. Ruff lint/format, strict mypy, locked dependency sync, all five schema exports, distribution build and wheel inspection passed. The current increment is not yet committed/published; hosted CI evidence cited above belongs to the earlier merged form increment.
