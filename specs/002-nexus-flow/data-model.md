# Data model and invariants

All identifiers are opaque; all timestamps are UTC. Organization and environment ownership must be enforced in persistence queries as well as application authorization. Exact schemas/indexes will be designed after storage/toolchain decisions.

| Entity | Key relationships | Invariants |
|---|---|---|
| Organization | Entra directory bindings, environments | Binding explicitly administered; no email-domain discovery |
| Environment | Organization, placement | Exactly named Dev/UAT/Prod initially; immutable scope |
| RoleGrant | Principal/group, scope, permission set | Server resolved; revocable and audited |
| Project | Organization, revisions, releases | Cross-environment identity stable |
| Revision | Project, parent, workflow/form snapshots | Append-only; optimistic concurrency |
| ProjectRelease | Project, revision, content digest | Immutable and self-consistent |
| ActivityVersion | Descriptor, bundle digest, approval | Published version cannot mutate |
| ImportedRelease | Environment, project release, validation | Not active merely because imported |
| BindingRevision | Environment, connection/config references | Immutable; no embedded credentials |
| ActiveProject | Environment/project, release, binding, generation | One atomic pointer; CAS updates |
| DeploymentRecord | Previous/next pointer, actor, evidence | Audited outcomes including failed attempts |
| Execution | Scope, release, runtime, binding, Temporal identity | Pinned before enqueue; unique start key |
| StepProjection | Execution, invocation, attempt, sequence | Rebuildable view, not execution authority |
| HumanTask | Execution/node invocation, form, revision | One terminal transition |
| TaskDecision | Task, actor, idempotency key | Duplicate cannot count twice |
| DeliveryOutbox | Task/start/deployment event | Durable retry; idempotent receiver |
| Connection | Environment, connector, secret reference | No cross-environment fallback |
| Attachment | Scope, task/evidence, digest, scan state | Quarantined content unavailable to ordinary downloads |
| AuditEvent | Actor, scope, action, outcome, correlation | Append-only; defined access/retention |

## State transitions

- Draft save → new revision; restore → new revision derived from a historical one.
- Release preparation → validated immutable release; invalid release never becomes published.
- Import staged → validated or rejected; interrupted staging cannot expose a partial usable release.
- Deployment pending → validating → activating → active/failed; crash reconciliation has an explicit recovery path and never exposes a mixed project generation.
- Task pending/claimed/information_requested → completed/rejected/cancelled/expired, according to configured rule; terminal events cancel outstanding timers.
- Execution created → running/waiting/paused → completed/failed/cancelled; projection delays do not redefine Temporal state.

## Retention and deletion

Do not delete an artifact or runtime referenced by a live execution. Retention applies independently to business payloads, task attachments, audit, debug snapshots and deployment evidence. Legal retention and residency requirements are unresolved; deletion and export must remain tenant scoped. Backups and restore must preserve referential integrity and reconcile outboxes with Temporal without repeating accepted decisions.
