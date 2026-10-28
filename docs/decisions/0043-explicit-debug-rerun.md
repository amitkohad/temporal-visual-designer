# ADR 0043: Explicit, parent-bound debug reruns

Status: accepted for the sequential Dev debugger.

Provide `POST /projects/{project_id}/debug-sessions/{session_id}/reruns` with the
existing `DebugStart` body and `DebugSession` response (202). The parent must be
an acknowledged session owned by the freshly authorized caller in the same Dev
project and placement. No parent runtime query, cancel or control is performed.
The parent may still be running or already closed.

The request explicitly selects source revision, configuration revision, inputs,
breakpoints and real mode. Its workflow ID must match the parent's workflow.
Inputs are not implicitly copied from the parent's private snapshot. A new
operation ID is required and creates a fresh session/execution and activity
invocation identities through the existing start path. The old execution remains
bound to its original plan; a rerun does not replace or terminate it.

Project-wide operation IDs remain immutable. The child intent and its parent
association are persisted atomically. Exact retries require the same parent and
body and reuse the frozen child, including after dispatch acknowledgement loss.
A normal start operation cannot be adopted as a rerun, nor can a rerun operation
be reused through normal start or another parent. A child may itself be selected
as a parent later with another new operation ID.

An additive SQLite `debug_reruns` table stores scoped operation-to-parent
associations with integrity checks. Existing session/command formats and their
checksums remain unchanged. Hosts migrate when opening the repository; old local
profiles retain read-only compatibility checks before that owned migration.
The selected source and current readiness are checked for a new child, while a retry
uses its saved intent. Parent authorization and binding checks remain mandatory.

Recovery must preserve `debug_sessions`, `debug_commands` and `debug_reruns`
together. Checksums detect changed stored associations; they do not establish
deletion-proof provenance. Deleting lineage rows or partially restoring the
database can lose the distinction between old normal starts and reruns. A
database without the new table is treated as a pre-rerun database and migrated.

This adds backend/API rerun semantics only. React rerun controls, mock mode,
general runtime inspection, expiry and full Designer acceptance remain open.
