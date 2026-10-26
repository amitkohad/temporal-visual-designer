# Immutable Dev debugger session increment

Branch: `codex/038-designer-debug-sessions`, based on main `0eebe55` (merged PR #35). Work resumed after the usage reset on 2026-10-26.

## Implemented boundary

Opt-in signed APIs now create owner-scoped Dev debug sessions from immutable project revisions without publication or activation. Requests explicitly select real mode and pin workflow/input/configuration/breakpoints. Readiness validates the project/catalog/configuration and selected sequential proof workflow. A bounded SQLite intent saves the original request, actor, frozen plan, checksum and trusted placement before dispatch. Exact retries reuse the original snapshot/execution; changed requests or placement conflict. Existing project revisions and conservative catalog holds preserve dependencies.

The actual Temporal adapter reuses the unchanged permanent start journal, acknowledges and persists an exact run ID, and targets that run for snapshot/control. Status exposes only existing payload-free node/state/revision/completed-node metadata. Step-over/continue commands reserve their complete identity before dispatch and save successful receipts. Changed command payloads conflict even while pending; exact completed retries return the original receipt before querying runtime. Stable Temporal update identities recover ambiguous acknowledgments. Every call reauthorizes debug.execute, explicitly requires Dev and enforces original ownership.

Sessions include checksum and stored-binding checks, atomic scope/project operation identity and default 1,000 retained sessions per scoped project. Commands are bounded to 1,000 per session. Started-state/run acknowledgments participate in integrity checks. Pending dispatch recovers through the original create request; status/control conflict until acknowledged. Generic hosts configure both providers explicitly; the local launcher is unchanged. No workflows, start fingerprints or normal execution semantics change; the journal gains an internal read method only. Mypy now includes the new module; no dependencies change.

See [ADR 0036](../../docs/decisions/0036-designer-debug-sessions.md), [walkthrough](../../docs/designer-debug-sessions.md) and [API](../../docs/api.md).

## Parallel ownership and verification

Frozen interfaces preceded domain/repository/unit, API/runtime-adapter/test and independent signed-HTTP/actual-Temporal lanes. The coordinator reviewed invariants, owns documentation/checklists and performs final global verification. Review found and corrected command identity reuse and stored binding/integrity gaps before acceptance.

Focused domain: 14 tests passed. Focused API/runtime adapter: 19 passed. Independent signed HTTP: 6 passed. New actual Temporal case passed and verifies a paused draft, real timer wait/no fabricated step, exact command retry, immutable session after draft edits, new operation identities, worker replacement, completion and history replay. Tests cover owner/revocation/Dev guards, explicit mode, safe failures, no activation, route pinning, pending/lost acknowledgment recovery, quotas, corruption and durable changed-command conflicts.

Full default regression: **1,462 passed, one expected Linux TCP TIME_WAIT test skipped on Windows, 45 integration tests deselected** (204.02 seconds). Ruff lint and formatting pass across 346 Python files; strict mypy passes across 88 source files. Source distribution and wheel build successfully. Independent final review found no blocking findings; `git diff --check` passes. Full actual Temporal runtime suite: **45 passed, 198 deselected** (234.16 seconds), including signed debugger draft execution, durable pause/step/continue, timer guards, worker replacement and history replay.

## Remaining scope

DB-T01a/T02a/T04a/T05a, RT-T03l and NF-T011a are checked only for this independently verified backend boundary; full parents remain open. Variable/input/output inspection/redaction, cancellation/expiry, mock connections, events, parallel frontier semantics, local launcher wiring and the React debugger panel remain outstanding. Actual local Temporal establishes the stated runtime behavior; identity/configuration/capability fixtures do not establish live Entra, production worker, cloud or browser acceptance.
