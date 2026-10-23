# ADR 0009: Durable start intents

Status: accepted for the isolated backend start boundary.

Manual/API starts use a persisted intent before dispatch. Its unique key is organization, environment, project, workflow, source and idempotency key. The fingerprint hashes canonical finite object JSON; key reuse with changed input conflicts. Authorization runs before every lookup, including retries. A new request resolves an atomic active release/configuration snapshot through a required port, then runs the existing proof compiler before reserving anything. Invalid definitions and scope mismatches cannot dispatch.

The winning SQLite reservation pins the complete immutable execution plan and a random logical execution ID. Separate connections arbitrate through the database unique constraint. Retries use the stored plan even if activation or configuration changes. Pending dispatch is retried by resubmitting the same authorized request; an acknowledged intent avoids dispatch. There is no background queue drainer in this increment.

Delivery is at least once. A dispatcher MUST permanently deduplicate by execution ID, compare the immutable plan on retries and acknowledge an existing execution even after completion. A timeout after remote acceptance or a crash before local acknowledgement can cause another delivery. This boundary does not establish exactly-once external effects. Retention or deletion of deduplication records must not be enabled without a defined replay window and compatible remote execution-retention policy.

`StartAuthorization`, `ActiveReleaseResolver` and `StartDispatcher` are trusted server-side ports, not callable client assertions. No default permissive adapters are supplied. Identity-backed start permission, production activation resolution, Temporal client dispatch/reuse semantics, release/artifact holds and transactional cross-module integration remain open. Fakes demonstrate the contracts only; this service is not wired into a deployed application.

The plan still uses `proof.v1`: a single linear workflow with literal inputs. It does not enable branching, binding expressions, loops or custom Python execution. Only manual/API source identifiers are accepted; webhook authentication/replay, forms, schedules/timezones/overlap and checkpoint semantics remain separate gates.

SQLite contains input payloads and complete plans. It is private application storage, never a telemetry projection or public receipt endpoint. Production storage requires access controls, encryption, retention and payload-redaction decisions. The current 64 KiB input and proof definition/output limits remain in effect. No throughput or production isolation guarantee is inferred from local SQLite tests.
