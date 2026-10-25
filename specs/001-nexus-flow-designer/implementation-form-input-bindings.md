# Managed form workflow-input binding increment

Branch: `codex/028-form-input-bindings`, from main after merged PR #25. Decision: [ADR 0026](../../docs/decisions/0026-form-input-bindings.md).

## Bounded scope

Resolve whole-field versioned workflow-input selectors in managed-form initial values before Temporal starts. Validate required object paths, source type compatibility, actual target constraints, payload limits and reserved syntax. Activation checks original schema/declarations without inventing business data. Preserve original workflow digests/declarations and canonical literal/no-form plans; pin only resolved approval values in the existing start plan.

The worker rejects any unresolved marker before task/destination writes. Existing atomic form provisioning, submission gates, field protection and retry semantics remain unchanged. No output values are added to workflow results or ordinary task/status/audit responses.

## Parallel ownership

The coordinator freezes helper/compiler interfaces and owns the worker guard, independent worker tests, sample project, documentation, checkboxes and aggregate verification. One agent owns pure selector validation/resolution and unit tests; another owns compiler/activation integration and tests; a third owns independent actual CLI/HTTP/Temporal acceptance. Paths are disjoint and dependency changes remain coordinator-owned.

## Verification

Verified locally using the existing Python 3.12 environment:

- Full default suite: 1,042 passed, one Linux-only test skipped on Windows and 35 integration tests deselected (185.69 seconds).
- New isolated coverage: 60 pure selector cases, 12 compiler/readiness cases and three worker literal-guard cases. Independent reciprocal verification of the helper/compiler suite: 72 passed.
- New actual CLI/HTTP/Temporal acceptance: one passed (32.04 seconds).
- Full actual Temporal runtime suite: 35 passed, 198 deselected (219.09 seconds), retaining all 34 previous runtime/API/launcher/form regressions.
- Ruff lint and formatting, strict mypy (74 source files), wheel/source distribution builds and `git diff --check` passed.

Independent reciprocal reviews found no blockers in JSON Pointer decoding, required-path/type guarantees, actual target validation, bounded immutable snapshots, worker marker rejection or activation/start separation. A schema-walk refinement distinguishes actual schema keywords from property names and annotation values; compiler-level reference restrictions retain their preexisting conservative behavior.

Actual acceptance publishes and activates the supplied example without business input, starts two executions with different nested input, and verifies distinct pinned form snapshots. Invalid inputs are safely rejected and changed input under one start key conflicts. Initially valid values still require an explicit submission. Draft edits and restart preserve original snapshots; approving one run resumes actual Temporal while the second remains pending. Ordinary status/task/audit responses and logs do not expose input values or hidden fields; authorized form previews expose selected readable values as intended.

No hosted CI, browser, live Entra or complete production-provider acceptance is claimed. Changes remain uncommitted for the next requested commit/PR step.

## Remaining scope

General activity/output bindings, optional/default/array-path selection, expressions, project-form resolution, browser mapping controls, production identity/providers and full interpreter acceptance remain open. Publication-time authoring validation for this approval subset is not claimed; activation and start boundaries are authoritative in this increment.
