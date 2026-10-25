# ADR 0024: Explicit local managed-approval composition

Status: accepted for the trusted-workstation `local-demo` profile.

Keep ordinary initialization and the existing welcome workflow compatible. Add an explicit `enable-tasks` command under the same exclusive installation lock as initialization and serving. It creates a separate immutable feature marker tied to installation identity and Dev scope, task and completion-journal stores, and a fixed participant grant for the existing synthetic demo user. It does not seed another project, add membership or change author grants.

Write the feature marker after provisioning completes. Repeated enable validates and returns existing state without modifying identity, keys or task data. Grant creation uses a fixed identity with revision zero, so an existing revoked tombstone cannot be treated as a missing grant and restored. Incomplete provisioning, missing enabled stores, mismatched installation identity or invalid schemas fail closed and preserve the partial state; there is no automatic recovery or regrant.

The local assignment resolver accepts only the initialized synthetic demo user, no groups or arbitrary external users. It grants no access. Signed task requests still use verified object IDs, current persisted membership and current participant grants. The local worker identity permits only `task.create` in the exact configured Dev scope; it is an explicit synthetic host capability, not an Entra workload identity adapter.

When enabled, the local runtime validates profile and persisted placement before starting Temporal or opening writable task stores. Register the existing managed-task creation activity on the owned worker and run the scoped background completion dispatcher inside that worker context. Cancel and await the dispatcher before stopping the worker, then close the task service and permanent completion journal before stopping the server. API request services use the same persistent task store through separate short-lived connections.

Preserve the existing runtime destination marker, start deduplication, exclusive ownership and database lifecycle. Keep the whole installation directory as one recovery unit. Opt-in does not change the Temporal port, namespace, queue or existing active project releases. The supplied approval-project example must pass actual project publication, activation and signed HTTP task-to-Temporal acceptance.

The feature adds no React UI, live Entra/group directory, task-bound form configuration, custom activity execution, notification transport or UAT/Prod deployment. `/health` remains process liveness, not dispatcher health or a production readiness check. Workflow termination does not yet reconcile outstanding tasks; background error monitoring and operator recovery remain separate work.
