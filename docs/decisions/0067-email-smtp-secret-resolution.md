# ADR 0067: Email SMTP secret resolution and authorized send

Status: accepted for the opt-in local-demo / controlled SMTPS subset of feature 08.

## Decision

Add an explicit `EmailSecretResolver` port and authorized SMTP send executor that
compose existing email preflight and MIME encoding without changing those
offline contracts. Connection revisions continue to store opaque
`secret_reference` labels only. Secret values resolve only inside authorized
workers immediately before any SMTP DNS/connect and again at a pre-DATA
`before_write` gate.

Freeze:

- `SMTPLoginCredential` — scheme allowlist (`login` only for this increment),
  private username/password, safe `__repr__`, bounded lengths, no CR/LF/C0.
- `EmailSecretResolver.resolve(scope, secret_reference)` — returns a login
  credential or raises closed `SecretResolverError` codes; never echoes
  reference values, usernames, or passwords.
- Live send **requires** `secret_reference` (`credentials_required` when absent).
  Open-relay / no-AUTH SMTP is out of scope.
- `AuthorizedEmailSendExecutor` composing preflight → MIME → secret resolve →
  pinned SMTPS transport.
- Optional private credential on the SMTP send request (`repr=False`) consumed
  only by the pinned sender.

Do not reuse `HTTPAuthorizationCredential` or Bearer `put-secret` for SMTP AUTH.
HTTP `SecretResolver` and credential-free HTTP executors remain unchanged.

## Authorization and privacy

Authorize `worker.execute` (via existing email preflight) before loading the
connection. Resolve secrets only after a successful live preflight and MIME
encode preparation context is available, and **before** SMTP DNS/connect.
Missing/forbidden secrets fail closed without network. Revalidation and the
transport `before_write` guard recheck permission, definition integrity, and that
the same reference still resolves to the same scheme. Raw credentials never enter
errors, activity output, Temporal history, or connection metadata.

Transport may AUTH LOGIN/PLAIN with the private credential, then MAIL FROM /
RCPT TO / DATA using the encoded envelope. Single attempt only; after DATA may
have been accepted, failures are `delivery_may_have_occurred` and non-retryable
at the application boundary.

## Local-demo vs production

Local-demo may use an explicit file-backed SMTP secret map under the
installation directory (`email-secrets.json`). That map is not Azure Key Vault,
HSM, managed identity, or a production secret provider. Controlled loopback
SMTPS acceptance does not establish public egress or internet mail delivery.
No automatic retries or durable effect recovery are added.

## Remaining scope

Additional SMTP AUTH schemes (XOAUTH2), email mocks, Designer activity palette,
durable ambiguous-delivery recovery, UAT/Prod binding promote, default launcher
enablement, and production SMTP providers remain open. See
[ADR 0068](0068-temporal-live-email.md) for Temporal/worker registration.
