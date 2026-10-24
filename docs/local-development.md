# Run the local API and Temporal worker

This launcher starts the Python API and a real Temporal proof worker on your workstation. The React designer is not implemented yet. The `local-demo` profile explicitly generates synthetic local credentials; it does not connect to Entra or configure a production tenant.

Prerequisites: Python 3.12 or 3.13, uv, and Temporal CLI 1.9.1. Put `temporal` on PATH, set `NEXUSFLOW_TEMPORAL_CLI` to its executable, or pass `--temporal-cli` to `serve`. The launcher does not download a CLI or adopt an existing Temporal server. See the [official Temporal CLI documentation](https://docs.temporal.io/cli) for installation.

From the repository directory:

```sh
uv sync --locked
uv run nexusflow-local init
uv run nexusflow-local serve
```

The API listens at `http://127.0.0.1:8000`, and the owned headless Temporal server uses loopback port 7233. Both ports must be free, distinct and between 1024 and 65535. Use `--api-port` and `--temporal-port` for a new installation if necessary; keep the Temporal port unchanged on subsequent runs. The API starts only after the Temporal worker and backend have initialized. `/health` is process liveness; a completed start/status check exercises dependencies.

In another terminal, issue a one-hour local bearer token and inspect the seeded project. PowerShell example:

```powershell
$localDemoToken = uv run nexusflow-local token
$localDemoHeaders = @{ Authorization = "Bearer $localDemoToken" }
$localDemoBase = "http://127.0.0.1:8000/api/v1/organizations/local-demo/environments/dev"
Invoke-RestMethod "$localDemoBase/projects" -Headers $localDemoHeaders
Invoke-RestMethod "$localDemoBase/projects/welcome" -Headers $localDemoHeaders
Invoke-RestMethod "$localDemoBase/projects/welcome/activation" -Headers $localDemoHeaders
```

`init` creates and activates project `welcome` with workflow `welcome` (start → one-second durable wait → end). Start it and inspect status:

```powershell
$localDemoBody = @{ workflow_id = "welcome"; idempotency_key = "first-demo-run"; input = @{} } | ConvertTo-Json
$localDemoRun = Invoke-RestMethod "$localDemoBase/projects/welcome/executions" -Method Post -Headers $localDemoHeaders -ContentType "application/json" -Body $localDemoBody
Invoke-RestMethod "$localDemoBase/projects/welcome/executions/$($localDemoRun.execution_id)" -Headers $localDemoHeaders
```

Query again if the workflow is still waiting. Repeating the same start request/key returns the same accepted execution, including after a clean restart. Use a different key to create another execution. Tokens are printed only by `token`; request access logging is disabled by the local host. Keep bearer values out of screenshots or checked-in scripts. A refreshed token can be issued while the launcher is running; lifetime can be set with `--lifetime-seconds` (1–86400).

Stop gracefully from the other terminal:

```sh
uv run nexusflow-local stop
```

`stop` requests shutdown for this initialized local installation. The API exits, repositories close, and the worker/server/journal unwind their owned contexts. Ctrl+C is also supported interactively. Prefer graceful stop to force-killing the process; in-flight workflow state is durable, but force termination is not a clean-shutdown acceptance case. A stop request targets a running launcher, not a service manager or scheduled future launch.

By default all state is under `.nexusflow-local/`, which is ignored by Git. Every command accepts `--state-dir PATH`; use the same directory and ports on restart. A custom state directory should remain private and outside version control.

| File | Purpose |
|---|---|
| `settings.json` | Profile, organization and installation identity |
| `private-key.pem`, `public-jwks.json` | Installation-specific synthetic signing material |
| `identity.db` | Directory binding, local membership, Dev grant and registry placements |
| `projects.db`, `catalog.db`, `intake.db` | Projects/releases and catalog/intake state |
| `activations.db`, `starts.db` | Active project snapshots and start intents |
| `runtime-identity.json` | Exact persistent runtime destination identity |
| `temporal.db`, `dispatch.db` | Real Temporal history and permanent dispatch journal |
| `.launcher.lock` | Exclusive init/serve ownership lock |
| `.stop-request` | Transient installation-bound stop request |

Re-running `init` validates existing state and leaves keys, project edits and grants untouched. It does not reset the example or restore revoked access. Unknown populated directories, incomplete initialization, mismatched signing material, changed runtime destination, occupied ports, or a missing member of the Temporal/journal database pair fail closed. There is no automatic destructive recovery. Preserve partial state for investigation; create a separate new directory for a genuinely new demo.

The whole directory is one recovery unit. Do not independently delete or replace its databases or keys. Permanent dispatch receipts prevent duplicate starts; they do not make completed Temporal history readable forever after its configured retention expires. Backups/recovery automation and production retention policies remain future work.

Local access is Dev-only. Existing repository provisioning also records UAT/Prod placements, but synthetic credentials receive no grants there. Custom Python execution is explicitly unavailable and cannot be enabled by publishing a bundle in this profile. The initial catalog is empty; the welcome workflow uses interpreter builtins. Windows file access follows workstation ACLs; Unix key creation requests mode 0600. This is a trusted-workstation demo, not hardened production key management or network deployment.

Checks:

```sh
uv run pytest nexusflow/tests/local
uv run pytest -m integration nexusflow/tests/runtime
```

Default tests use isolated local fixtures and do not start servers. The marked real-socket test invokes init/token/serve/stop in subprocesses, checks authentication/exclusive ownership, executes a workflow, restarts with the same disk state, and verifies original token/project/activation/execution continuity. It requires an installed Temporal CLI; existing proof tests retain their separate SDK test-server fallback behavior. See [ADR 0013](decisions/0013-local-launcher.md).
