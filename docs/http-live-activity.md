# Live HTTP JSON activity (secrets + worker registration)

Opt-in Dev path for running the typed HTTP JSON activity in **live** mode with
opaque secret references. See [ADR 0065](decisions/0065-http-secret-resolution.md)
and [ADR 0066](decisions/0066-temporal-live-http.md).

## Local-demo enablement

```sh
uv run nexusflow-local init
uv run nexusflow-local enable-live-http
uv run nexusflow-local put-secret --reference my-api-token --bearer-token "$TOKEN"
# Register an HTTPS connection revision that sets secret_reference=my-api-token
# (Connections UI or signed PUT /connections/... APIs).
uv run nexusflow-local serve --ui
```

`enable-live-http` creates a Dev-only profile and empty `http-secrets.json`.
`serve` registers `HTTPJSONActivities.dispatch` as `nexusflow.proof.dispatch` on
the owned Temporal worker. Without the profile, proof HTTP dispatch stays off.

Mock fixtures remain separate (`enable-mock-fixtures`). When both are enabled,
the live worker factory can attach the mock port as well so one dispatch owner
accepts `execution_mode` live or mock.

## Secrets boundary

- Connections store **opaque** `secret_reference` labels only.
- Local-demo stores Bearer tokens in `http-secrets.json` (not Key Vault).
- Resolution happens in the worker after `worker.execute` authorization and
  before DNS; rechecked at post-DNS / pre-write.
- Activity output remains status-only; Authorization never enters Temporal
  history as a workflow result field.

## Non-claims

local-demo secrets and controlled/loopback TLS acceptance are **not** production
provider, Entra, public egress, built-in catalog publication, Designer palette,
or F04-T007 custom Python sandbox acceptance.
