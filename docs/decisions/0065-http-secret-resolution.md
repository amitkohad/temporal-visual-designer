# ADR 0065: Secret-aware HTTP attempt coordination

Status: accepted for the opt-in local-demo / controlled TLS subset of feature 08.

## Decision

Add an explicit `SecretResolver` port and authorized destination preparation that
keeps the existing credential-free path unchanged. Connection revisions continue
to store opaque `secret_reference` labels only. Secret values resolve only inside
authorized workers immediately before DNS and again at post-DNS / pre-write gates.

Freeze:

- `HTTPAuthorizationCredential` — scheme allowlist (`Bearer` only for this
  increment), private token material, safe `__repr__`, bounded lengths.
- `SecretResolver.resolve(scope, secret_reference)` — returns a credential handle
  or raises closed errors; never echoes reference values or tokens.
- `prepare_authorized` / `revalidate_authorized` on `HTTPDestinationPlanner`.
- Optional private `authorization` on `HTTPAttemptRequest` (full header value,
  `repr=False`) consumed only by the pinned sender.
- `AuthorizedHTTPAttemptExecutor` composing authorized planner + sender.

## Authorization and privacy

Authorize `worker.execute` before loading the connection and before any secret
lookup. When `secret_reference` is absent, authorized preparation matches the
credential-free path and never attaches Authorization. When present, resolve
before DNS; missing/forbidden secrets become `credentials_required` or
`forbidden` without invoking DNS. Revalidation and the transport `before_write`
guard recheck permission, definition integrity, and that the same reference still
resolves to the same scheme (token comparison is by scheme only in public
diagnostics; raw tokens never enter errors, activity output, or Temporal history).

Transport may emit a single `Authorization` header from the private attempt field.
Arbitrary caller headers remain forbidden. Credential-free executors continue to
reject configured secrets before DNS and must never set authorization.

## Local-demo vs production

Local-demo may use an explicit file-backed secret map under the installation
directory. That map is not Azure Key Vault, HSM, managed identity, or a
production secret provider. Controlled loopback TLS acceptance does not establish
public egress. No automatic retries or durable effect recovery are added.

## Remaining scope

Email secret resolution, additional auth schemes, response mapping/redaction,
debugger secret UX, UAT/Prod binding promote, and default launcher enablement
remain open. See [ADR 0066](0066-temporal-live-http.md) for Temporal/worker
registration.
