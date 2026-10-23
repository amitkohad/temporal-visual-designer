# Feature 04: Activity catalog and Python bundle publishing

## Outcome and scope

Developers author Python activities outside Nexus Flow Designer. Authorized administrators import activity bundles through Admin, validate them, and publish approved versions to the Designer palette. This feature owns artifact intake, catalog metadata and isolated custom Python execution workers. Feature 08 owns connector implementations and connection resolution; feature 05 owns orchestration dispatch. No application code is created by this specification.

## Requirements

- **F04-FR01:** Import a versioned Python bundle containing manifest, executable artifact, pinned dependency information, input/output schemas, configuration UI schema, connection requirements, documentation, and runtime compatibility metadata.
- **F04-FR02:** Validate archive paths, expanded size/file-count limits, manifest/schema versions, digests, dependency completeness, and compatible runtime before a bundle becomes publishable. Reject traversal, unexpected executable installation hooks, mutable dependency aliases, and embedded credentials.
- **F04-FR03:** Never import or execute user Python in the Admin/API process during validation. Delegate any optional executable compatibility check to an isolated validation service through a port.
- **F04-FR04:** Separate import, validation, publish, deprecate, and withdraw states. Only authorized activity publishers may make a validated bundle visible; workflow package import permission alone is insufficient.
- **F04-FR05:** Publish immutable activity versions keyed by organization, activity identity, version, and digest. Reject replacement of an existing version by different content; retries of identical intake return the existing artifact.
- **F04-FR06:** Expose searchable, categorized descriptors to the Designer only for permitted organizations/environments. Descriptors include configuration schemas, typed inputs/outputs, documentation, exact version, and availability.
- **F04-FR07:** Pin project dependencies to immutable versions. Deprecation hides a version from new selection by default but preserves existing references; withdrawal blocks new deployment under policy and does not remove artifacts required by running executions.
- **F04-FR08:** Retain an auditable artifact closure that whole-project export can include and target import can validate without the original Dev registry. Secrets and environment bindings are excluded.
- **F04-FR09:** Supply a common descriptor contract for built-in, connector, and custom activities, with language-neutral invocation metadata for future languages. Only Python custom execution is required initially.
- **F04-FR10:** Execute approved pinned Python artifacts in isolated worker processes/containers with resource, network and credential limits. Resolve locked dependencies before execution; reject incompatible artifacts. Support cancellation, timeout, schema validation, output redaction and stable invocation identity without running user code in platform processes.

## Data and interfaces

Use [shared contracts](../../contracts.md). ActivityManifest contains identity/version, artifact digest, Python/runtime compatibility, pinned dependencies, schema versions, connection declarations, and descriptor metadata. CatalogEntry tracks validation/publishing status, scope, author, and timestamps. ArtifactStore, BundleValidator, CompatibilityProbe, Authorization, Audit, and UsageReferences are replaceable ports. Catalog read/resolve and artifact export APIs do not invoke activities. This feature publishes worker capability/availability through a port, validated again during deployment.

## Acceptance scenarios

- **F04-A01 (positive):** Given a valid externally authored bundle, when an authorized admin imports, validates, and publishes it, then the permitted Designer palette exposes its exact version and schemas.
- **F04-A02 (negative):** Given traversal, excessive expansion, digest mismatch, missing dependency, unsupported runtime, or invalid schemas, when imported, then validation fails with a reason and no publishable artifact appears.
- **F04-A03 (security):** Given a user permitted to import projects but not publish activities, when publishing Python code or reading another organization's artifact, then access is denied and no content leaks.
- **F04-A04 (recovery):** Given an interrupted upload/validation or a lost successful response, when retried, then no partial bundle is visible and identical content yields one immutable catalog version.
- **F04-A05 (negative):** Given a published activity version, when different content uses that same version, then replacement is rejected and existing project references resolve unchanged.
- **F04-A06 (positive):** Given a deprecated activity used by an existing project, when catalog search and package export occur, then default search omits it but the pinned artifact remains resolvable/exportable.
- **F04-A07 (isolation):** Given a bundle with malicious Python module-level side effects, when metadata validation runs, then no user code executes in the platform process.
- **F04-A08 (execution):** Given a pinned approved Python bundle and isolated worker, when invoked, then typed output is returned; forbidden network/file access, resource exhaustion, timeout and invalid output are bounded failures. A retry preserves operation identity and no credential appears in output/errors. Run with a local sandbox harness, then separately integrate real Temporal dispatch.

## Success criteria and exclusions

Catalog scenarios run with fake storage, identity and compatibility ports; no cloud is required. F04-A08 additionally uses a local isolated Python worker harness. Archive attack fixtures produce no files outside staging. Project dependency fixtures export all declared artifacts. Connector business behavior, infrastructure provisioning and an in-browser Python editor are excluded.
