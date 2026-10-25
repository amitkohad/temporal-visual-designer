# Feature 09 tasks

Program gate: NF-T003 and later implementation authorization are required before any task below. Shared contracts take precedence; freeze feature-local contracts before parallel lanes. Integration depends on actual providers as listed in the program plan.

`[P]` tasks run concurrently only after prerequisites and own separate subtrees. These are future implementation tasks.

- [ ] F09-T01 Freeze `nexusflow/modules/09-package-promotion/contracts/` against `specs/001-nexus-flow-designer/contracts.md`; prerequisites: release/registry/runtime/authorization review; acceptance: F09-A01–A06 mapped, evidence optional.
  - [x] F09-T01a Freeze target-local activation/rollback requests, revisions, operation receipts, readiness/configuration and audit contracts (ADR 0011).
  - [x] F09-T01c Freeze optional change-request/notes metadata, legacy request identity and bounded successful-operation history contracts (ADR 0030).
  - [x] F09-T01d Freeze bounded optional evidence-file uploads, immutable attachment identity, authorization, safe binary download and quota contracts (ADR 0031).
  - [x] F09-T01e Freeze portable release ZIP inventory/digests, source closure, limits and non-activating inspection contracts (ADR 0032).
  - [x] F09-T01f Freeze target package staging, logical operation identity, immutable receipts/archive, scoped quotas and metadata history (ADR 0033).
  - [ ] F09-T01b Freeze executable target release/artifact materialization, production provider and remaining cross-environment trust/binding contracts.
- [ ] F09-T02 [P] Build lifecycle and audit in `nexusflow/modules/09-package-promotion/backend/`; prerequisites: F09-T01; acceptance: F09-A01–A06 through fake ports.
  - [x] F09-T02a Implement local SQLite whole-project activation/rollback, fresh authorization, exact compatibility checks, conservative retention, atomic success audit and idempotent operation recovery.
  - [x] F09-T02c Persist optional immutable evidence text in the existing activation transaction and expose scoped metadata-only history/operation lookup with stable pagination and legacy receipt recovery ([evidence](../../implementation-deployment-traceability.md)).
  - [x] F09-T02d Implement local atomic evidence-file storage with scoped operation linkage, per-operation quotas, original-actor retry receipts and verified downloads ([evidence](../../implementation-deployment-attachments.md)).
  - [x] F09-T02e Implement deterministic whole-project export with exact activity bundles, strict ZIP/JSON/integrity checks and side-effect-free package inspection ([evidence](../../implementation-project-package-export.md)).
  - [x] F09-T02f Persist structurally inspected target packages and first archives atomically with scoped revisions/quotas, immutable logical retries and integrity-checked reads ([evidence](../../implementation-project-package-intake.md)).
  - [ ] F09-T02b Implement executable target release/artifact materialization, production artifact providers, target binding persistence and remaining lifecycle/recovery operations.
- [ ] F09-T03 [P] Build Admin screens in `nexusflow/modules/09-package-promotion/ui/`; prerequisites: F09-T01; acceptance: F09-A01/A02/A04/A06 using stubs.
- [ ] F09-T04 [P] Build fixtures and acceptance suites in `nexusflow/modules/09-package-promotion/tests/`; prerequisites: F09-T01; acceptance: F09-A01–A06 including post-commit retry.
  - [x] F09-T04a Verify backend activation, concurrent/duplicate requests, lost-commit-response recovery, rollback incompatibility, no-evidence Prod activation authorization, immutable start binding and source/artifact retention.
  - [x] F09-T04c Verify optional evidence metadata, exact/conflicting retries, legacy serialization, SQL metadata bounds, signed Prod policy, scoped reads and independent HTTP acceptance ([evidence](../../implementation-deployment-traceability.md)).
  - [x] F09-T04d Verify evidence-file scope/revocation, size/quota races, lost-commit recovery, additive local schema, metadata-only listing, opaque download safety and no activation side effects ([evidence](../../implementation-deployment-attachments.md)).
  - [x] F09-T04e Verify portable package roundtrip, whole-resource/source preservation, strict archive/JSON adversarial cases, early budgets, signed export/import permissions and non-activating inspection ([evidence](../../implementation-project-package-export.md)).
  - [x] F09-T04f Verify staging retry/quota races, loss-of-ack recovery, strict scope/permissions, metadata-only pagination, stored integrity and no target release/activation mutation ([evidence](../../implementation-project-package-intake.md)).
  - [ ] F09-T04b Complete full package transfer, evidence attachment, UI and F09-A01–A06 end-to-end acceptance.
- [ ] F09-T05 Assemble real provider ports in `nexusflow/modules/09-package-promotion/integration/`; prerequisites: F09-T02/F09-T03/F09-T04 and registry/runtime/identity providers ready; acceptance: F09-A01–A06 across environments.
  - [x] F09-T05a Compose actual local project/catalog/identity stores and start resolver; verify activation/rollback coexistence and replay against real local Temporal with explicit configuration/capability fakes.
  - [x] F09-T05c Compose authenticated activation/current-state HTTP routes using actual local providers, explicit readiness ports and safe receipt summaries; verify activation through the signed HTTP-to-Temporal start path (ADR 0012).
  - [x] F09-T05d Compose optional evidence and bounded history/detail HTTP routes through actual local identity/SQLite providers, preserving typed OpenAPI and existing no-evidence responses ([evidence](../../implementation-deployment-traceability.md)).
  - [x] F09-T05e Compose signed attachment upload/list/download through actual local providers and verify original deployment receipts and pinned Temporal execution remain unchanged ([evidence](../../implementation-deployment-attachments.md)).
  - [x] F09-T05f Compose release-package download and offline-validation HTTP routes through actual local project/catalog/identity providers; retain immutable release bytes and non-mutating inspection ([evidence](../../implementation-project-package-export.md)).
  - [x] F09-T05g Compose signed staging APIs and additive local persistent storage; verify Dev-to-UAT staging, immutable archives/receipts across real CLI restart and unchanged original execution ([evidence](../../implementation-project-package-intake.md)).
  - [ ] F09-T05b Complete secure package transfer, actual configuration/capability providers, Admin UI and cross-environment acceptance.
- [ ] F09-T06 Record acceptance and applicable lint in `nexusflow/modules/09-package-promotion/integration/verification.md`; prerequisites: F09-T05; acceptance: no mixed activation or secret export, all scenarios pass.

Bounded activation evidence: [implementation record](../../implementation-project-activation.md). The F09-T01a contract subset gates T02a/T04a/T05a only. These local backend subtasks do not require UI completion; full parent tasks remain open. Python implementations live in `nexusflow/modules/package_promotion/` and tests in `nexusflow/tests/package_promotion/`.
