# Feature 01 backend implementation

Date: 2026-10-23. Branch: codex/005-identity-environments.

## Main verification and branch origin

Fetched origin. Local main and origin/main both resolved to c19d096e384d71ba32243dad65b58d3ec071ba18 with a clean worktree. Runtime-proof commit 6a5a9c8 is an ancestor. Before branching, Ruff lint/format, mypy, 82 isolated tests and six real Temporal integration tests passed. The new branch was created from that exact main commit.

## Delivered and parallel work

Frozen feature interfaces in identity_environments/types.py enabled three disjoint sub-agent lanes: Entra token validation and signed fixtures; persistent SQLite metadata and transaction tests; independent role-policy acceptance. Root implemented authorization policy, token-to-context composition, rate admission and end-to-end tests. Shared manifests/type-check configuration were updated by root only. Final integration added UTC/scoped audit details and fixed the independent review's organization-enumeration finding.

The resulting core validates signed Entra access tokens, resolves current group membership via a port, derives permissions from persisted grants, separates workloads, rejects cross-scope access and returns trusted placement. Metadata survives reopen. Concurrent grant updates use CAS; failure during audit rolls back the corresponding metadata/environment transaction. Group/local membership and role revocation apply on the next request.

## Acceptance mapping

| Scenario | Implemented evidence |
|---|---|
| F01-A01/A02 | Signed-local token corpus and composed server context |
| F01-A03 | Policy scope/permission cases plus composed same-directory cross-organization denial |
| F01-A04 | Role matrix; token roles cannot inject Prod support rights |
| F01-A05 | Injected overage/fresh-group provider, revocation and unavailable cases |
| F01-A06 | Persistent, atomic, idempotent logical Dev/UAT/Prod routing |
| F01-A07 | Separate workload token/client/role/membership/grant, scoped placement and audit |

120 feature-specific tests are implemented. Final verification results are recorded below. Tests prove the backend interfaces; no actual API/download/stream route or React view exists yet, so resource-level enforcement and UI acceptance remain open.

## Remaining feature scope

Live Microsoft JWKS/Graph adapters, Entra registration/consent, React sign-in/environment/admin screens, secured HTTP administration, project/task resource policies, production shared persistence/migrations, distributed admission, tamper-evident audit storage and hosted deployment remain pending. F01-T001/backend core and backend acceptance are implemented; the entire feature and real-provider integration are not marked complete. No live-tenant validation or Azure/Temporal resource provisioning occurred.

Technical decisions and public boundaries: [ADR 0003](../../docs/decisions/0003-identity-environments.md).

## Final verification

- Python 3.12.2: 202 isolated tests passed, including all 120 identity tests.
- Python 3.13.3: 202 isolated tests passed; six real Temporal regression tests passed.
- Ruff lint/format and strict mypy passed (16 production source files).
- JSON Schema export and source/wheel builds passed. Wheel inspection confirmed identity core is included and test helpers/fakes are excluded.
- Documentation link checks and git diff --check passed.
- Main remains at c19d096. No remote publish, live Entra consent, cloud provisioning or React implementation occurred in this increment. Hosted CI is pending publication.

The independent review found that malformed tokens produced different errors for registered and unknown organizations. IdentityService now normalizes these authentication failures in both authorize and placement, with two regression cases. The review found no other actionable issues within this backend scope; it does not constitute a production security audit.
