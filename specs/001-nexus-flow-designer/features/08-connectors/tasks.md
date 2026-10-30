# Feature 08 tasks

Program gate: NF-T003 and later implementation authorization are required before any task below. Shared contracts take precedence; freeze feature-local contracts before parallel lanes. Integration depends on actual providers as listed in the program plan.

`[P]` means concurrently executable only after listed prerequisites, with disjoint ownership. Bounded implemented subtasks are checked below; full connector execution and UI acceptance remain open.

- [ ] F08-T01 Freeze `nexusflow/modules/08-connectors/contracts/` schemas against `specs/001-nexus-flow-designer/contracts.md`; prerequisites: shared contract review; acceptance: F08-A01–A05 mapped to fields/errors.
  - [x] F08-T01b Freeze internal worker destination request/approval, injected IPv4 resolver, authorization, redirect and safe error contracts (ADR 0050). Actual invocation/delivery contracts remain open.
  - [x] F08-T01a Freeze the HTTP metadata-only connection/revision, scoped repository and public privacy contracts (ADR 0049). Invocation, secret and transport contracts remain open.
- [ ] F08-T02 [P] Implement provider adapters and dispatch boundaries in `nexusflow/modules/08-connectors/backend/`; prerequisites: F08-T01; acceptance: F08-A01–A05.
  - [x] F08-T02b Implement pinned connection preflight with fresh worker authorization, canonical origin/IPv4 checks, redirect rebinding/budgets and bounded DNS awaits ([evidence](../../implementation-http-destination-preflight.md)). No outbound HTTP adapter or credentials yet.
  - [x] F08-T02a Implement canonical fixed HTTP definitions, immutable SQLite revision storage, scoped quotas/integrity and fresh administrator metadata service ([evidence](../../implementation-http-connections.md)). No outbound adapter or worker dispatch yet.
- [ ] F08-T03 [P] Implement schema forms, connection selection, and validation in `nexusflow/modules/08-connectors/ui/`; prerequisites: F08-T01; acceptance: F08-A01/A02/A04 using fake APIs.
- [ ] F08-T04 [P] Create fake providers, secret sentinels, and scenario suites in `nexusflow/modules/08-connectors/tests/`; prerequisites: F08-T01; acceptance: F08-A01–A05, including retry-after-acceptance and cancellation fixtures.
  - [x] F08-T04b Verify URL/IP boundaries, authority escapes, real-registry scoped identity, authorization ordering, redirect DNS rechecks, deadline/cancellation and payload-free diagnostics using isolated ports ([evidence](../../implementation-http-destination-preflight.md)). Actual transport/effect scenarios remain open.
  - [x] F08-T04a Verify definition rejection/canonicalization, zero DNS/network access, metadata privacy, scoped concurrent claims/recovery and signed permission/error boundaries ([evidence](../../implementation-http-connections.md)). Actual secret/transport scenarios F08-A01–A05 remain open.
- [ ] F08-T05 Wire runtime and Designer ports in `nexusflow/modules/08-connectors/integration/`; prerequisites: F08-T02/F08-T03/F08-T04 and provider ports available; acceptance: F08-A01–A05 against connected modules.
  - [x] F08-T05a Compose explicitly injected registry providers with signed admin revision PUT/GET/history APIs; preserve default host grants/stores and existing endpoint behavior ([evidence](../../implementation-http-connections.md)). Runtime and Designer integration remain open.
- [ ] F08-T06 Record independent acceptance and integration results in `nexusflow/modules/08-connectors/integration/verification.md`; prerequisites: F08-T05; acceptance: all scenarios pass, zero secret leaks, applicable lint passes.
  - [x] F08-T06b Record preflight/policy acceptance and explicit transport/pinning/secret-provider limitations ([evidence](../../implementation-http-destination-preflight.md)). Full connector acceptance remains open.
  - [x] F08-T06a Record isolated registry/provider/privacy evidence and its execution/UI limitations ([evidence](../../implementation-http-connections.md)). Full connector acceptance remains open.
