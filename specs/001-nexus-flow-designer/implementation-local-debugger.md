# Opt-in local debugger composition increment

Branch: `codex/039-local-debugger`, based on main `41a279f` (merged PR #36).

## Implemented boundary

The standalone local launcher now supplies the existing debugger session providers after explicit `enable-debugger`. A bounded strict `debugger.json` profile binds installation identity, organization/Dev and fixed `debug-sessions.db`. Enablement runs under the existing exclusive installation lock, initializes the session store before profile publication and changes no grants, keys, projects or activation. Exact enable retries preserve original state; disabled installations gain no profile/database.

Read-only profile loads inspect required tables/columns and reject missing, corrupt, orphan or mismatched recovery state before owned runtime startup or writable journal/session construction. Orphan WAL/SHM state is also rejected. SQLite read-only WAL inspection can use/create normal sidecars; it does not construct a writable recovery provider or repair state. Startup preserves partial files for explicit recovery.

Enabled bootstrap owns the session repository through ExitStack and builds TemporalDebugRuntime from the existing actual gateway client factory and permanent journal. Generic gateways cannot silently supply debugger behavior. The initializer seed path stays unchanged. Existing loopback placement/server ownership, Temporal workflow commands, normal start fingerprints and existing APIs do not change. Re-enabling never restores revoked access.

See [ADR 0037](../../docs/decisions/0037-local-debugger.md), [runnable walkthrough](../../docs/local-development.md#enable-dev-debugging) and [session APIs](../../docs/designer-debug-sessions.md). Preserve the entire local state directory, including profile/session store/journal/Temporal history, together during recovery. No dependency or role-grant changes.

## Parallel ownership and verification

Frozen interfaces preceded profile/domain and tests; CLI/bootstrap and tests; independent preflight and actual CLI/socket/Temporal acceptance. The coordinator owns documentation/checklists, cross-lane review and full verification.

Profile unit tests: 15 passed. New CLI/bootstrap tests: 10 passed. Full local regression: 127 passed with one expected Windows platform skip. Independent preflight acceptance: 3 passed. New actual CLI/socket integration: 1 passed (23.75 seconds), verifying paused session through full graceful shutdown/restart, unchanged token/session/execution/run IDs, exact command retries, changed-command conflicts, step/continue, actual completion/replay, activation and normal-run isolation, fresh revocation and re-enable preserving revocation. Request payload markers remain absent from logs. Independent review found no blocking issues.

Full default regression: **1,491 passed, one expected Linux TCP TIME_WAIT test skipped on Windows, 46 integration tests deselected** (252.95 seconds). Full actual Temporal runtime suite: **46 passed, 198 deselected** (260.67 seconds). Ruff lint and formatting pass across 355 Python files; strict mypy passes across 89 source files. Source distribution and wheel build successfully; `git diff --check` passes. Independent review found no blocking findings.

Hosted-review correction: the initial push-triggered Temporal job exposed admission-budget exhaustion from 10 Hz authenticated test polling (503 at the final revocation check), while the parallel PR job passed. The test now observes the exact pinned run through bounded SDK queries and makes one HTTP status assertion per observed state. Application limits, source implementation and the required revocation 403 assertion are unchanged. The focused corrected CLI/socket regression passed in **24.18 seconds**; Ruff/format checks pass. Hosted full checks are tracked in the associated PR.

## Remaining scope

DB-T01c/T04c/T05b, RT-T03m, NF-T011c and NF-T015e are checked for local composition only; full parent tasks remain open. React controls, variable inspection/redaction, cancellation/expiry, events, mock connections, parallel stepping and production identity/provider/operations acceptance remain outstanding. Timer-based local acceptance does not establish every managed-approval or custom-activity debugging scenario. No browser, live Entra or production debugging acceptance is claimed.
