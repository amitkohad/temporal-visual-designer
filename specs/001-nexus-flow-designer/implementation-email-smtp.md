# Live email SMTP secrets + Temporal registration increment

Branch: `feature/phase-3-email-smtp-9e27`.

## Bounded implementation

1. Freeze SMTP login secret contracts ([ADR 0067](../../docs/decisions/0067-email-smtp-secret-resolution.md))
   and opt-in Temporal/launcher live email dispatch
   ([ADR 0068](../../docs/decisions/0068-temporal-live-email.md)).
2. Keep offline email preflight and MIME unchanged; add
   `AuthorizedEmailSendExecutor`, `EmailSecretResolver` /
   `SMTPLoginCredential`, and pinned SMTPS transport that AUTH before DATA.
3. Resolve opaque `secret_reference` **before** SMTP connect; require a secret
   for live send; never reuse HTTP Bearer credentials for SMTP AUTH.
4. Add live-only `EmailTextActivities` proof dispatch, optional
   `CombinedProofDispatch` router when HTTP+email profiles coexist, and
   `nexusflow-local enable-live-email` / `put-smtp-secret` composition.
5. Wire local-demo `email-connections.db` into `ApiBackend` and Connections UI
   email metadata registration (opaque secret labels only).
6. Critical-path unit, profile, and real Temporal live dispatch tests (fake
   sender for integration). Do not claim public SMTP egress, Key Vault,
   publication, Designer palette, or F04-T007.

## Verification

Focused suites:

- `test_smtp_secret_types.py`, `test_authorized_email_execution.py`,
  `test_temporal_email_policy.py`, `test_live_email_profile.py` — credentials
  hide passwords; resolve before sender; missing secret fails closed; live
  identity kind; profile enablement.
- `pytest -m integration nexusflow/tests/runtime/test_email_live_dispatch_integration.py`
  — status-only live proof dispatch + replay with fake executor (not production
  SMTP acceptance).

## Remaining scope

Email mocks, Designer activity palette binding, durable ambiguous-delivery
recovery, default-on launcher, built-in executable publication/capability
registration (`connector.email.text.v1`), controlled loopback SMTPS wire
acceptance beyond fake sender, F04-T007 custom Python isolation, and full
F08-A01–A05 browser/production acceptance remain open.
