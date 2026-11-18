# Entra pilot enablement (Phase 2 P1 / Phase 4 Dev smoke)

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
| Operator `nexusflow-entra-smoke` for browser config + projects list | Automatic live-tenant CI without operator credentials |

Do **not** treat mocked-boundary tests as production-provider acceptance.

## App registration (no secrets in repo)

Create registrations in your Microsoft Entra tenant (Azure portal → Microsoft Entra ID
→ App registrations). Do not commit client secrets, tokens, or tenant-specific IDs
into git; use environment variables or a local JSON file referenced by
`NEXUSFLOW_ENTRA_CONFIG`.

### 1. SPA (public client)

1. New registration, **Accounts in this organizational directory only** (single tenant)
   for the Dev pilot.
2. **Authentication** → platform **Single-page application**.
3. Redirect URI must match Hosted Dev, typically `http://127.0.0.1:8000/`
   (trailing slash recommended). Loopback `http://localhost:8000/` is also accepted.
4. Enable **Allow public client flows** if your portal UX requires it for PKCE SPAs.
5. Note the **Application (client) ID** → `NEXUSFLOW_ENTRA_SPA_CLIENT_ID`.

### 2. API audience / delegated scope

Either expose an API on the same app or use a separate API registration:

1. **Expose an API** → Application ID URI such as `api://nexus-flow-pilot`
   → `NEXUSFLOW_ENTRA_API_AUDIENCE`.
2. Add a delegated scope, default name `access_as_user`
   → `NEXUSFLOW_ENTRA_DELEGATED_SCOPE` (optional; default `access_as_user`).
3. Under the SPA, **API permissions** → add the delegated scope → **Grant admin consent**.
4. `NEXUSFLOW_ENTRA_API_CLIENT_ID` is the allowed token `azp` (authorized party). For a
   same-app SPA it is usually the SPA client id; when omitted, Hosted Dev defaults it
   to `NEXUSFLOW_ENTRA_SPA_CLIENT_ID`.

### 3. Authors and optional Graph

1. Collect each pilot author's Entra user **Object ID** (`oid`) →
   `NEXUSFLOW_ENTRA_AUTHOR_OBJECT_IDS` (comma-separated). Hosted Dev bootstraps Dev
   `business_author` grants for those OIDs.
2. Optional group refresh: app-only client with Graph application permission
   (for example `GroupMember.Read.All`) plus `NEXUSFLOW_ENTRA_GRAPH_CLIENT_ID` /
   `NEXUSFLOW_ENTRA_GRAPH_CLIENT_SECRET`. Without Graph credentials, OID grants still
   work; signed group claims are not accepted without a resolver.

### 4. Example config file

Copy [`examples/entra-pilot.config.example.json`](../examples/entra-pilot.config.example.json),
replace placeholder UUIDs, and point `NEXUSFLOW_ENTRA_CONFIG` at the file. Never commit
a filled-in copy. Graph client secrets belong only in the environment, not in the
example file.

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
| `NEXUSFLOW_ENTRA_API_CLIENT_ID` | Allowed `azp` / API client id (defaults to SPA id when omitted) |
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
# export NEXUSFLOW_ENTRA_* …  (or NEXUSFLOW_ENTRA_CONFIG=./entra-pilot.config.json)
uv run nexusflow-local serve --ui
```

1. Open the printed Hosted Dev URL.
2. Confirm `GET /api/v1/auth/browser` returns `"enabled": true` and your SPA fields
   (no secrets).
3. In **Projects**, use **Sign in with Microsoft** (org/env are filled from config)
   or keep pasting `uv run nexusflow-local token` for `local-demo` / `dev`.
4. Author/publish against the Entra organization when grants exist for your `oid`.

## Critical-path smoke (recorded + manual live)

### Automated (no Microsoft credentials)

```sh
uv run pytest nexusflow/tests/identity/test_entra_adapters.py
```

These cases use recorded/mocked JWKS boundaries and prove: config gate, public
browser auth, and an Entra-shaped bearer token listing Dev projects. They do
**not** claim live-tenant acceptance.

### Manual live tenant (operator credentials required)

When a real tenant is available (never invent secrets in agents/CI):

1. Complete app registration and admin consent above.
2. Export `NEXUSFLOW_ENTRA_*` (or config file) including your author `oid`.
3. `uv run nexusflow-local serve --ui` with network access to
   `login.microsoftonline.com` (and Graph if configured).
4. Browser: **Sign in with Microsoft** → confirm Projects loads for the Entra org.
5. Or CLI after copying the MSAL access token from the browser session storage /
   network tab (do not commit the token):

```sh
uv run nexusflow-entra-smoke --check-browser-only
uv run nexusflow-entra-smoke --token "$ENTRA_ACCESS_TOKEN"
```

Exit `0` = browser config and/or projects list OK. Exit `2` = Entra disabled on the
host (local-demo remains). Exit `1` = request/auth failure.

Optional follow-ups still outside this smoke:

- JWKS rotation / unknown `kid` fails closed.
- Graph: revoke group membership and confirm the next request denies.
- Task inbox for Entra assignees (local task profile still resolves the synthetic
  local-demo user only).
- Environment switcher / grant feedback UI (F01-T003).

## Local-demo vs Entra

| Path | When | Connect values |
|---|---|---|
| Token paste | Always (default) | org `local-demo`, env `dev`, token from `nexusflow-local token` |
| Microsoft sign-in | Entra env configured | org/env from `/api/v1/auth/browser`; MSAL access token |

Both can coexist: local-demo keeps synthetic JWKS; Entra org uses Microsoft JWKS
routed by directory tenant.

## Spec pointers

- Feature tasks: [features/01-identity-environments/tasks.md](../specs/001-nexus-flow-designer/features/01-identity-environments/tasks.md)
- Evidence: [implementation-entra-live-smoke.md](../specs/001-nexus-flow-designer/implementation-entra-live-smoke.md)
- ADR: [decisions/0003-identity-environments.md](decisions/0003-identity-environments.md)
- Runbook: [pilot-runbook.md](pilot-runbook.md)
