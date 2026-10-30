# HTTP JSON activity increment

Branch: `codex/056-http-json-activity`, based on main `520e3d5` (PR #53).

## Scope and parallel plan

Add typed business input, status-only output, private closed errors, a pure
content-addressed metadata descriptor/catalog and an internal adapter to existing
live/mock executors. Trusted scope/operation identity never comes from mapped
business input. Explicit mode selection has no fallback. Raw provider response
headers/body are never activity output.

1. Coordinator freezes contracts/body limits and owns structural validation
   tests, documentation, checklist evidence and regression.
2. [P] Descriptor lane owns ref-free conditional schemas, canonical metadata
   bytes/digest identity and fresh exact-pin descriptor-only lookup.
3. [P] Adapter lane owns snapshots, JSON-to-request mapping, mode/error/delivery
   semantics, private result projection and deadline/cancellation tests.
4. [P] Independent acceptance lane owns literal authoring, blocked executable
   publication, real mock composition and controlled SQLite/TLS live composition.
5. Review final boundaries and record confirmed subtasks without closing parents.

Shared contracts preceded parallel edits; no dependency/environment change was
required. See [ADR 0054](../../docs/decisions/0054-http-json-activity.md) and the
[activity guide](../../docs/http-json-activity.md).

## Verification

Independent cross-review found no source blockers; its header-size hardening and
test-evidence feedback were resolved. Final focused validation: **175 passed**
(2.38 seconds): 18 contract/body-bound tests, 44 descriptor/schema tests, 90 adapter
tests and 23 independent acceptance cases. Coverage includes exact metadata-byte
identity, literal authoring, blocked Python artifact publication, real mock
composition and controlled actual SQLite/TLS live execution with three
authorization gates. Both 200 and 503 responses produce status-only output without
retries or raw provider values. A scan-call assertion proves oversized headers
are rejected before regex work.

Ruff lint/format (501 Python files), mypy (118 source files), schema exports,
dependency lock checks and source/wheel builds passed. All three final activity
modules were compared with wheel contents. No dependencies changed. Broad default
regression: **3,129 passed, one expected Linux TCP TIME_WAIT test skipped on
Windows, 75 integration tests deselected** (389.90 seconds). This run collected
before the two additional live TLS cases and final defensive header-size test;
the final 175-case focused run includes all three and the header hardening. No
Temporal/runtime source changed; its opt-in integration suite was not rerun for
this internal metadata/adapter increment. No public egress, live Entra, cloud or
browser acceptance is claimed.

## Remaining scope

The artifact identity hashes actual contract metadata, not executable code. No
ArtifactResolver, Python bundle publication, runtime capability, Temporal or
launcher/API/palette registration is added. Current literal authoring is bounded;
general variable bindings, response mappings/redaction, secrets, durable retries,
other connector descriptors and business-user React screens remain open.
