# Basic Designer debugger

Status: specification only. See [shared contracts](../../contracts.md).

## Scope and requirements

Offer debugging inside the visual Designer, against Dev only. No production debugger, Python source debugger, live variable editing, step-into child workflows, time travel or definition mutation during a run.

- **DB-FR-01:** Create debug sessions from an immutable draft snapshot with workflow/input/activity versions, runtime compatibility, connection mode and organization/Dev binding. Reject UAT/Prod sessions and unauthorized users server-side, regardless of UI.
- **DB-FR-02:** Support breakpoints before executable steps, continue, step-over, cancel and rerun. Pause at durable boundaries; no pending external side effect starts for a paused step. Already dispatched activities cannot be frozen or undone.
- **DB-FR-03:** Display current node/branch, variables, step inputs/outputs, attempts and errors with schema-aware redaction and payload limits. Check permission for every inspection. Hidden secrets must not appear in browser state, logs or debug events.
- **DB-FR-04:** Persist session state and breakpoint/control commands through the runtime's durable debug port. Commands carry session revision and unique IDs; duplicate commands have one effect and stale commands return conflict. Reconnection/restart restores paused state.
- **DB-FR-05:** Freeze the runnable frontier when paused. Step-over permits one selected eligible node invocation; in parallel branches others remain gated, while previously running operations may finish. Child workflows are one step. Continue releases eligible nodes until the next breakpoint; process independent arrivals deterministically.
- **DB-FR-06:** Make mock versus real Dev connections explicit before start. Real actions can have external effects and obey normal retries/idempotency policies. Rerun creates a new session/execution with fresh invocation identities; edits never change the old session.
- **DB-FR-07:** Durable timers/human waits remain visible waiting states. Step-over does not manufacture a human approval or bypass a timer. Cancellation propagates using the configured runtime policy; idle-session expiry requests cancellation and records any still-running external operations.

## Data and contracts

Own DebugSession (snapshot digest, execution binding, actor permissions, mode, state/revision, expiry), BreakpointSet and DebugCommandReceipt. Runtime owns step gates/checkpoints; debugger owns presentation/session APIs. Freeze create/inspect/control/event replay contracts in `nexusflow/modules/07-designer-debugger/contracts/`. Use ordered cursor-based event replay with snapshot reconciliation after gaps. Bounded redacted payload references avoid copying execution secrets to session stores.

## Acceptance scenarios

- **DB-A1 positive:** Given a Dev snapshot and breakpoint, when start/step/continue execute, then the canvas follows runtime events and exactly the selected invocation crosses its gate.
- **DB-A2 negative:** Given a UAT/Prod target, foreign user or protected variable, when APIs are called directly, then start/inspection is denied and sensitive values are absent from responses/events.
- **DB-A3 recovery:** Given a paused session, when worker/browser restarts and the last command is resent, then pause and bindings survive and no extra step executes.
- **DB-A4 parallel:** Given two eligible branches and one in-flight external action, when a branch is stepped, then only that eligible invocation is released; an in-flight action can finish and is accurately shown.
- **DB-A5 versions:** Given a paused run, when its draft is edited and rerun, then a new session uses the new snapshot while the old run remains pinned.
- **DB-A6 waiting/cancel:** Given a timer or approval wait and duplicate cancel command, when control is applied, then no fabricated response advances the flow and cancellation follows runtime policy once.
- **DB-A7 inspection:** Given oversized/secret output and an event-stream gap, when reconnecting, then a reconciled redacted snapshot and bounded inspection are shown without losing ordering.

## Success criteria

DB-A1–A7 pass without the full Designer using a fake runtime gate/event port and fake snapshot provider. A separate real Temporal runtime scenario proves a paused gate survives restart and continuation. Every debug execution is Dev-scoped; every control command is idempotent. UI tests verify keyboard operation, clear paused/running/waiting states and branch selection. Throughput targets remain outside this initial debugger feature.
