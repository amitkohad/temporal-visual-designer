# Entra pilot enablement (Phase 2 P1)

How to turn on Microsoft Entra browser sign-in beside the local-demo token path
on Hosted Dev (`nexusflow-local serve --ui`).

## What this proves

| Verified | Not verified (needs live tenant) |
|---|---|
| Config-gated Microsoft JWKS adapter with allowlisted HTTPS | Live Microsoft JWKS fetch against your tenant |
| Optional Graph group resolver with fail-closed errors | Live Graph consent, overage paging, revocation SLA |
| Public `GET /api/v1/auth/browser` SPA config (no secrets) | App registration / redirect URI consent in Entra |
| MSAL popup Connect in Projects/Tasks when config enabled | End-to-end SSO with real users in a customer tenant |
| Local-demo token paste still works when Entra env is unset | Managed-approval task assignment for Entra OIDs |
| Critical-path tests with **recorded/mocked** Microsoft boundaries | Production multi-tenant hosting, TLS edge, HA |

Do **not** treat mocked-boundary tests as production-provider acceptance.

## Prerequisites

Same as [pilot-runbook.md](pilot-runbook.md), plus:

1. An Entra app registration for the **SPA** (public client, PKCE).
2. An API app registration (or exposed API on the same app) with scope
   `access_as_user` (or your chosen delegated scope) and audience
   `api://…`.
3. SPA redirect URI matching the Hosted Dev origin, typically
   `http://127.0.0.1:8000/`.
4. Author **object IDs** (Entra `oid`) to bootstrap as Dev `business_author`.

## Environment variables

Set these before `nexusflow-local serve` (or point `NEXUSFLOW_ENTRA_CONFIG` at a
JSON file with the same fields). When the core fields are absent, Entra stays
**off** and local-demo continues unchanged.

| Variable | Purpose |
|---|---|
| `NEXUSFLOW_ENTRA_ORGANIZATION_ID` | Nexus Flow organization id (use a distinct id from `local-demo`) |
| `NEXUSFLOW_ENTRA_TENANT_ID` | Directory tenant GUID |
| `NEXUSFLOW_ENTRA_SPA_CLIENT_ID` | SPA application (client) ID |
| `NEXUSFLOW_ENTRA_API_AUDIENCE` | API audience (e.g. `api://nexus-flow-pilot`) |
| `NEXUSFLOW_ENTRA_API_CLIENT_ID` | Allowed `azp` / API client id (often the SPA id) |
| `NEXUSFLOW_ENTRA_DELEGATED_SCOPE` | Optional; default `access_as_user` |
| `NEXUSFLOW_ENTRA_REDIRECT_URI` | Optional; default `http://127.0.0.1:8000/` |
| `NEXUSFLOW_ENTRA_AUTHOR_OBJECT_IDS` | Comma-separated Entra user `oid` values to grant Dev author |
| `NEXUSFLOW_ENTRA_GRAPH_CLIENT_ID` | Optional app-only client for Graph group refresh |
| `NEXUSFLOW_ENTRA_GRAPH_CLIENT_SECRET` | Optional; required with Graph client id |
| `NEXUSFLOW_ENTRA_CONFIG` | Optional path to JSON overlay for the fields above |

## Operator steps

```sh
uv sync --locked
cd web && npm ci && npm run build && cd ..
uv run nexusflow-local init
uv run nexusflow-local enable-tasks
# export NEXUSFLOW_ENTRA_* …
uv run nexusflow-local serve --ui
```

1. Open the printed Hosted Dev URL.
2. Confirm `GET /api/v1/auth/browser` returns `"enabled": true` and your SPA fields.
3. In **Projects**, use **Sign in with Microsoft** (org/env are filled from config)
   or keep pasting `uv run nexusflow-local token` for `local-demo` / `dev`.
4. Author/publish against the Entra organization when grants exist for your `oid`.

## Local-demo vs Entra

| Path | When | Connect values |
|---|---|---|
| Token paste | Always (default) | org `local-demo`, env `dev`, token from `nexusflow-local token` |
| Microsoft sign-in | Entra env configured | org/env from `/api/v1/auth/browser`; MSAL access token |

Both can coexist: local-demo keeps synthetic JWKS; Entra org uses Microsoft JWKS
routed by directory tenant.

## Remaining live-tenant smoke

Before calling F01-T005 / NF-T002b complete for production:

1. Register SPA + API apps; grant admin consent for delegated scope (and Graph
   `GroupMember.Read.All` or equivalent if using group resolver).
2. Run Hosted Dev with real `NEXUSFLOW_ENTRA_*` values; sign in; call a scoped
   Projects API with the acquired token.
3. Confirm JWKS rotation / unknown `kid` fails closed.
4. If using Graph: revoke group membership and confirm next request denies.
5. Task inbox for Entra assignees (local task profile still resolves the
   synthetic local-demo user only) — follow-up work.
6. Environment switcher / grant feedback UI — deferred (Phase 2 P1 follow-up;
   not required to enable Entra Connect).

## Spec pointers

- Feature tasks: [features/01-identity-environments/tasks.md](../specs/001-nexus-flow-designer/features/01-identity-environments/tasks.md)
- ADR: [decisions/0003-identity-environments.md](decisions/0003-identity-environments.md)
- Runbook: [pilot-runbook.md](pilot-runbook.md)
