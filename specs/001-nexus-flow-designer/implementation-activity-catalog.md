# Activity catalog backend increment

Branch: `codex/010-activity-catalog`, based on prerequisite intake commit `df962ed` on `codex/009-activity-bundle-intake`. The intake branch was committed/published but not merged into main (`cb4c22f`) at increment start; main is unchanged. This branch includes the prerequisite and stacks catalog work on top.

Implemented F04-T003 and completed isolated catalog/import acceptance F04-T005. Contracts live in `activity_catalog/catalog_types.py`; `catalog.py` supplies fresh authorization and capability-gated publication; `catalog_repository.py` provides local transactional immutable versions, lifecycle generations, search, audit events, usage holds and logical purge tombstones. `apps/catalog_adapters.py` composes real catalog data into authoring validation and project publication.

Parallel ownership separated storage/provider tests from independent service acceptance; the coordinator owned service, contracts, identity policy, composition and documentation. Review found unbounded archive loading during palette search. Metadata projections and SQL-side filters/pagination now avoid BLOB reads, verified with a SQLite authorizer denying archive-column access.

Run `uv run pytest nexusflow/tests/activity_catalog` without network, Temporal or Entra services. Coverage includes private intake -> publish -> palette, exact version/digest immutability, lost response retries, capability failure, stored archive tampering, deprecation/withdrawal, stale lifecycle changes, retained deletion protection, cross-connection races, transactional audit failure, durable tombstones, scope isolation, source non-execution and self-contained vendored export/import.

Signed synthetic Entra-token integration proves business authors can read the palette/export but cannot publish or withdraw code. Revoked grants fail subsequent operations. A composed project test uses actual catalog/intake/project SQLite providers and AuthoringValidator; deprecation preserves pinned releases, while withdrawal blocks new project publication without altering earlier releases.

F04-T001 lifecycle/catalog contract subset and local F04-T006 composition are checked separately. Admin UI, worker dispatch/sandbox contracts, isolated execution and automatic release/run retention wiring remain unchecked. See [ADR 0008](../../docs/decisions/0008-activity-catalog.md) for lifecycle/export semantics, trust boundaries and production limitations. `nexusflow-bundle-schema` now also exports the catalog-entry schema.

Verification: 467 isolated tests passed on both Python 3.12 and 3.13; all six real Temporal integration tests passed. The catalog/intake suite has 98 tests. Ruff lint/format, strict mypy, locked dependency sync, all schema export commands, distribution build and wheel inspection passed. Current catalog changes are uncommitted; no hosted CI result is claimed for this branch.

PR #9 review found and fixed a configuration mismatch: publication now accepts an explicit IntakeLimits policy, so source files imported under supported custom limits can publish under the same policy. A regression verifies default tighter rejection leaves no publication and matching custom limits succeed. Two independent review lanes found no other blocking issues within the documented backend scope.
