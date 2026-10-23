# Durable runtime implementation plan

No implementation is authorized by this document. Read [shared contracts](../../contracts.md) and repository test guidance before future work.

## Ownership and dependencies

Own only `nexusflow/modules/05-durable-runtime/` and `nexusflow/modules/05-durable-runtime/tests/`. Own runtime start/step/control schemas in the module contract directory; changing shared envelopes requires coordinator review. Consume identity, immutable release resolver, activity dispatcher, human response and debug control ports. Use versioned fakes until providers exist. No dependency on Designer UI or live Entra/cloud services.

## Contract-first sequence and parallel lanes

1. Freeze execution bindings, interpreter operations, start deduplication, step events, response/control acknowledgements and checkpoint schemas against shared contracts. Record compatibility and cancellation semantics before concurrent work.
2. Lane A owns `core/`: deterministic interpreter, data mapping, joins, loops and control semantics.
3. Lane B owns `adapters/`: Temporal bridge, start adapters, checkpoints, worker lifecycle and projections. Consume frozen core interface using a fake core while A works.
4. Lane C owns `nexusflow/modules/05-durable-runtime/tests/`: contract fixtures, acceptance tests and replay/restart harness. Use fake core/ports; never edit implementation lanes.
5. Integrate serially, verify histories and publish provider contracts for human tasks/debugger. Providers can proceed on fakes after step 1, not after runtime completion.

## Verification

Use virtual clock and explicit synchronization, not sleeps. Cover RT-A1–A7, failure injection between commit/ack, duplicate/out-of-order messages, continuation and artifact unavailability. Temporal-specific tests use an isolated test environment. Application tests use the commands and integration conventions approved during stack setup. Run applicable future module checks and the application lint command selected during stack setup after code changes, documenting unrelated failures. No performance claim without reproducible sizing/load fixtures.
