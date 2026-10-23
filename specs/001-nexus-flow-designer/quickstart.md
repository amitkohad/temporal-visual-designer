# Specification review and future verification

## Review now

Read README → spec → contracts → plan → feature documents → tasks. Confirm research defaults and capability boundaries. Review each feature's Given/When/Then scenarios independently. Check every `[P]` task has completed prerequisites and exclusive paths. No code or deployment commands should be executed from these plans until implementation is requested.

## Future isolated test protocol

For one feature: load C01–C10 fixtures applicable to it, use deterministic local fakes for providers, run its domain/API/UI acceptance tests, and record requirement IDs. No Azure credentials, real Entra tenant, email delivery or another unfinished module is required. Run provider conformance against the same corpus before cross-feature integration.

## Future integrated verification

1. Provision an isolated test organization with Dev/UAT/Prod test environments and roles.
2. Import/publish a synthetic Python activity through Admin; deny an unauthorized publisher.
3. Build onboarding and purchasing projects with forms and approval rules.
4. Debug Dev snapshots, inspecting redacted step data and pausing across worker restart.
5. Export/import the same whole-project bytes into UAT; bind connections without embedding credentials.
6. Reject business-author Prod import; allow production support with empty optional evidence and record audit.
7. Activate a newer project release while an older run waits; roll back and confirm new starts select the prior release while both old runs finish on their pinned versions.
8. Exercise duplicate commands, task/deadline races, import interruption, conflicting activation, missing artifacts, revoked access and cross-tenant downloads.

Application test commands and tool versions will be recorded after stack selection. Select application test commands and integration-suite conventions after the stack decision. Run the application lint command selected during stack setup after future code changes. This specification-only change requires document/link/task consistency checks, not server tests or cloud access.

## Implemented foundation commands

The foundation is now implemented; use [development.md](../../docs/development.md) for uv sync, lint, format, type checks, pytest, schema export and build. [implementation-foundation.md](implementation-foundation.md) records verified scope. Future feature scenarios above are not yet implemented. Python is the backend/runtime stack; React is the browser UI stack.
