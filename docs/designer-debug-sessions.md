# Debug an immutable Dev project revision

An explicitly configured host supplies a `SQLiteDebugSessionRepository` as `ApiBackend.debug_sessions` and `TemporalDebugRuntime` as `ApiBackend.debug_runtime`. The adapter uses the existing permanent start journal and trusted environment placement. The standalone local-demo launcher configures these providers after the explicit [enable-debugger command](local-development.md#enable-dev-debugging).

Create a session with Dev-scoped signed headers and `debug.execute`:

```http
POST /projects/hr/debug-sessions
Content-Type: application/json

{
  "operation_id": "debug-001",
  "source_revision": 1,
  "workflow_id": "onboarding",
  "configuration_revision": "local-v1",
  "input": {},
  "breakpoints": ["review"],
  "mode": "real"
}
```

The selected source revision stays immutable even if the draft is edited. The workflow and all project artifacts must fit the supported sequential proof subset and destination readiness. There is no required publication or activation. Real mode is explicit: Dev activities may have external effects.

A successful 202 response supplies session metadata, execution identity, acknowledged Temporal run ID and state `started`. It does not mean the workflow has completed. Reuse the same operation and exact request after a lost response; it resumes the same frozen intent, not a new execution. Changed requests conflict. A new operation creates a new session. The default limit is 1,000 retained sessions per scoped project.

Query status:

```http
GET /projects/hr/debug-sessions/{session_id}
```

The response contains `session` metadata and a payload-free `snapshot` with current node, state, runtime revision and completed node IDs. A run initially pauses before its first executable step. After continuing it runs until a configured breakpoint or a real timer/approval wait. Use step-over or continue with the returned node/revision:

```http
POST /projects/hr/debug-sessions/{session_id}/commands
Content-Type: application/json

{
  "command_id": "step-001",
  "node_id": "review",
  "action": "step_over",
  "expected_revision": 0
}
```

Command identities and successful receipts are durable in the session database (up to 1,000 commands per session). Exact retries reuse the command receipt. Changed content under the same command ID conflicts. Stale or premature commands conflict; use a new command ID for a new intent. A permit releases one eligible sequential step; it does not fabricate an approval or bypass a timer. Control requests target the saved exact Temporal run.

Every operation rechecks permission and ownership. UAT/Prod, another owner and revoked access are refused. Missing providers and runtime/storage failures return safe errors. Pending dispatch must be resumed by retrying the original create request before status/control can proceed. Changed trusted placement conflicts rather than redirecting a persisted session.

Use `action: "cancel"` at a paused boundary, timer wait or undecided unmanaged approval wait to request exact-run Temporal cancellation without releasing the next step. See [cancellation and recovery](debug-session-cancellation.md) for the `cancelling` state and retry behavior.

Keep the session database, permanent dispatch journal, project/catalog/configuration stores and Temporal history together during recovery. Sessions and artifact holds are conservative and have no cleanup/expiry operation yet. Browser controls, general runtime variable/step-input/output inspection, managed-task/active-activity cancellation, mock connections and parallel stepping remain future increments.

Read [frozen workflow inputs](debug-input-inspection.md) through the separate `/inputs` route. Only explicitly public primitive fields are disclosed. Inspection does not advance the workflow or require a running worker. Runtime outputs and general variables remain planned.

Use [owner-scoped session discovery](debug-session-discovery.md) to reconnect to saved sessions. Listing returns paginated metadata without querying Temporal; use the selected session status route for live state.

Use [explicit rerun](debug-rerun.md) to start a new revision or fresh inputs from an acknowledged session. Supply the full start request and a new operation ID. The original execution remains unchanged; exact retries recover the same child.
