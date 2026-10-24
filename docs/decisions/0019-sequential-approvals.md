# ADR 0019: Fixed sequential approval stages

Status: accepted for the bounded local human-task service.

Human tasks may declare a fixed sequence of 1 to 20 uniquely identified approval stages. Each stage uses the existing any-one or all-approvers rule and explicit user/group assignments. Sequential requests cannot also declare a root assignment. All stages resolve through the scoped directory port at creation, with no empty stage and no more than 1,000 resolved assignment entries across stages. Membership is snapshotted; later directory changes do not silently alter future stages.

Only the current stage's assigned actors can read the task, appear in its inbox, inspect its form or submit a new decision. A future or previous stage assignment grants no current access. The original creation request remains immutable; resolved stage assignments and the current stage index are persisted separately. Decision records retain their stage ID. An actor assigned to multiple stages may vote once in each stage.

Approving a stage advances to the next stage in the same SQLite transaction as the decision, command receipt and audit events. Intermediate advancement keeps the task pending and creates no completion outbox entry. Final-stage approval creates the existing durable completion entry; rejection at any stage immediately rejects the whole task and creates its completion entry. Shared revision checks serialize competing decisions, form updates, reassignment and notification scheduling. Stage advancement emits an explicit audit event identifying the previous and next stage.

Every decision attempt rechecks current authorization. A previous-stage actor may retrieve the immutable receipt for their own exact accepted command, including its original revision and decision. This exception grants no current task read, new vote or access to another actor's receipt. Reused commands with changed content conflict. Receipts are historical responses and do not represent the current task revision or assignment.

Administrative reassignment remains available only before the first vote anywhere in the task. For a sequential task it replaces the current first-stage snapshot, preserves the original request and future snapshots, and enforces the aggregate assignment limit. There is no reassignment after a stage advances. The task uses one pinned shared form; after any decision it becomes read-only for subsequent stages. Existing field-level preview permissions and validated-form approval requirements remain in force.

Notifications bind their recipient snapshot to the stage ID as well as remaining voters. A stage change invalidates an old pending notification even when the next stage has identical actor IDs. Existing at-least-once delivery and uncertain-send limitations remain unchanged; cancellation cannot retract an already accepted external message.

Existing non-sequential notification payloads omit the new optional stage field when it is absent. Loading and retrying a previously stored occurrence therefore preserves its serialized payload and stable notification identity rather than adding a null field during upgrade.

Conditional routes, dynamic stage creation, stage-specific forms or deadlines, post-vote reassignment, delegation, React configuration and Temporal workflow-to-task progression remain separate work. This local persistence increment does not establish live Entra directory integration or production hosting guarantees.
