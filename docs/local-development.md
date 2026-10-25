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

## Enable managed approvals

Stop the launcher before enabling this optional feature, then run:

```sh
uv run nexusflow-local enable-tasks
uv run nexusflow-local serve
```

Use the same `--state-dir` on every command if you configured a custom directory. Enabling creates `human-tasks.json`, `tasks.db` and `task-completions.db`, and adds the Dev `local-task-participant` grant for the existing synthetic user. Ordinary `init` does not enable it. Repeating enable leaves grants and data unchanged, including revoked access. Missing enabled stores or partial provisioning fail closed; preserve all files for recovery rather than deleting a marker to reset the feature.

In a second PowerShell terminal, obtain `$localDemoHeaders` and `$localDemoBase` as above. From the repository root, publish and activate the supplied sample project:

```powershell
$localApprovalProject = Invoke-RestMethod "$localDemoBase/projects" -Method Post -Headers $localDemoHeaders -ContentType "application/json" -Body (Get-Content examples/local-approval-project.json -Raw)
$localApprovalPublish = @{ source_revision = $localApprovalProject.revision; idempotency_key = "approval-release-1" } | ConvertTo-Json
$localApprovalRelease = Invoke-RestMethod "$localDemoBase/projects/welcome-approval/releases" -Method Post -Headers $localDemoHeaders -ContentType "application/json" -Body $localApprovalPublish
$localApprovalActivation = @{ project_id = "welcome-approval"; release_id = $localApprovalRelease.release_id; configuration_revision = "local-v1"; operation_id = "approval-activation-1"; expected_revision = 0 } | ConvertTo-Json
Invoke-RestMethod "$localDemoBase/projects/welcome-approval/activation" -Method Post -Headers $localDemoHeaders -ContentType "application/json" -Body $localApprovalActivation
$localApprovalStart = @{ workflow_id = "approval-demo"; idempotency_key = "approval-run-1" } | ConvertTo-Json
$localApprovalRun = Invoke-RestMethod "$localDemoBase/projects/welcome-approval/executions" -Method Post -Headers $localDemoHeaders -ContentType "application/json" -Body $localApprovalStart
$localApprovalInbox = Invoke-RestMethod "$localDemoBase/tasks?status=pending&run_id=$($localApprovalRun.execution_id)" -Headers $localDemoHeaders
$localApprovalInbox.items
```

Query the inbox again if task creation is still in progress. Once the task appears, approve it:

```powershell
$localApprovalTask = $localApprovalInbox.items | Select-Object -First 1
$localApprovalDecision = @{ command_id = "approve-demo-1"; expected_revision = $localApprovalTask.revision; decision = "approve" } | ConvertTo-Json
Invoke-RestMethod "$localDemoBase/tasks/$($localApprovalTask.task_id)/decisions" -Method Post -Headers $localDemoHeaders -ContentType "application/json" -Body $localApprovalDecision
Invoke-RestMethod "$localDemoBase/projects/welcome-approval/executions/$($localApprovalRun.execution_id)" -Headers $localDemoHeaders
```

The background dispatcher resumes the workflow; query status again until it reports `completed`. Repeating the exact decision or start request is retry-safe. Use a new start key for another run; create/publish/initial activation commands above assume the sample project does not already exist. A pending approval survives graceful stop and restart with the same directory and runtime settings.

This profile resolves only synthetic user `22222222-2222-4222-8222-222222222222`; groups and unknown users fail closed. No browser inbox, live Entra adapter or form-binding UI is added. Task forms still require separate explicit providers and bindings. See [ADR 0024](decisions/0024-local-managed-approvals.md).

## Managed approval forms

With `enable-tasks` already configured, the local profile also supplies field access for the synthetic user. Publish the separate [form project](../examples/local-approval-form-project.json) using the same create/release/activation sequence above, substituting project ID `welcome-approval-form`, workflow ID `approval-form-demo` and the new JSON file. Its `human_task.form` contains a full immutable `definition` and literal `initial_values`.

Start that workflow with a new key, then query its inbox as above. The task first appears at revision 2 because creation and form binding commit together. It cannot be approved until the form is submitted successfully. After setting `$localFormTask` to the returned task, preview and submit it:

