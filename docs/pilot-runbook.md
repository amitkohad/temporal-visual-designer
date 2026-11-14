# Pilot runbook (local-demo Hosted Dev)

Short operator guide for an **internal business-user pilot** on the managed-approval
Dev loop. Default identity is the synthetic `local-demo` bearer token (paste into
Connect). When Entra is configured on the host, **Sign in with Microsoft** is also
available — see [entra-pilot.md](entra-pilot.md). This Hosted Dev loop is **not**
cloud Temporal or production multi-tenant hosting.

## Prerequisites

- Repository checkout
- Python 3.12 or 3.13, [uv](https://docs.astral.sh/uv/), Node 22+ / npm
- [Temporal CLI 1.9.1](https://docs.temporal.io/cli) on `PATH` (or
  `NEXUSFLOW_TEMPORAL_CLI` / `--temporal-cli`)
- Free loopback ports **8000** (API+UI) and **7233** (Temporal), unless you override

## One-time prep

From the repository root:

```sh
uv sync --locked
cd web && npm ci && npm run build && cd ..
uv run nexusflow-local init
uv run nexusflow-local enable-tasks
```

`init` creates `.nexusflow-local/` (Git-ignored). `enable-tasks` is required for the
task inbox / approve-reject path. Re-running either command is safe on an existing
installation; it does not reset projects or restore revoked grants.

## Start the pilot host (API + UI, same origin)

```sh
uv run nexusflow-local serve --ui
```

Startup prints JSON including `url` (open that in the browser) and `"ui":"same-origin"`.
The React app and `/api/...` share `http://127.0.0.1:8000` — no separate Vite process.

Custom build path: `uv run nexusflow-local serve --ui --ui-dir /path/to/dist`.

Stop from another terminal (same `--state-dir` if customized):

```sh
uv run nexusflow-local stop
```

## Token / sign-in

### Path A — local-demo token (default)

While the host is running (or after init):

```sh
uv run nexusflow-local token
```

In the browser Connect form:

| Field | Value |
|---|---|
| Organization | `local-demo` |
| Environment | `dev` |
| Token | paste the command output |

The token stays in memory only for the tab session. Do not put tokens in URLs,
screenshots, or checked-in scripts. Issue a fresh token with `--lifetime-seconds`
(1–86400) if needed.

### Path B — Microsoft Entra (optional)

When `NEXUSFLOW_ENTRA_*` (or `NEXUSFLOW_ENTRA_CONFIG`) is set before `serve`, the
API exposes `GET /api/v1/auth/browser` and Connect shows **Sign in with Microsoft**.
Local-demo token paste remains available. Full enablement, app registration, and
honest live-tenant remaining work: [entra-pilot.md](entra-pilot.md).

## Click-through (Sample → Projects → Release → Tasks)

1. Open the printed `url` (default `http://127.0.0.1:8000`).
2. **Sample** — local in-memory canvas (optional).
3. **Projects** — Connect with the values above → create a starter
   (`employee-onboarding` or `purchase-requisition`) or open a draft → edit supported
   steps → Validate when prompted.
4. **Release** — Publish → Activate → Start a run (or use Release controls embedded
   in Projects when the publish gate is ready).
5. **Tasks** — open inbox for the run → submit the managed form if required →
   Approve or Reject → confirm the run completes on Release/status.

Details for API-only demos remain in [local-development.md](local-development.md)
and [mvp-dev-loop.md](mvp-dev-loop.md). Navigation is Sample / Projects / Release /
Tasks in the React app (`web/src/App.tsx`).

## Backup `.nexusflow-local`

Treat the whole directory as **one recovery unit**:

| Include | Notes |
|---|---|
| `settings.json`, `private-key.pem`, `public-jwks.json` | Installation identity |
| `identity.db`, `projects.db`, `catalog.db`, `intake.db` | Directory / drafts / catalog |
| `activations.db`, `starts.db` | Activation pointer and start intents |
| `human-tasks.json`, `tasks.db`, `task-completions.db` | When tasks are enabled |
| `temporal.db`, `dispatch.db`, `runtime-identity.json` | Runtime continuity |
| Optional debugger / mock fixture files | Only if those features were enabled |

**Do:** stop the launcher, copy the entire directory to private storage, restore the
same tree before the next `serve`, keep the same Temporal port.

**Do not:** delete individual DBs to “reset”; mix files from two installations; commit
the directory to Git; treat this backup as production RPO/RTO.

Unknown or partial state fails closed — preserve files for investigation and start a
**new** `--state-dir` for a clean demo.

## Engineer fallback (two processes)

If you need Vite HMR instead of the built UI:

```sh
uv run nexusflow-local serve
# other terminal:
cd web && npm run dev
```

Open the Vite URL; it proxies same-origin `/api` to port 8000. Prefer `serve --ui`
for business-user pilots.

## Non-claims

- Token paste ≠ Entra / org SSO (unless Path B is configured and live-tenant smoke passes)
- Mocked Entra JWKS/Graph boundary tests ≠ live-tenant production acceptance
- Local Temporal CLI ≠ cloud Temporal
- Loopback Hosted Dev ≠ production reverse proxy, TLS, or multi-tenant isolation
- Environment switcher / grant feedback UI is deferred; Connect still uses explicit org/env fields
