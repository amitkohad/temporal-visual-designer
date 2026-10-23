# Implementation plan

Status: Python foundation implementation authorized on 2026-10-23; see implementation-foundation.md for scope and verification. Remaining features are planned. Cloud provisioning is outside this implementation increment.

## Technical approach

Build the standalone application in https://github.com/amitkohad/temporal-visual-designer, using the Temporal server as an external engine. Proposed application root: `nexusflow/`. Each feature owns a vertical module with its API/domain/UI adapters and isolated tests; app composition imports those modules. This avoids every UI agent editing one router or every backend agent editing one service bootstrap. Start with a modular platform, dedicated Temporal runtime workers and isolated Python activity workers; do not introduce a separate deployable service per module without evidence.

Use a versioned definition interpreter with durable commands and Activities for side effects. Prototype compatibility and recovery before committing to the full runtime. Persistence holds project revisions, release pointers, tasks and outboxes; Temporal is orchestration authority. Execution read models are rebuildable projections. Artifact storage holds immutable content; secrets stay in an environment-local secret provider. Select actual libraries/toolchain in NF-T002; this plan does not approve unreviewed dependencies.

Shared control services initially host multiple organizations; scoped namespaces and worker routing isolate environments. Capacity limits and scalable workers address interference. Dedicated placement is supported through interfaces, not an automatic migration promise. Azure deployment requirements will be planned for this application while retaining portable provider boundaries.

## Candidate stack decision gates

- Browser: evaluate a typed web application and graph/form libraries against accessible keyboard editing, schema-driven properties and licensing. Record package choices before code.
- Backend: choose a platform API language after evaluating repository conventions, Temporal SDK support, team operations and activity integration. Python activity support does not mandate all services use Python.
- Runtime: evaluate supported Temporal SDK/version and interpreter replay/versioning mechanics with fixtures. Pin SDK/runtime compatibility.
- Persistence: evaluate transactional relational storage for activation/task/outbox invariants; object storage for artifacts/attachments. Record indexes, retention and migrations in feature design.
- Identity: Entra protocol and directory integration need real token/overage tests later; no live credentials needed for isolated suites.

## Delivery gates

1. **Contract gate:** root approves C01–C10, decisions, fixture formats, ownership and requirement mapping. Resolve directory-tenant mode and build strategy before implementing their dependent adapters.
2. **Feasibility gate:** runtime proof demonstrates replay, restart, approval delivery, durable pause and concurrent release compatibility. Compare interpreter vs generated code if proof fails; do not disguise uncertainty with a full UI implementation.
3. **Parallel feature gate:** implement isolated features against approved fixtures. Every feature runs without unrelated services; fakes are checked by provider conformance tests.
4. **Integration gate:** compose modules under a single integration owner, run actual-provider tests and both reference processes. No mock-only production claim.
5. **Release readiness gate:** agree numeric capacity/recovery objectives; test noisy neighbors, backup/restore, old/new runtime coexistence, security boundaries and artifact retention.

## Feature dependency graph

All features depend on approved contracts; isolated work can use fakes. Real-provider integration dependencies:

| Feature | Provider dependencies |
|---|---|
| 01 Identity/environments | None |
| 02 Projects/versioning | 01 |
| 03 Designer/forms | 01, 02, 04 |
| 04 Activity catalog | 01 |
| 05 Runtime | 01, 02, 04 |
| 06 Human tasks | 01, 03 form contract, 05 |
| 07 Debugger | 01, 03, 05 |
| 08 Connectors | 01, 04, 05 |
| 09 Promotion | 01, 02, 04, 05, 08 |
| 10 Operations | 01 and actual modules for system tests |

These are integration dependencies, not permission to make independent feature tests require all providers. Feature-local dependency tables must distinguish contract/fake work from integration.

## Proposed ownership

Each agent owns only `nexusflow/modules/<feature-slug>/` and its associated feature tests/fixtures as stated in local plans. Contract owner owns `nexusflow/contracts/`; integration owner owns app composition, root manifests/lockfiles, shared migrations, CI and cross-feature tests. No parallel edits to those shared files. Module migrations are namespaced proposals; a single migration owner orders and integrates them.

Feature-local tasks describe future paths. Before delegation, integrator freezes a path allocation manifest; resolve any overlapping paths before starting. One agent may own multiple sequential tasks, but cannot execute a dependent task until its predecessor is accepted.

## Verification

Run isolated domain/API/UI suites per feature, common contract conformance, negative scope/permission tests and deterministic failure injection. Runtime uses a Temporal test environment plus real-worker restart/replay integration. Connector tests use local endpoints; external provider smoke tests are explicit integration jobs. Frontend accessibility and keyboard operation are acceptance behaviors.

Future implementation must establish and follow an application testing guide. Application test and integration commands will be selected during stack setup; run the application lint command selected during stack setup after repository code changes. Application-specific commands are selected and documented after stack decisions; this documentation change does not invent runnable test commands.

## Figma design gate

Use Figma MCP for editable designs, component/library inspection, screenshot review and implementation handoff. Follow UX-T001–006 in [ux-design.md](ux-design.md). The design lead owns shared tokens/components; separate screen owners can design concurrently after foundation freeze. UI implementation requires UX-T006 in addition to existing feature prerequisites. Backend/contracts are not blocked by screen approval when separately authorized. The selected graph/form libraries must support the approved interactions and accessibility criteria; visual convenience cannot bypass semantic/runtime contracts.

## Foundation implementation update

The user authorized implementation and selected Python backend/runtime and React browser UI on 2026-10-23. ADR 0001 records the executable-contract subset and tooling. Browser UI remains subject to its Figma gates; this foundation introduces no UI code. Scope-specific foundations may progress without falsely closing production identity, runtime, retention or sizing decisions. Actual module imports use snake_case names per nexusflow/modules/README.md.
