# ADR 0036: Durable Dev debugger sessions for the sequential proof runtime

Status: accepted for the bounded backend increment.

The proof interpreter already has durable pause/step/continue gates, but no authenticated session API. Add an explicitly configured session repository/runtime adapter without changing existing execution plans, start fingerprints or workflow commands. This increment supports sequential `proof.v1` only.

## Freeze before dispatch

A create request identifies a project revision, workflow, input, configuration revision, breakpoints and stable operation ID. It must explicitly select `mode: real`; there is no implicit mock mode. Validate the whole immutable project snapshot against catalog/configuration/runtime readiness, compile the selected workflow for debugging, and retain referenced artifacts conservatively. Debugging does not publish a project release or activate anything.

Persist the original request, verified owner, frozen plan, trusted environment placement and stable execution identity before dispatch. Scoped project/operation retries reuse the first snapshot and execution even after drafts change. Failed or ambiguous starts remain resumable by retrying the original operation through the existing permanent dispatch journal. Once acknowledged, persist and use the exact Temporal run ID. Reject changed requests or placement; do not silently move a session to another worker destination. Retain sessions and journal records permanently in this increment, with a default 1,000 sessions per scoped project.

## Authorization and control

Every create/read/control call requires fresh `debug.execute` and an explicit Dev guard before provider access. Only the original owner may inspect or control a session. Generic hosts opt in with both session and runtime providers; the existing local demo remains unchanged. Query/control targets use the saved execution/run IDs and verified plan binding, not caller-supplied run identity.

Expose only session metadata and the existing payload-free runtime snapshot: current node/state, command revision and completed node IDs. Step-over and continue use stable command IDs and expected runtime revisions. Reserve command identity in the session database before dispatch and retain successful receipts. A command ID with changed content conflicts even while pending; an exact successful retry returns the original receipt before querying runtime. Existing workflow command receipts and stable Temporal update IDs recover ambiguous acknowledgements. Stale/premature commands conflict rather than granting a later permit. Timers and approvals remain real waits. Queries do not expose workflow inputs, activity outputs or secret values.

A pending start is recovered by replaying its original create request; status/control reject until dispatch is acknowledged. This is not a cancellation, expiry or event projection system. Repeated completed-start requests return the original session metadata; runtime status is queried separately.

## Remaining scope

Variable inspection/redaction, cancellation/expiry, mock connections, parallel frontier semantics, event replay and the React Designer debugger panel remain open. Real Dev actions may have external effects and normal retries. Production identity/worker/secret integration and production debugging are outside this boundary. Synthetic signed identity and actual local Temporal tests establish only the stated local backend acceptance.
