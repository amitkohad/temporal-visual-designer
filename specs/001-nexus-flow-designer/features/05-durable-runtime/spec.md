# Durable workflow runtime

Status: NF-T004 linear feasibility proof implemented; complete feature requirements remain planned. Shared vocabulary and envelopes: [contracts](../../contracts.md).

## Scope

Execute immutable project releases on Temporal, independently of the Designer. Include manual/API, form, schedule and authenticated webhook start adapters; control flow; reusable workflows; durable waits; cancellation; activity dispatch and execution projections. Connector implementations belong to the activity feature. Do not modify Temporal server internals. No running-definition mutation or cross-cluster execution migration.

## Functional requirements

- **RT-FR-01:** Resolve an active project release at start and pin its digest, definition version, interpreter compatibility version, activity versions and environment configuration revision. Pin all workflows of the project consistently. Enforce organization/environment authorization on starts and reads.
- **RT-FR-02:** Interpret sequence, condition/switch, parallel/join, bounded loop, child workflow, typed data mapping and terminal states. Validate definitions before dispatch; reject unbounded cycles, unknown activities and invalid input without starting a run.
- **RT-FR-03:** Keep clock, randomness and external I/O outside nondeterministic interpreter logic. Route external effects through Activities; define stable per-step invocation/idempotency keys and declared retry/timeout policies. Never promise exactly-once external effects.
- **RT-FR-04:** Support durable timers, external responses, failure routes, explicit compensation, cancellation and child cancellation policies. Record step state transitions and redact sensitive payloads in projections.
- **RT-FR-05:** Deduplicate starts using organization/environment/project/workflow/start-source/idempotency key. Reuse with different payload returns a conflict. Verify webhook authentication and replay limits; schedules require timezone and overlap policy. Form and schedule adapters use the same start contract.
- **RT-FR-06:** Enforce configurable payload, loop, concurrency and history budgets. At safe boundaries, continue-as-new preserves logical execution ID, pinned versions, pending response routing and debugger state. Reject unsafe continuation or stop with a visible limit error before Temporal history exhaustion.
- **RT-FR-07:** Recover workers without losing decisions or duplicating acknowledged responses. Keep old compatible workers/artifacts available for active releases; fail activation compatibility validation before execution. Projection lag cannot alter authoritative execution state.

## Data and contracts

Runtime-owned records: ExecutionBinding (logical/run IDs, organization, environment, release digest, configuration revision, interpreter version); StepAttempt (stable step ID, iteration/branch, attempt, state, redacted payload references); StartReceipt; ExecutionCheckpoint. Define start, command, query, response acknowledgement and ordered step-event contracts under `nexusflow/modules/05-durable-runtime/contracts/`, aligned with the shared contract freeze. Human task/debug interfaces must tolerate duplicate messages and require expected revision/command ID. Event order is per execution, not global. Errors distinguish invalid definition, unauthorized, conflict, unavailable dependency and budget exceeded.

## Independently testable acceptance scenarios

- **RT-A1 positive:** Given a pinned purchase flow and fake activities, when a valid start branches and joins, then outputs and ordered transitions match its definition and exact dependency versions.
- **RT-A2 negative:** Given invalid input, unbounded cycles, wrong environment, or invalid webhook credentials, when a start is requested, then no workflow or external effect starts and a typed error is returned.
- **RT-A3 recovery:** Given a waiting approval and completed activity, when workers restart and the response is delivered twice, then replay is deterministic and the next step is entered once.
- **RT-A4 release isolation:** Given release A running, when B becomes active and a new start arrives, then the old run uses A throughout and the new run uses B; rollback affects only subsequent starts.
- **RT-A5 budgets:** Given a small configured history budget and bounded loop, when continuation occurs, then output, signal routing and pinned bindings survive; an impossible checkpoint yields a visible bounded failure.
- **RT-A6 start reliability:** Given a duplicate API/webhook delivery or overlapping scheduled occurrence, when adapters process it, then the declared deduplication/overlap policy holds; conflicting payload reuse fails.
- **RT-A7 failure control:** Given failing/slow activities and parallel children, when retry exhaustion or cancellation occurs, then configured error/compensation and child policies execute without unconfigured compensation.

## Success criteria

All RT-A scenarios pass with deterministic clock, fake activity dispatcher and fake release resolver without other Nexus Flow Designer services. A separate Temporal test-environment suite must replay captured histories without nondeterminism, cover worker restart and continuation, and demonstrate two concurrent release versions. Unauthorized scenarios yield zero dispatched effects. Numeric throughput/SLO targets remain a sizing decision, not an invented guarantee.

Evidence: [runtime proof](../../implementation-runtime-proof.md). Importable Python code lives in nexusflow.modules.durable_runtime; numbered spec directories identify ownership.