```powershell
$localFormPreview = Invoke-RestMethod "$localDemoBase/tasks/$($localFormTask.task_id)/form" -Headers $localDemoHeaders
$localFormPreview
$localFormBody = @{ expected_revision = $localFormPreview.revision; values = @{ employee = "Ada" } } | ConvertTo-Json
$localFormSubmitted = Invoke-RestMethod "$localDemoBase/tasks/$($localFormTask.task_id)/form" -Method Post -Headers $localDemoHeaders -ContentType "application/json" -Body $localFormBody
$localFormApproval = @{ command_id = "approve-form-1"; expected_revision = $localFormSubmitted.revision; decision = "approve" } | ConvertTo-Json
Invoke-RestMethod "$localDemoBase/tasks/$($localFormTask.task_id)/decisions" -Method Post -Headers $localDemoHeaders -ContentType "application/json" -Body $localFormApproval
```

The required `employee` field must be nonempty. Initial `department` is `Engineering`; `reference` is read-only, and the initial `token` value is write-only and omitted from previews. Invalid submissions leave the revision unchanged. Forms become read-only after the first vote, and rejection remains available without form submission. Existing runs retain the same form snapshot and submitted values across source edits and launcher restarts. Submissions are stored with the task; returning form values to workflow variables is future work. See [ADR 0025](decisions/0025-managed-approval-forms.md).

## Initialize forms from workflow input

Publish the [input-binding project](../examples/local-approval-input-project.json) using the earlier create/release/activation sequence with project ID `welcome-approval-input`. Its workflow `approval-input-demo` selects `/employee/name` and `/department` from required input properties. Start it with:

```powershell
$localInputStart = @{ workflow_id = "approval-input-demo"; idempotency_key = "input-form-run-1"; input = @{ employee = @{ name = "Ada" }; department = "Engineering" } } | ConvertTo-Json -Depth 10
$localInputRun = Invoke-RestMethod "$localDemoBase/projects/welcome-approval-input/executions" -Method Post -Headers $localDemoHeaders -ContentType "application/json" -Body $localInputStart
Invoke-RestMethod "$localDemoBase/tasks?run_id=$($localInputRun.execution_id)" -Headers $localDemoHeaders
```

The form preview contains the selected values once its task appears. Use the same preview/submission/decision sequence as above. An empty `values` object can explicitly validate already-valid initial values; prepopulation alone does not unlock approval. A new start key with different input creates a separate snapshot. Reusing a key with changed input conflicts. Draft edits and restart do not alter existing task values.

Selectors must occupy an entire initial field, use `schema_version: "0.1"`, `source: "input"` and a strict JSON Pointer to required object properties. Optional fields, array traversal, step outputs and expressions are unsupported. Referenced input types must match the form field; invalid runtime values fail before execution is accepted. Existing field permissions and write-only redaction still apply. General activity mappings and workflow output bindings remain future work. See [ADR 0026](decisions/0026-form-input-bindings.md).

Local access is Dev-only. Existing repository provisioning also records UAT/Prod placements, but synthetic credentials receive no grants there. Custom Python execution is explicitly unavailable and cannot be enabled by publishing a bundle in this profile. The initial catalog is empty; the welcome workflow uses interpreter builtins. Windows file access follows workstation ACLs; Unix key creation requests mode 0600. This is a trusted-workstation demo, not hardened production key management or network deployment.

Checks:

```sh
uv run pytest nexusflow/tests/local
uv run pytest -m integration nexusflow/tests/runtime
```

Default tests use isolated local fixtures and do not start servers. The marked real-socket test invokes init/token/serve/stop in subprocesses, checks authentication/exclusive ownership, executes a workflow, restarts with the same disk state, and verifies original token/project/activation/execution continuity. It requires an installed Temporal CLI; existing proof tests retain their separate SDK test-server fallback behavior. See [ADR 0013](decisions/0013-local-launcher.md).

## Project revision history

Using the signed headers and scoped API base from the walkthrough above, request `GET /projects/{project_id}/history?limit=10`. Pass the returned `next_before_revision` as `before_revision` for the next page. Compare using `GET /projects/{project_id}/compare?before_revision=1&after_revision=2`; add `include_layout=true` to include canvas positions.

To restore in Dev, send `POST /projects/{project_id}/restore` with `{"source_revision":1,"expected_revision":2,"summary":"Restore reviewed draft"}`. Substitute the current head for expected_revision. The response is a new revision; publication and activation remain explicit separate operations. A 409 means reload the head, including after an ambiguous retry. These operations are available through the API; React controls are not implemented yet.
