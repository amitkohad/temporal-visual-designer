# ADR 0020: Explicit participant vote delegation

Status: accepted for bounded participant delegation in the local human-task service.

An eligible human-task participant may explicitly transfer their own uncast vote in the current approval stage to one resolved user. The task must remain pending. The target cannot be the participant or an already eligible current-stage voter. Group targets and implicit membership refresh are excluded. This bounded operation neither rewrites a recorded vote nor replaces the entire electorate.

Every command freshly requires scoped `task.respond` authorization and uses the verified Entra object ID at the HTTP boundary. Administrative permission does not bypass participant eligibility. Resolving a target does not grant that user a role: the recipient must independently satisfy scope authorization to read, submit a form or decide. Missing or failed directory resolution fails closed.

`POST /tasks/{id}/delegations` accepts a command ID, expected revision and target user ID through the explicitly configured assignment resolver. For a sequential task, delegation changes only the current stage snapshot. Other voters, previous decisions, future stages and the immutable creation request remain unchanged. The one pinned shared form remains read-only after any task vote.

The old participant loses current read, inbox, form and new-vote eligibility once the transfer commits. After fresh scope authorization, that actor may retrieve their own exact accepted delegation receipt even though they no longer belong to the current snapshot. This historical response grants no current access or new action. Changed-content retries and command IDs reused across command kinds conflict.

SQLite serializes the operation using the shared expected revision. The replacement snapshot, before/after audit, assignment history and immutable command receipt commit atomically. Delegations and administrative reassignments share the existing limit of 100 assignment changes per task. Delegation creates no completion outbox item. Only a subsequent terminal decision completes the task.

Automatic delegation, claim/release, conditional routes, changing cast votes, React interactions and Temporal workflow-to-task integration remain separate work. Local synthetic identity and resolver tests cannot establish live Entra behavior or production provider capacity. The existing notification delivery guarantees remain unchanged: a pending occurrence whose recipient snapshot becomes stale is cancelled, but an external notification already accepted cannot be retracted.

