# Live HTTP JSON secrets + Temporal registration increment

Branch: `feature/phase-34-live-http-f663`.

## Bounded implementation

1. Freeze secret-aware attempt contracts ([ADR 0065](../../docs/decisions/0065-http-secret-resolution.md))
   and opt-in Temporal/launcher live dispatch ([ADR 0066](../../docs/decisions/0066-temporal-live-http.md)).
2. Keep credential-free `HTTPAttemptExecutor` unchanged; add
   `AuthorizedHTTPAttemptExecutor`, `SecretResolver`, and authorized planner
   methods that resolve before DNS.
3. Extend pinned transport with optional private `Authorization` on
   `HTTPAttemptRequest` (Bearer only).
4. Add dual-mode `HTTPJSONActivities` proof dispatch and
   `nexusflow-local enable-live-http` / `put-secret` composition.
5. Critical-path unit, controlled TLS, and real Temporal live dispatch tests.
   Do not claim public egress, Key Vault, publication, or F04-T007.

## Verification

Focused suites:

- `test_secret_types.py`, `test_authorized_destination.py`,
  `test_temporal_http_policy.py`, `test_live_http_profile.py`,
  `test_authorized_execution_acceptance.py`,
  `test_credential_free_destination.py` — secrets resolve before DNS;
  credential-free path still rejects configured secrets; Bearer on controlled
  TLS wire; profile enablement.
- `pytest -m integration nexusflow/tests/runtime/test_http_live_dispatch_integration.py`
  — status-only live proof dispatch + replay; mock mode denied without mock port.

## Remaining scope

Email secrets, additional auth schemes, Designer activity palette binding,
durable live-effect recovery, default-on launcher, built-in executable
publication/capability registration, F04-T007 custom Python isolation, and full
F08-A01–A05 browser/production acceptance remain open.
