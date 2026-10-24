# Project-wide activation increment

Branch: `codex/013-project-activation`, based on `main` at `15a1e3e`. Decision: [ADR 0011](../../docs/decisions/0011-project-activation.md).

## Implemented scope

- `nexusflow/modules/package_promotion/types.py`: strict activation/rollback request, immutable activation/audit records and mandatory destination readiness/configuration ports.
- `repository.py`: target-scoped SQLite project pointer, optimistic revision checks, atomic receipt/success audit, retained snapshots and sanitized rejection audit.
- `service.py`: freshly authorized activation/rollback, operation retries, source integrity verification, conservative release/activity retention, current state/audit reads and actual active-release resolver for start services.
- `readiness.py`: proof interpreter structural compatibility, original input-schema restrictions, exact catalog/worker-capability validation, immutable configuration validation port and artifact holds.
- `authorization.py` and identity policy: request-bound `run.start` authorization for Dev/UAT authors and production support; business authors cannot deploy/start in Prod.
- `tests/package_promotion/`: real project/catalog/activation persistence with explicit configuration/capability fakes, signed-token identity integration, concurrent expected-revision and duplicate-operation arbitration, post-commit retry, rollback compatibility, pointer read atomicity, retention failure and restart.
- `tests/runtime/test_temporal_integration.py`: actual Temporal run A stays waiting through B activation and rollback; new starts bind B then A; all three complete and replay. This uncovered and fixes the earlier dispatch adapter's use of `run_id` instead of `result_run_id`; its offline success fixture now reflects the SDK.

## Acceptance mapping

F09-T01a/T02a/T04a/T05a and RT-T03d/T04d are verified at the bounded backend/provider boundary. They contribute to F09-A02/A03/A04/A05/A06 and RT-A2/A3/A4/A6; no full package/UI acceptance is claimed. Source release/catalog/identity and active-release resolution use real local providers; immutable destination configuration and worker capability discovery remain required fakes in acceptance, with no permissive production defaults.

## Remaining scope

Whole-project archive export/import, environment bindings/configuration persistence, actual worker discovery, optional evidence fields/uploads, deployment status HTTP API and React Admin UI remain open. No automatic hold cleanup, cross-provider transaction, production DB migration or HA acceptance is included. Previously activated releases are retained conservatively; losing/failed operations can leave extra holds. See ADR 0011 for authorization/audit and readiness limitations.

## Verification

Verified locally on Python 3.12.2:

- `uv run pytest nexusflow/tests/package_promotion -q`: 24 passed after review corrections.
- `uv run pytest -q`: 529 passed, 9 integration tests deselected after review corrections.
- `uv run pytest -m integration nexusflow/tests/runtime -q`: 9 passed against disposable local Temporal CLI 1.9.1 / Server 1.32.0; 67 nonintegration runtime tests deselected.
- Ruff lint/format checks passed; strict mypy passed for 54 source files.
- `uv build`: source distribution and wheel built successfully.
- `git diff --check`: passed.

Hosted CI must pass on the reviewed commit before merge. The real server test uses synthetic destination configuration/capability and authorization fixtures; separate signed-token tests cover actual local identity policy. No live Azure/Entra or production readiness result is inferred.

PR review corrections: duplicate activations now recheck the immutable operation receipt if a commit races with the initial revision read. Activation also checks the original definition size, including its input schema, against the same budget used by runtime preflight. Independent regressions cover both cases. Updated-head hosted CI is required before merge.
