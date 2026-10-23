# Decisions, assumptions and required clarifications

## Confirmed requirements

Nexus Flow Designer is the product; Temporal is the engine. Standalone business-user Designer, task portal and Admin. Shared services first, organization-specific Dev/UAT/Prod. Entra identity. Python authored externally and imported via Admin. Built-in version control. Whole-project exports and project-wide activation. Business users operate Dev/UAT; production support operates Prod. Optional external change number/approval uploads; no in-tool change process. Basic Designer debugging. Configurable connectors and first-release human tasks.

## Proposed defaults requiring review, not invented user commitments

| Decision | Proposed default | Gate |
|---|---|---|
| Entra tenancy | Support explicit directory-to-organization bindings; design for customer-owned directories | Confirm single vs multiple directories before production identity adapter |
| Approval rules | Any-one, all, sequential and conditional; quorum later | Confirm quorum before final rule schema freeze |
| Group membership | Snapshot assignment, fresh action authorization, audited reassignment | Confirm leaver/access revocation expectations |
| Cross-organization packages | Reject initially | Explicit product policy before enabling transfer |
| Revision model | Linear append-only revisions with optimistic concurrency | Collaborative branching/merging outside initial release |
| Activity trust | Destination publisher approval required for new executable digest | Business project import cannot grant code publication |
| Connectors | Generic HTTP, email, external notification first | Named ERP/CRM connectors need separate requirements |
| Debug scope | Step-over ready invocation, inspect, continue/cancel; no variable edits | Advanced stepping is later |

## Technical research to complete before implementation

1. Select API/browser/runtime stack, Temporal SDK versions, graph/form libraries and expression engine after reviewing licensing, accessibility, security and repository dependency policy.
2. Choose Python bundle build/distribution format and supported Python range; define offline dependency resolution and OS/architecture compatibility.
3. Prove interpreter replay/version coexistence, durable permits and approval outbox recovery with a real Temporal worker.
4. Define canonical package digest encoding, signature/trust policy, size limits and artifact provenance.
5. Specify transaction/outbox routing for activation, schedules, webhooks and start retries.
6. Agree target organizations, workflow rates, wait durations, payload sizes, task volume, RPO/RTO, retention and residency. Do not substitute arbitrary performance targets.
7. Determine Entra group resolution permissions/consent and identity provisioning/offboarding.

## Process reference

GitHub Spec Kit: https://github.com/github/spec-kit (consulted 2026-10-23). Adopt its specification/plan/tasks separation and contract-first traceability. CLI installation and agent integration are not part of this documentation-only request. The project begins with authored specification artifacts and a project constitution. Implementation and toolchain setup will follow specification approval.

## Implementation decisions — 2026-10-23

User-selected stack: Python backend/contracts/Temporal workers and React browser UI. Foundation tooling is uv, Pydantic 2, JSON Schema 2020-12, jsonschema, pytest, Ruff, mypy and Hatchling; resolved versions are in uv.lock. See [ADR 0001](../../docs/decisions/0001-foundation.md). C01/C02/C03/C06 foundation shapes are implemented; the broader contract set and adapter-specific decisions remain open. Scope includes isolated checks and composition boundaries, not UI or real orchestration.

Runtime proof uses Temporal Python SDK 1.34.0, immutable proof.v1 plans and sandbox/replay tests. See [ADR 0002](../../docs/decisions/0002-runtime-proof.md). Production interpreter and adapter decisions remain separate.

Identity decisions: explicit per-organization directory binding, allowlisted user/workload clients, fresh injected group resolution, scoped server grants and local SQLite provider. No live tenant or production database topology is assumed. See [ADR 0003](../../docs/decisions/0003-identity-environments.md).

Authoring validation decisions: offline descriptor-backed checks, structured joins, project-local reusable references and versioned variable bindings with conservative schema/availability proof. The proof runtime rejects bindings pending execution support. General expression and form contracts remain open. See [ADR 0005](../../docs/decisions/0005-authoring-validation.md).

Activity intake decision: ZIP with manifest 0.1 and preassembled pure-Python source/vendored dependencies; offline validation, bounded in-memory staging and atomic local SQLite storage. See [ADR 0007](../../docs/decisions/0007-activity-bundle-intake.md). Production build/provenance, worker sandbox/resource policy and full capability checks remain explicit future gates.

Catalog decision: immutable scoped snapshots, mandatory publication capability provider, monotonic generation-checked lifecycle, internal usage holds and permanent version reservations. Palette queries read metadata only and paginate in SQL. See [ADR 0008](../../docs/decisions/0008-activity-catalog.md). Preassembled intake has no build runner; catalog acceptance uses capability fakes. Actual sandbox/worker dispatch and release/run retention orchestration remain separate gates.
