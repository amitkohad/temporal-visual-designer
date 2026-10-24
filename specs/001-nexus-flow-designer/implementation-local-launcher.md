# Local bootstrap and launcher increment

Branch: `codex/015-local-launcher`, based on merged PR #12 (`6d07986`). Decision: [ADR 0013](../../docs/decisions/0013-local-launcher.md). User guide: [local-development.md](../../docs/local-development.md).

## Implemented scope

- `apps/local_bootstrap.py`: explicit synthetic local-demo initialization, strict marker/key consistency, scoped real identity/SQLite composition, actual welcome project publication/activation, bounded token issuance and safe reinitialization.
- `apps/local_runtime.py`: owned loopback disk-backed Temporal server, registry namespace verification, permanent runtime destination marker, joint history/journal checks, actual proof worker/gateway and cleanup.
- `apps/local.py`: installed `nexusflow-local` init/token/serve/stop commands, exclusive installation lock, loopback API socket, sanitized output and graceful stop marker.
- `tests/local/`: independently isolated bootstrap/runtime/CLI tests; no server processes in the default suite.
- `tests/runtime/test_local_launcher_integration.py`: independent real-socket/subprocess init/token/serve/stop/restart acceptance with actual Temporal server/worker and persistent API project/activation/execution state.
- Repository instructions record the user's standing requirement for parallel sub-agents with disjoint ownership and centralized dependency/environment updates. `.gitignore` excludes default local state.

## Parallel implementation evidence

The coordinator froze module interfaces before delegating three disjoint lanes: bootstrap plus own tests, runtime plus own tests, and independent real-socket acceptance plus final read-only review. The coordinator owned CLI/locking, manifests, documentation, task reconciliation and final suite integration. No application code imports fixtures. A dependency synchronization collision during concurrent agent checks was resolved by using the existing interpreter/no-sync checks in agent lanes and one coordinator-owned synchronization; this coordination rule is now in AGENTS.md.

## Acceptance and remaining work

NF-T015c and RT-T03f/T04f are complete at the local-demo launcher boundary. Real production providers are not inferred from demo signing/configuration: production API construction remains explicit, live Entra/TLS/cloud portability/HA and custom activity execution remain open. React UI and design-handoff gates remain unchanged. Init is marker-last, preserves edited state and revoked grants, and never enables UAT/Prod. Startup and test retention/recovery limitations are documented in ADR 0013.

The local API can now be launched from repository commands without supplying a custom host module. Browser testing still requires the React increments. Running sample workflows use only safe interpreter builtins; the local activity catalog initially remains empty.

## Verification

Verified locally on Python 3.12.2:

- `uv run pytest nexusflow/tests/local -q`: 66 passed, 1 Linux-only regression skipped on Windows.
- `uv run pytest -q`: 621 passed, 11 integration tests deselected.
- `uv run pytest -m integration nexusflow/tests/runtime -q`: 11 passed, including independent real socket/subprocess restart acceptance using installed Temporal CLI 1.9.1.
- Ruff lint/format and strict mypy passed (61 source files).
- `uv run nexusflow-local --help`: installed CLI exposes init/token/serve/stop.
- `uv build`: source distribution and wheel built successfully.
- `git diff --check`: passed.

PR review identified a Linux graceful-restart failure caused by the runtime port probe rejecting TCP TIME_WAIT. The probe now matches the owned POSIX listener with SO_REUSEADDR while retaining active-listener rejection. A Linux socket regression proves the old bind fails before exercising the corrected probe. Hosted CI must pass on the updated PR head before merge. Independent final review found no blockers for the documented local-demo scope; production ACLs, live identity, history retention and partial-state recovery limitations remain explicit in ADR 0013.
