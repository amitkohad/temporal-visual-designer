# Implementation: Entra pilot adapters + browser sign-in

Date: 2026-11-14. Phase 2 P1 subset of F01-T005 / NF-T002b.

## Delivered

- `MicrosoftJwksSigningKeys` and optional `MicrosoftGraphGroupResolver` with
  allowlisted HTTPS (`entra_http.py` / `entra_adapters.py`).
- Config-gated Hosted Dev composition (`entra_pilot.py`) that keeps local-demo
  working when Entra env is unset; composite JWKS routing by directory tenant.
- Public `GET /api/v1/auth/browser` exempt from bearer gate; never returns secrets.
- React MSAL Connect (`web/src/entra-auth.ts`) beside token paste in Projects/Tasks.
- Operator docs: `docs/entra-pilot.md`, updated `docs/pilot-runbook.md`.

## Verification

```sh
uv run pytest nexusflow/tests/identity/test_entra_adapters.py
cd web && npm test -- --run src/entra-auth.test.ts tests/Projects.test.tsx tests/Tasks.test.tsx
```

Recorded/mocked Microsoft boundaries only. No live Entra credentials were
available in the implementation environment.

## Explicit non-claims / remaining

- Live tenant consent, JWKS rotation, Graph overage/revocation smoke (F01-T005c).
- Entra OID task assignment in the local managed-approval profile.
- Environment switcher / grant feedback UI (F01-T003) — deferred as not required
  to enable Entra Connect.
- Full F01-T005 consumer conformance and production deployment.
