# Explicitly opted-in array input inspection increment

Branch: `codex/042-debug-array-inspection`, based on main `04994a9` (merged PR #39).

## Implemented boundary

The existing frozen-input inspector now supports homogeneous business collections without changing routes, responses, persistence, worker commands or start fingerprints. Each array layer requires both the existing public flag and the new x-nexusflow-inspect-array:true. Review identified that older schemas already carrying public array annotations were previously hidden; requiring a new marker preserves their privacy. Authors must opt in through a new immutable schema revision. Existing original-source digest and owner/Dev checks remain unchanged.

A single typed items schema controls projection. Primitive items require independent public classification; object items disclose only declared public leaves; nested arrays need both markers at each layer. Protected parent/item flags and unsupported/ref/composed/tuple/containment declarations take precedence. Unknown dynamic keys stay hidden. Individual undisclosable runtime values use null plus an indexed redaction path, preserving later positions. Unsupported/protected homogeneous item declarations hide the whole array.

The array cap is 100 leading items per array. Omitted tails mark truncation/path. Existing scalar 1,024 JSON byte, depth eight, 1,000 visited-node, 100 redaction-path and 16 KiB whole-response limits remain enforced; traversal exhaustion stops tails and aggregate overflow keeps the empty-root fallback. No secret/connection resolution, runtime output retrieval, gate release or activation occurs.

See [ADR 0040](../../docs/decisions/0040-debug-array-inspection.md), [guide](../../docs/debug-array-inspection.md), [API](../../docs/api.md) and [sample](../../examples/local-debug-array-project.json). No dependency or persisted-format changes.

## Parallel ownership and verification

Frozen policy preceded pure redaction/unit, independent signed-HTTP and actual Temporal acceptance lanes. The coordinator owns documentation/checklists, example verification and final regression. Cross-lane review found and corrected the legacy-array visibility issue before completion; baseline privacy tests remain unchanged.

Pure tests: 85 passed (42 existing, 43 new). Signed API: 14 passed (8.53 seconds). New actual Temporal: 1 passed (7.90 seconds), verifying public line-item/object/nested projections, protected parent/item/unknown hiding, a legacy-public-only negative case, indexed oversized-scalar placeholders, unchanged paused gates, original schema despite edits, worker-down/completed inspection and history replay. API tests also cover fresh owner/revocation, dual opt-ins, unsupported tuple/contains schemas, 100-item tails and the whole-response cap. The sample passes baseline project validation and proof compilation without execution. Independent core reviews found no blocking issues.

Full default regression: **1,663 passed, one expected Linux TCP TIME_WAIT test skipped on Windows, 49 integration tests deselected** (270.21 seconds). Full actual Temporal runtime suite: **49 passed, 198 deselected** (321.33 seconds), with recoverable ignored log/exit evidence. Ruff lint and formatting pass across 379 Python files; strict mypy passes across 93 source files. Source distribution and wheel build successfully; `git diff --check` passes. Independent reviews found no blocking issues.

## Remaining scope

DB-T01f/T02f/T04f/T05e and NF-T011f are checked for this independently verified input projection subset; full debugger parents remain open. Runtime variables/step outputs, broader composed-schema policies, cancellation/expiry, events, mocks, parallel stepping and React controls remain outstanding. Actual local Temporal and synthetic identity/readiness establish only the stated backend behavior; no live Entra, browser or production-provider acceptance is claimed.
