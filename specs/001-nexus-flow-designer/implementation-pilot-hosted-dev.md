# Pilot Hosted Dev composition (Phase 2 P0)

Branch: `feature/pilot-hosted-dev-cab2`. Scope: same-origin local-demo packaging so
business users are not stuck on a separate Vite terminal as the product story.
Entra, environment switcher, promote/Admin and form builder remain out of scope.

## Delivered

- `nexusflow/apps/local_ui.py`: resolve/require `web/dist`, mount `/assets` and SPA
  index onto the existing FastAPI app after API routes (path-escape safe).
- `nexusflow/apps/local.py`: `serve --ui` / `--ui-dir`; startup JSON includes
  `"ui":"same-origin"`; missing builds fail closed before Temporal starts.
- Docs: [pilot-runbook.md](../../docs/pilot-runbook.md), Hosted Dev section in
  [local-development.md](../../docs/local-development.md).
- Critical-path tests: `nexusflow/tests/local/test_local_ui.py` (mount, API
  precedence, CLI `--ui` / `--ui-dir` guards). No Temporal socket suite added.

## Verification

```sh
uv run pytest nexusflow/tests/local/test_local_ui.py nexusflow/tests/local/test_cli.py
```

## Explicit non-claims

Token paste ≠ Entra. Loopback `serve --ui` ≠ production reverse proxy / TLS /
multi-tenant isolation. These tests do not establish browser e2e against a live
Temporal pilot host.
