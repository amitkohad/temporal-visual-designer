# Local mock fixture administration increment

Branch: `codex/061-local-mock-fixtures`, based on main `69aede0` (PR #58).

## Frozen local host interface and parallel plan

`nexusflow.apps.local_mocks` provides `LocalMockFixtureProfile`,
`load_mock_fixture_profile(directory: Path, settings: LocalSettings)` returning an
optional profile, and `enable_mock_fixtures(directory: Path)` returning a profile.
The caller holds the installation lock for enablement. The loader is read-only.

The strict profile has schema version `0.1`, profile name
`local-demo-http-mock-fixtures`, canonical installation ID, exact installed Dev
scope, and `fixtures_database` fixed to `http-mock-fixtures.db`. Marker filename is
`http-mock-fixtures.json`. Enablement creates the fixed synthetic user grant
`local-mock-fixture-admin` with role `organization_administrator` in Dev, using
expected revision zero. It never restores membership, author grants or revoked
administrator grants. Existing valid enablement is idempotent and does not write.

CLI command `enable-mock-fixtures` uses the existing installation lock and prints
profile/organization/environment, `mock_fixtures: enabled`, and
`administrator_role: organization_administrator`, without tokens or fixture data.
The backend opens the fixed SQLite store and enables only the installed Dev scope
when its profile exists. Initialization remains disabled. Serve validates the
profile before starting runtime/network resources. No mock dispatch activity,
executable built-in publication or live HTTP capability is added.

1. [P] Profile lane owns strict marker/storage recovery checks and enablement tests.
2. [P] Host lane owns CLI/preflight/backend lifecycle and isolated host tests.
3. [P] Acceptance lane owns actual CLI/socket/Temporal launcher restart and fresh
   authorization checks. Fixture operations remain administration, not workflow
   mock execution.
4. Coordinator owns shared docs, spec checkbox evidence and regression checks.

## Verification

Focused final-source verification: **33 profile tests passed** (22.13 seconds),
with four real-symlink cases skipped because this Windows host does not permit
unprivileged symlink creation. **15 CLI/backend tests passed** (25.81 seconds).
These 48 passed cases cover strict/bounded markers, orphan/corrupt/schema and WAL
checks, partial initialization, fixed grant tombstones, explicit enablement,
disabled defaults, resource closure and administrator/author/membership revocation.
The skipped real-symlink acceptance remains explicitly unchecked.

Three actual subprocess/socket/Temporal launcher acceptance scenarios passed
(74.57 seconds), including full restart, fixture metadata/cursor persistence,
ordinary welcome workflow completion/replay, signing/project state retention,
installation-lock exclusion and fail-before-runtime corrupt-state handling.
That initial run overlapped the loader WAL refinement. The full final-source
real Temporal runtime suite subsequently passed **84 tests**, with 236 default
runtime tests deselected (615.52 seconds), including the three new scenarios.

Independent review passed for final profile/host composition and WAL-aware
schema validation. Ruff lint/format, configured mypy (128 source files), lock
verification and source/wheel build passed. Wheel parity passed for all three
final local fixture modules. Full final-source default regression passed
**3,634 tests**, with five skips (four host-permission symlink cases and one
Windows TCP TIME_WAIT exclusion) and 84 integration tests deselected (966.52
seconds). Final collection confirms 37 profile cases (33 pass plus four
permission skips) and 15 CLI/backend cases. No dependencies,
workflow serialization, worker activity registration or default-on profile
behavior changed.

## Remaining scope

This synthetic local profile is not Entra/cloud/production administration. Browser
screens, fixture packages/history/delete, live HTTP, persistent-fixture workflow
dispatch and executable activity publication remain open.
