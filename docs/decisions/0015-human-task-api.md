# ADR 0015: Scoped human-task inbox and HTTP decisions

Status: accepted for the bounded approval API; browser, forms and runtime delivery remain separate gates.

The existing authenticated API exposes assigned-task listing, task detail, audit and approve/reject commands. Task creation and completion delivery remain trusted internal operations and have no HTTP routes. The host explicitly supplies `ApiBackend.tasks_database`; an omitted provider returns a sanitized unavailable response. Each request opens and closes its own task service with request-bound authorization. The existing local launcher does not configure this optional provider or seed tasks.

Task authorization freshly verifies the token and policy through `IdentityService.authorize_user_object_id`. It returns the verified Entra user object ID, which matches assignment snapshots; pairwise token subjects cannot substitute for directory IDs. Workload identities cannot use this user boundary. Existing project author attribution and role grants retain their established contracts. A scoped `task_participant` grant and eligible snapshot membership are both required for task access; groups can supply grants, while resolved eligible users remain the immutable task snapshot.

Inbox queries apply organization, environment and eligible-user filtering before pagination. Optional status and run filters use bound SQL parameters. SQLite JSON1 reads the existing persisted task snapshot, avoiding a second membership source that could drift during upgrades. The supported Python SQLite runtime must provide JSON1. This local query implementation does not establish production capacity or indexed membership-search performance.

Pages contain at most 100 tasks, ordered by creation time and task ID. Offset is bounded to 0–10000; `next_offset` is emitted only within that range. Offset pagination is a current-state view, not a frozen cursor: decisions and new tasks between requests can change page membership. Due-date and workflow filters require future task metadata; run filtering is provided now. The result represents assignment through either explicit users or resolved groups, rather than current group membership.

HTTP decisions reuse the core's atomic receipts, expected revisions, membership checks and outbox. Exact retries return the recorded response, which may precede current state; read the task again for current progress. Domain errors use the existing safe envelopes and cache/body boundaries. No form responses, attachment uploads, arbitrary actors, assignment edits or runtime destinations are accepted by these endpoints.

Live Entra/Graph adapters, React inbox/accessibility, form submission, deadlines, delegation, task creation from workflows and actual Temporal completion delivery remain open. Signed synthetic-token HTTP tests establish local provider composition only.
