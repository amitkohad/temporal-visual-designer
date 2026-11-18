# Implementation: Entra Dev live-smoke prep (Phase 4)

Date: 2026-11-18. Nested slice of F01-T005c / NF-T002b Entra subset.

## Delivered

- Hardened Hosted Dev Entra config: `NEXUSFLOW_ENTRA_API_CLIENT_ID` defaults to the
  SPA client id when omitted; redirect URI restricted to loopback Hosted Dev origins.
- Example config without secrets: `examples/entra-pilot.config.example.json`.
- Operator docs: app registration / redirect / scopes in `docs/entra-pilot.md`.
- Critical-path recorded smoke: Entra-shaped bearer → `GET …/projects` in
  `nexusflow/tests/identity/test_entra_adapters.py`.
- Operator helper: `uv run nexusflow-entra-smoke` (`nexusflow/apps/entra_smoke.py`)
  for public browser config + one signed Dev projects list when a live token is
  supplied by the operator.
- Local-demo synthetic tokens remain the default when Entra env is unset.

## Verification

```sh
uv run pytest nexusflow/tests/identity/test_entra_adapters.py
```

No live Microsoft tenant credentials were available in the implementation
environment. Manual live steps are recorded in `docs/entra-pilot.md`.

## Explicit non-claims / remaining

- Live tenant consent, real JWKS fetch, Graph overage/revocation (F01-T005c2).
- Entra OID task assignment in the local managed-approval profile.
- Environment switcher / grant feedback UI (F01-T003 / F01-T005d).
- Full F01-T005 consumer conformance and production multi-tenant deployment.
