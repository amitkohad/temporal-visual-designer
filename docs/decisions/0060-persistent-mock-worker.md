# ADR 0060: Explicit Temporal worker over read-only persistent fixtures

Status: accepted for explicitly composed synthetic Dev mock workers.

## Decision

Add `open_persistent_http_mock_activities` as a host composition context manager.
It opens an existing fixture database read-only, composes the existing explicit
Dev policy, mock executor, HTTP JSON adapter and Temporal mock dispatcher, and
yields activities for a host to register. It does not start a worker, grant
permission, create fixtures or change default launcher behavior.

Hosts supply the database, fresh worker authorization port, trusted scope,
namespace, task queue and explicitly eligible Dev scopes. Validate configuration
before storage access. Scope names alone grant nothing. The existing dispatcher
still verifies actual SDK context, exact activity reference, release fields and
mock-only inputs. Worker permission is checked before and after fixture lookup;
admin fixture-writing permission is separate and insufficient for execution.

The factory has no live executor or transport dependency. Its HTTP adapter has
only the mock port configured. Existing run-bound operation IDs, private errors,
status-only outputs and bounded deadlines remain unchanged. Synthetic fixture
response data must not enter activity outputs or history. Input literals retain
the proof workflow's existing history behavior and must be synthetic.

## Storage and lifecycle

`SQLiteHTTPMockScenarios(read_only=True)` requires an existing regular database
and opens SQLite `mode=ro`. It validates the effective committed schema, including
WAL, without schema creation, migrations or journal-mode conversion. PUT fails
privately before payload processing. Existing administrative writable defaults
remain unchanged. Runtime GET/list metadata behavior and content-integrity checks
remain the existing implementation.

Share exact schema validation with local profile loading instead of duplicating
the seven-column/type/not-null/primary-key contract. Validate physical column
order too, since administrative inserts use that canonical layout. Read-only SQLite can manage
transient locking/WAL sidecars; this is not a claim of immutable filesystem
bytes. Other authorized administrative connections may commit immutable new
fixture revisions; subsequent reads see committed data rather than a startup
snapshot. Same-key replacement remains forbidden by the administrative store.

Stop the registered Temporal worker before leaving the factory context. The host
owns worker drain and the context owns store closure, including setup failures.
Context-body exceptions propagate unchanged. This does not add heartbeats or
promise immediate cancellation delivery to a running activity.

## Integration and limits

Independent acceptance uses signed fixture creation and actual compiled proof
plans on explicitly registered Temporal workers. Persisted pins, worker/store
restart and replay are verified separately from project publication. The built-in
descriptor is still contract metadata, not an executable Python artifact; default
project publication, activation and app start remain blocked for it.

No local default worker registration, new CLI grant, live effects, browser UI,
fixture packages, production storage or deployed Entra authorization is added.
Host ports remain trusted configuration, not a sandbox against malicious hosts.
See [increment evidence](../../specs/001-nexus-flow-designer/implementation-persistent-mock-worker.md).
