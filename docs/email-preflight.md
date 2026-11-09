# Prepare an authorized email envelope

Explicit worker hosts can compose `EmailPreflightPlanner` with an email connection
repository and fresh worker authorization port:

```python
from nexusflow.modules.connectors.email_preflight import EmailPreflightPlanner

planner = EmailPreflightPlanner(repository, worker_authorization)
envelope = await planner.prepare(scope, operation_id, email_input)
```

`scope` and `operation_id` are trusted host values. `email_input` is the existing
typed email activity input. The planner requires `worker.execute` before reading
the private revision and again afterward. Administrator connection-management
permission does not grant worker access.

The definition supplies the canonical sender, SMTP host/port and opaque secret
reference. All To/Cc/Bcc domains must match its exact recipient allowlist; allowing
`example.test` does not allow `sub.example.test`. Input and definition snapshots
are independently validated, pinned and copied. Private envelope fields are
excluded from its representation and from fixed error messages.

The result is a frozen preparation snapshot. It does not send email, resolve a
credential, perform DNS/TLS checks or authorize a later send by possession alone.
Mock-mode input is forbidden until an actual email mock provider is implemented.
Use synthetic message data for local testing.

The default deadline is five seconds, configurable up to 30. Synchronous storage
runs off the event loop. Delivered cancellation propagates and late answers are
rejected; an already running SQLite read may finish after its caller stops waiting.
No retries or possible delivery are reported by preflight errors.

Transport, egress/secret controls, durable effects, worker registration and browser
flows remain future work. See [ADR 0063](decisions/0063-email-preflight.md).

The resulting snapshot can feed the offline [MIME encoder](email-mime.md).
Encoding adds no sending authority or external effect.
