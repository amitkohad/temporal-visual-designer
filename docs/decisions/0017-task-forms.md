# ADR 0017: Pinned task forms with atomic revision checks

Status: accepted for bounded task-form persistence and HTTP integration.

Human tasks reuse the existing server-side form validator through a task-owned gateway. An internal, trusted `task.create` operation binds one immutable form definition and initial values to an existing task before voting. The original workflow task request remains unchanged. Exact binding retries compare the original definition and initial values, while different bindings conflict. Fetching this definition from the actual pinned project release remains a future runtime/provider integration.

Form values live in a separate scoped SQLite table in the same database as task state. They do not appear in task views, inbox pages, audit events or completion outbox records. Each preview requires fresh task-read authorization, current assignment eligibility and an explicitly configured trusted field policy. The policy returns readable/editable fields for the verified actor; it cannot be supplied in a browser request. The existing form engine filters hidden and write-only values and validates supported schemas and conditional rules.

Submissions carry the shared task revision and a partial values object. The form engine validates the merged values. Commit rechecks authority, assignment eligibility, policy and the task revision inside the SQLite transaction, then atomically persists values, validation status, task revision and a payload-free audit event. There is no form command receipt: a lost response is resolved by reloading the current revision. Task forms cannot be edited after the first decision, including an all-approver task that is still pending.

Binding starts the form unvalidated. An approval requires a successful server-validated submission when a form is bound; rejection remains possible without completing the form. The decision check shares the task transaction, preventing approval against an unvalidated or concurrently edited form. Tasks without forms keep their existing approval behavior.

The HTTP factory exposes authorized preview and submission only, using the existing verified Entra object-ID adapter and safe error envelopes. Hosts explicitly configure a field policy and task database; absent policies fail closed. The local launcher does not enable task providers automatically. There is no browser endpoint to bind or replace a form, grant field permissions or read unfiltered values.

The gateway owns local task persistence. Existing rows remain readable; downgrade to binaries that omit form approval guards is unsupported. Form binding/submission audit events contain only event metadata. Production encryption/retention, attachment handling, live identity/field-policy providers, React rendering, workflow input/output mapping and actual Temporal human-task completion remain separate work.
