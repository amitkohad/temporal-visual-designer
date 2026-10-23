# Human tasks and approval portal

Status: specification only. Reference [shared contracts](../../contracts.md).

## Scope and requirements

Provide configurable task forms and a business-user inbox for onboarding and purchase approvals. Task form rendering consumes the versioned form contract; form authoring belongs to the Designer feature. External notifications use connector ports, not embedded provider credentials. Offline production change requests are unrelated to workflow approval tasks.

- **HT-FR-01:** Assign tasks to Entra user/group IDs within the organization; authorize server-side reads, claims and actions. Store an eligible-approver snapshot at creation and auditable reassignment revisions. Treat group membership changes as an explicit reassignment/refresh operation; never silently change completed decisions.
- **HT-FR-02:** Business users configure any-one, all-approver, sequential-stage and conditional routing rules through structured settings. Evaluate rules against pinned definition/data. Validate empty approver sets, contradictory stages and unreachable routes before publishing; reject a runtime resolution with no eligible approvers into a visible failure/escalation path.
- **HT-FR-03:** Support approve, reject, request information, comments, claim/release, delegation and reassignment with audit. Only one current revision may accept a conflicting terminal action. Deduplicate approvers resolved through multiple groups. All-approver completion requires every distinct required approver; rejected decisions follow the configured rejection path.
- **HT-FR-04:** Render pinned configurable forms with typed validation, conditional visibility, field permissions and attachments. Validate again on the server. Restrict file sizes/types by policy, quarantine pending scans, authorize downloads and use expiring access; a failed scan blocks access. Retention follows environment policy.
- **HT-FR-05:** Provide deadlines, durable reminders, escalation and external notifications with configurable recipients. Persist timer occurrences/idempotency keys; do not send reminders for completed tasks. Provider failures are observable/retryable and cannot reverse an approval.
- **HT-FR-06:** Persist decisions and an outbound completion record atomically; retry delivery until runtime acknowledges. Duplicate completion delivery advances the workflow once. Recover pending delivery and timers after service restart. No distributed transaction with Temporal is assumed.
- **HT-FR-07:** Inbox supports assigned-to-me/group, status, due date and workflow filters with pagination. Track requester-visible progress without exposing protected task data. Audit creation, membership resolution, claims, delegation, decisions and delivery outcomes.

## Data/contracts

Own Task (tenant/environment/execution/step IDs, pinned form/rule version, status/revision, due date), AssignmentSnapshot, ApprovalStage/Decision, Delegation, AttachmentReference, NotificationOccurrence and CompletionOutbox. Use immutable actor IDs plus display labels. Freeze create/query/action, assignment resolver, attachment storage/scanner, notification and runtime completion ports under `nexusflow/modules/06-human-tasks/contracts/`. Actions carry command ID and expected revision; errors identify unauthorized, invalid form, stale revision and already-completed task. Task creation is idempotent by execution/step invocation, not display name.

## Acceptance scenarios

- **HT-A1 positive:** Given a purchase request with any-one/all/sequential stages, when eligible Entra users decide, then each configured rule selects the correct next route and one completion is delivered.
- **HT-A2 negative:** Given a foreign organization user, stale task revision or hidden field modification, when queried/actioned, then access/action fails and no state or notification changes.
- **HT-A3 concurrency:** Given two approvers racing a terminal decision and duplicate group memberships, when actions arrive, then one valid transition commits and all-approver counts distinct users only.
- **HT-A4 recovery:** Given a decision committed before runtime delivery and due reminders, when the service restarts, then delivery resumes without lost decisions and completed tasks receive no new reminder occurrences.
- **HT-A5 reassignment:** Given delegation/reassignment before completion, when the new assignee responds, then permissions and audit reflect the revision and former assignees cannot act using stale revisions.
- **HT-A6 attachments:** Given an oversized, malicious or unauthorized attachment, when upload/download is attempted, then policy/quarantine blocks access; a clean permitted file remains attached to the correct task.
- **HT-A7 escalation:** Given a deadline and failing notification provider, when virtual time advances, then escalation occurs once per policy and notification retries do not repeat workflow advancement.

## Success criteria

HT-A1–A7 pass independently using fake Entra membership, runtime, notification, scanner and storage adapters plus virtual time. Every terminal decision has one durable outbox record and auditable actor/revision. UI tests cover keyboard task completion, validation and pagination against fake APIs. Real runtime integration separately proves decision delivery recovery. Quorum voting and automatic manager discovery are deferred unless explicitly added.
